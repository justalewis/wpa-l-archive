"""Item 5: what the list was about, era by era, without guessing search terms.

One document per thread: the subject and its messages' own words (quoted text,
signatures removed), so the unit is a conversation, not a message. Documents
are embedded with a sentence-transformer, clustered with BERTopic (UMAP +
HDBSCAN, seeded), reduced to a fixed maximum number of topics, and described
by c-TF-IDF terms. Prevalence is the share of each month's threads in a topic.

Everything that shapes the model is in decisions/topics.toml; the run records
that config, the library versions and the seed. Embeddings are cached under
data/reference/topics/ keyed by document hash, so a refit with the same
documents reproduces the same model without re-embedding.

Topic ids from a fit are not stable, so each topic gets a key: the thread key
of its most representative thread. Hand labels in decisions/topic_labels.csv
(key,label,representatives) carry over to a refit when any of a topic's
representative threads was a representative of the labeled topic.

Slow (about an hour on a laptop CPU the first time); skip it with
`python -m wpal.build --skip-stages topics` and run it alone with
`python -m wpal.build --stages topics`.
"""

from __future__ import annotations

import collections
import csv
import hashlib
import json
import time

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib


def run(db, ctx) -> None:
    cfg_path = ctx.decisions / "topics.toml"
    db.executescript("""
        DROP TABLE IF EXISTS topics;
        DROP TABLE IF EXISTS topic_terms;
        DROP TABLE IF EXISTS topic_months;
        DROP TABLE IF EXISTS topic_threads;
        DROP TABLE IF EXISTS topic_run;
        CREATE TABLE topics (topic_key TEXT PRIMARY KEY, topic_id INTEGER, label TEXT, auto_label TEXT,
                             labeled_by TEXT, n_threads INTEGER, peak_year INTEGER);
        CREATE TABLE topic_terms (topic_key TEXT, rank INTEGER, term TEXT, weight REAL);
        CREATE TABLE topic_months (topic_key TEXT, year INTEGER, month INTEGER, n INTEGER, share REAL);
        CREATE INDEX topic_months_k ON topic_months(topic_key);
        CREATE TABLE topic_threads (topic_key TEXT, thread_key TEXT, prob REAL, representative INTEGER);
        CREATE INDEX topic_threads_k ON topic_threads(topic_key, representative);
        CREATE TABLE topic_run (key TEXT PRIMARY KEY, value TEXT);
    """)
    if not cfg_path.exists():
        ctx.note(f"topics: skipped (no {cfg_path})")
        return
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    try:
        import numpy as np
        from bertopic import BERTopic
        from hdbscan import HDBSCAN
        from sentence_transformers import SentenceTransformer
        from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS
        from umap import UMAP
    except ImportError as e:
        ctx.note(f"topics: skipped ({e}; pip install -r requirements-build.txt)")
        return

    docs, keys, when = [], [], []
    texts = collections.defaultdict(list)
    meta = {}
    for tid, key, subj, year, month in db.execute(
            "SELECT id, root_uid, subject, year, month FROM threads"):
        meta[tid] = (key, subj, year, month)
    for tid, fresh in db.execute("SELECT thread_id, body_fresh FROM messages ORDER BY thread_id, sent_at"):
        if fresh:
            texts[tid].append(fresh)
    for tid, parts in texts.items():
        key, subj, year, month = meta[tid]
        body = " ".join(" ".join(parts).split())[:cfg["max_doc_chars"]]
        if len(body) < cfg["min_doc_chars"]:
            continue
        docs.append(f"{subj}. {body}")
        keys.append(key)
        when.append((year, month))

    if len(docs) < 500:  # a test build, or a partial one: too thin to model
        ctx.note(f"topics: skipped ({len(docs)} thread documents; at least 500 needed)")
        return

    cache_dir = ctx.reference / "topics"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((cfg["embedding_model"] + "\x00" + "\x00".join(docs)).encode()).hexdigest()[:16]
    cache = cache_dir / f"embeddings-{digest}.npy"
    t0 = time.time()
    if cache.exists():
        emb = np.load(cache)
    else:
        ctx.note(f"topics: embedding {len(docs):,} thread documents with {cfg['embedding_model']} ...")
        model = SentenceTransformer(cfg["embedding_model"])
        emb = model.encode(docs, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        np.save(cache, emb)
    t_emb = time.time() - t0

    stop = list(ENGLISH_STOP_WORDS.union(cfg["extra_stopwords"]))
    topic_model = BERTopic(
        umap_model=UMAP(n_neighbors=cfg["umap_neighbors"], n_components=cfg["umap_components"], min_dist=0.0,
                        metric="cosine", random_state=cfg["seed"]),
        hdbscan_model=HDBSCAN(min_cluster_size=cfg["min_cluster_size"], min_samples=cfg["min_samples"],
                              metric="euclidean", cluster_selection_method="eom", prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words=stop, ngram_range=(1, 2), min_df=5, max_df=0.6),
        top_n_words=cfg["top_n_words"], calculate_probabilities=False, verbose=False,
    )
    topics, probs = topic_model.fit_transform(docs, emb)
    if len(set(topics)) - (1 if -1 in topics else 0) > cfg["reduce_to"]:
        topic_model.reduce_topics(docs, nr_topics=cfg["reduce_to"])
        topics = topic_model.topics_
    probs = topic_model.probabilities_ if topic_model.probabilities_ is not None else [None] * len(docs)

    info = topic_model.get_topic_info()
    reps = {}
    rep_docs = topic_model.get_representative_docs()
    doc_index = {d: i for i, d in enumerate(docs)}
    for tid_, ds in rep_docs.items():
        reps[tid_] = [keys[doc_index[d]] for d in ds if d in doc_index]

    labels = _load_labels(ctx)
    month_threads = collections.Counter(when)
    rows_t, rows_terms, rows_m, rows_th = [], [], [], []
    for _, r in info.iterrows():
        t = int(r["Topic"])
        if t == -1:
            continue
        rep = reps.get(t) or [keys[i] for i, x in enumerate(topics) if x == t][:1]
        tkey = rep[0]
        terms = topic_model.get_topic(t) or []
        auto = ", ".join(w for w, _ in terms[:4])
        label, by = auto, "auto"
        for lab in labels:
            if set(lab["representatives"]) & set(rep):
                label, by = lab["label"], lab.get("coder") or "hand"
                break
        members = [i for i, x in enumerate(topics) if x == t]
        per = collections.Counter(when[i] for i in members)
        peak = max(per.items(), key=lambda kv: kv[1] / month_threads[kv[0]])[0][0] if per else None
        rows_t.append((tkey, t, label, auto, by, len(members), peak))
        rows_terms += [(tkey, i + 1, w, float(s)) for i, (w, s) in enumerate(terms)]
        rows_m += [(tkey, y, m, n, n / month_threads[(y, m)]) for (y, m), n in per.items()]
        rows_th += [(tkey, keys[i], None, int(keys[i] in rep)) for i in members]
    db.executemany("INSERT INTO topics VALUES (?,?,?,?,?,?,?)", rows_t)
    db.executemany("INSERT INTO topic_terms VALUES (?,?,?,?)", rows_terms)
    db.executemany("INSERT INTO topic_months VALUES (?,?,?,?,?)", rows_m)
    db.executemany("INSERT INTO topic_threads VALUES (?,?,?,?)", rows_th)

    import bertopic as _b
    import hdbscan as _h
    import sentence_transformers as _s
    import umap as _u
    run_info = {"config": cfg, "seed": cfg["seed"], "n_docs": len(docs), "n_topics": len(rows_t),
                "outliers": int(sum(1 for x in topics if x == -1)), "embedding_cache": cache.name,
                "embedding_seconds": round(t_emb), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "versions": {"bertopic": _b.__version__, "sentence_transformers": _s.__version__,
                             "umap": _u.__version__, "hdbscan": getattr(_h, "__version__", "?")}}
    db.executemany("INSERT INTO topic_run VALUES (?,?)", [(k, json.dumps(v)) for k, v in run_info.items()])
    ctx.note(f"topics: {len(rows_t)} topics over {len(docs):,} threads; {run_info['outliers']:,} unassigned")


def _load_labels(ctx) -> list[dict]:
    path = ctx.decisions / "topic_labels.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [{"label": r["label"], "coder": r.get("coder", ""),
                 "representatives": [k for k in (r.get("representatives") or r.get("key") or "").split("|") if k]}
                for r in csv.DictReader(fh)]

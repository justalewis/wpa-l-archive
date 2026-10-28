"""Item 6: what kind of message each one is.

Labels:
  query           a thread starter asking the list something
  discussion      a reply in an ordinary thread
  debate          a reply in a large, fast, many-voiced thread (30+ messages,
                  15+ posters, within 14 days); a structural definition, not a
                  judgment of tone
  cfp             a call for papers, proposals, chapters or participants
  job             a position announcement
  announcement    publications, events, awards, registrations, reminders
  tribute         a death, memorial or remembrance
  administrative  list business: subscribing, settings, moderation, tests,
                  auto-replies
  other           a thread starter none of the above fits

Three layers, each recorded in messages.genre_source:
  1. hand labels from decisions/genre_labels.csv (coder named per row);
  2. rules on the subject and opening of the poster's own words;
  3. for thread starters no rule places, a TF-IDF + logistic regression model
     trained on the rule labels and the hand labels, used only above a
     probability floor (otherwise "other").

Replies are discussion or debate unless a rule says otherwise (a forwarded CFP
sent as a reply is still a CFP). Evaluation against the hand labels is held
out from training and published as genre_eval.

Job ads also yield a jobs table: institution, title, rank type, date.
"""

from __future__ import annotations

import collections
import csv
import re

LABELS = ["query", "discussion", "debate", "cfp", "job", "announcement", "tribute", "administrative", "other"]
MODEL_FLOOR = 0.55
SEED = 1993

R = lambda p: re.compile(p, re.I)  # noqa: E731
RULES = [
    ("administrative", R(r"^\s*(un)?subscribe\b|\bsignoff\b|\bset wpa-l\b|^\s*test\b|\bout of (the )?office\b|"
                         r"\bauto(matic)?[- ]?reply\b|\bvacation\b.*\b(reply|message)\b|\blist (owner|moderator|policy|etiquette|business)\b|"
                         r"\bwpa-l (archive|policy|moderat|etiquette|server|move)|\bdigest (mode|format)\b|\bposting (guidelines|policy)\b"),
     R(r"^\s*(signoff|unsubscribe|subscribe|set) wpa-l\b|\bi am (currently )?out of the office\b|\bwill be out of (the )?office\b|"
       r"\bauto(matic)? reply\b|\bto unsubscribe\b.{0,40}\bsend\b")),
    ("tribute", R(r"\bin memoriam\b|\bmemorial\b|\bpassed away\b|\bpassing of\b|\bdeath of\b|\bremembering\b|\bobituary\b|"
                  r"\bcondolence|\bsad news\b|\brest in peace\b|\bRIP\b|\bhonoring the memory\b"),
     R(r"^.{0,300}\b(passed away|died (on|peacefully|suddenly|last)|has died|her death|his death|their death|memorial service)\b")),
    ("cfp", R(r"\bcfp\b|\bc\.f\.p\.|\bcall for (papers|proposals|submissions|chapters|participants|contributions|abstracts|"
              r"manuscripts|reviewers|presenters|panelists|essays|articles|nominations|editors|chairs)\b|"
              r"\bproposals? (due|deadline|invited|sought)\b|\bsubmissions? (due|deadline|invited|sought|open)\b|"
              r"\bdeadline (extended|extension)\b|\bseeking (contributors|chapters|proposals|submissions)\b|\bcall for\b"),
     R(r"^.{0,500}\b(call for (papers|proposals|submissions|chapters|contributions|abstracts)|we invite (proposals|submissions|"
       r"contributions|chapter proposals)|proposals (are )?due|submission deadline)\b")),
    ("job", R(r"\bjob\b|\bposition(s)?\b|\btenure[- ]?track\b|\bt\.?t\.?\b.*\b(position|job|line)\b|\b(assistant|associate|full) professor\b|"
              r"\blecturer(ship)?\b|\binstructor(ship)?\b|\bvisiting (assistant )?professor\b|\bhiring\b|\bvacanc|\bopening\b|"
              r"\bpostdoc|\bpost-doc|\bfellowship\b|\bdirector of (the )?(writing|composition|first[- ]year|wac|wid|rhetoric)|"
              r"\bwriting center (director|coordinator)\b|\bsearch (for|reopened)\b"),
     R(r"^.{0,600}\b(invites applications|seeks applications|is seeking (a|an) |is hiring|applications (are )?(invited|"
       r"now being accepted|will be reviewed)|review of applications|position (is|will be) (a |an )?(tenure|full|nine|three|two|"
       r"one)|to apply,? (please )?(send|submit|visit))\b")),
    ("announcement", R(r"\bannounc|\bnow (available|online|live|out)\b|\b(newest|latest) (issue|edition|volume|book)\b|\bnew (book|issue|edition|journal|collection|volume|release|website|resource)\b|"
                       r"\bjust (published|released|out)\b|\bpublished\b|\breleased?\b|\breminder\b|\binvitation\b|\bwebinar\b|"
                       r"\bworkshop\b|\bsymposium\b|\bregistration\b|\bregister\b|\baward(s|ed)?\b|\bnominations?\b|\bwinners?\b|"
                       r"\bconference\b|\bsummer (institute|seminar)\b|\bnewsletter\b|\bpress release\b|\bupdate from\b|"
                       r"\bjoins\b|\bwelcome to\b|\bcongratulat|\bkairos [0-9]|\bissue [0-9]|\bvol(ume)?\.? ?[0-9]"),
     R(r"^.{0,400}\b(i am (pleased|happy|delighted|excited) to announce|we are (pleased|happy|delighted|excited|proud) to "
       r"(announce|share)|is now (available|out|live|online)|has just been (published|released)|please join us)\b")),
    ("query", R(r"\?|\bquery\b|\bquestion\b|\bhelp\b|\badvice\b|\bsuggestions?\b|\brecommendations?\b|\blooking for\b|"
                r"\bseeking\b|\bdoes anyone\b|\banyone\b|\brequest\b|\binfo(rmation)? (on|about|needed)\b|\bsources? (on|for)\b|"
                r"\bsurvey\b|\bwondering\b|\bneed\b"),
     # A bare "?" in an opening is as often rhetorical ("Are you a new WPA?") as a
     # request, so the body rule needs a request phrase.
     R(r"^.{0,500}\b(does anyone|has anyone|is anyone|do any of you|can anyone|could anyone|can any of you|"
       r"i'?m wondering|i am wondering|i'?d (love|like) to hear|any (suggestions|advice|recommendations|thoughts|ideas)|"
       r"i'?m looking for|i am looking for|could someone|can someone|would someone|i need (some )?help|"
       r"please (respond|reply) off[- ]list|i'?m curious|i am curious|(help|advice) (with|on|regarding))\b")),
]
# A thread starter that answers or comments on earlier posts, or passes along
# an article, opens a discussion rather than asking or announcing anything.
DISCUSSION_START = R(
    r"^.{0,250}(\b\w+ (wrote|writes|said|says|asked|asks):|\b(check (this|it) out|of interest|fyi|thought you might|"
    r"thought this might|interesting (article|piece|post)|here'?s (a|an) (article|piece|link))\b|^\s*https?://)")
REPLY = re.compile(r"^\s*(re|aw|sv)\s*[:\[]", re.I)
FWD = re.compile(r"^\s*(fwd?|fw)\s*:", re.I)


def rule_label(subject: str, fresh: str, is_root: bool) -> tuple[str | None, str]:
    """(label, which part fired) from the rules, or (None, '')."""
    subj = REPLY.sub("", subject or "")
    opening = (fresh or "")[:700]
    for label, subj_rx, body_rx in RULES:
        if label == "query" and not is_root:
            continue
        if subj_rx.search(subj):
            # A subject alone saying "position" or "job" in a reply is usually a
            # discussion about jobs; ask the opening to agree for replies.
            if not is_root and label in ("job", "announcement", "cfp") and not body_rx.search(opening):
                continue
            return label, "subject"
        if body_rx.search(opening):
            return label, "body"
    if is_root and (DISCUSSION_START.search(opening) or re.match(r"^\s*re\s*\[\d+\]", subject or "", re.I)):
        return "discussion", "body"
    return None, ""


def run(db, ctx) -> None:
    from . import add_column
    add_column(db, "messages", "genre", "TEXT")
    add_column(db, "messages", "genre_source", "TEXT")

    hand = {}
    path = ctx.decisions / "genre_labels.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("label") in LABELS and not r["uid"].startswith("#"):
                    hand[r["uid"]] = (r["label"], r.get("coder", ""))

    has_fresh = "body_fresh" in {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    rows = db.execute(f"""
        SELECT m.uid, m.subject, {'m.body_fresh' if has_fresh else 'm.body'}, m.uid = t.root_uid,
               t.n_messages, t.started_at, t.last_at, t.id, m.year
        FROM messages m JOIN threads t ON t.id = m.thread_id""").fetchall()
    posters = dict(db.execute("SELECT thread_id, COUNT(DISTINCT person_id) FROM messages GROUP BY thread_id"))

    out = {}
    unruled = []
    for uid, subject, fresh, is_root, n, started, last, tid, year in rows:
        # A thread's first message titled "Re:" answers something the log never
        # kept; it is a reply, not a new query.
        if is_root and REPLY.match(subject or ""):
            is_root = False
        label, part = rule_label(subject, fresh, bool(is_root))
        if label:
            out[uid] = (label, f"rule:{part}")
        elif not is_root:
            out[uid] = (_reply_label(n, posters.get(tid, 0), started, last), "structure")
        else:
            unruled.append((uid, subject, fresh))

    model_note = _model(rows, out, hand, unruled)
    for uid, _, _ in unruled:
        out.setdefault(uid, ("other", "default"))
    # What the pipeline says about the hand-coded messages, before their hand
    # labels replace it: that comparison is the published evaluation.
    predicted = {u: out[u][0] for u in hand if u in out}
    for uid, (label, coder) in hand.items():
        if uid in out:
            out[uid] = (label, f"hand:{coder}")

    db.executemany("UPDATE messages SET genre = ?, genre_source = ? WHERE uid = ?",
                   [(g, s, u) for u, (g, s) in out.items()])
    db.execute("CREATE INDEX IF NOT EXISTS messages_genre ON messages(genre, year)")
    _evaluate(db, predicted, hand)
    _jobs(db, has_fresh)
    dist = collections.Counter(g for g, _ in out.values())
    ctx.note("genre: " + ", ".join(f"{k} {v:,}" for k, v in dist.most_common()) + f"; {model_note}")


def _reply_label(n, n_posters, started, last) -> str:
    from datetime import datetime
    if n >= 30 and n_posters >= 15 and started and last:
        days = (datetime.fromisoformat(last) - datetime.fromisoformat(started)).days
        if days <= 14:
            return "debate"
    return "discussion"


def _model(rows, out, hand, unruled) -> str:
    """Fill thread starters the rules missed, from a model trained on the rules."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return "no model (scikit-learn not installed)"
    roots = {u: (s, f) for u, s, f, is_root, *_ in rows if is_root}
    train_x, train_y = [], []
    for u, (s, f) in roots.items():
        if u in hand:
            continue  # hand labels are held out for evaluation
        g = out.get(u, (None,))[0]
        if g in ("query", "cfp", "job", "announcement", "tribute", "administrative", "discussion"):
            train_x.append(_doc(s, f))
            train_y.append(g)
    if len(set(train_y)) < 2 or not unruled:
        return "no model (too few labels)"
    big = len(train_x) >= 200   # the cutoffs assume a real corpus; tests build a tiny one
    vec = TfidfVectorizer(min_df=3 if big else 1, max_df=0.5 if big else 1.0, ngram_range=(1, 2),
                          sublinear_tf=True, max_features=60000)
    try:
        x = vec.fit_transform(train_x)
        clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced", random_state=SEED)
        clf.fit(x, train_y)
    except ValueError as e:
        return f"no model ({e})"
    probs = clf.predict_proba(vec.transform([_doc(s, f) for _, s, f in unruled]))
    placed = 0
    for (uid, _, _), p in zip(unruled, probs):
        i = p.argmax()
        if p[i] >= MODEL_FLOOR:
            out[uid] = (clf.classes_[i], f"model:{p[i]:.2f}")
            placed += 1
    return f"model placed {placed:,} of {len(unruled):,} starters no rule matched"


def _doc(subject, fresh):
    return f"{subject or ''} {subject or ''} {(fresh or '')[:1500]}"


def _evaluate(db, pred: dict, hand: dict) -> None:
    """Per-class precision and recall of rules + structure + model against the hand labels."""
    db.executescript("""
        DROP TABLE IF EXISTS genre_eval;
        CREATE TABLE genre_eval (label TEXT PRIMARY KEY, precision REAL, recall REAL, support INTEGER,
                                 predicted INTEGER, coder TEXT);
    """)
    hand = {u: v for u, v in hand.items() if u in pred}
    if not hand:
        return
    coder = ", ".join(sorted({c for _, c in hand.values() if c}))
    out = []
    for label in LABELS:
        tp = sum(1 for u, (g, _) in hand.items() if g == label and pred[u] == label)
        support = sum(1 for g, _ in hand.values() if g == label)
        predicted = sum(1 for u in hand if pred[u] == label)
        if support or predicted:
            out.append((label, tp / predicted if predicted else None, tp / support if support else None,
                        support, predicted, coder))
    db.executemany("INSERT INTO genre_eval VALUES (?,?,?,?,?,?)", out)


TITLE_CLEAN = re.compile(r"^\s*(re|fwd?|fw)\s*:\s*|\b(job|position|posting|announcement|opening|ad)s?\s*[:\-–]\s*", re.I)
INSTITUTION = re.compile(
    r"((?:[A-Z][\w.&'-]*[ \t]+){0,5}(?:University|College|Institute|Polytechnic)(?:[ \t]+(?:of|at|in|the|[A-Z][\w.&'-]*)){0,5})")


def rank_type(text: str) -> str:
    t = text.lower()
    if re.search(r"writing (center|centre) (director|coordinator)|director of (the )?writing (center|centre)", t):
        return "writing center director"
    if re.search(r"director of (the )?(writing|composition|first[- ]year|wac|wid|rhetoric)|writing program (administrator|director)|\bwpa\b", t):
        return "WPA"
    if re.search(r"non[- ]tenure|lecturer|instructor|visiting|adjunct|part[- ]time|term (position|appointment)|teaching (professor|faculty)", t):
        return "non-tenure-line"
    if re.search(r"tenure[- ]track|tenure[- ]line|\btt\b|assistant professor|associate professor|full professor", t):
        return "tenure-line"
    if re.search(r"post[- ]?doc|fellowship", t):
        return "postdoc/fellowship"
    return "other"


def _jobs(db, has_fresh) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS jobs;
        CREATE TABLE jobs (uid TEXT PRIMARY KEY, institution TEXT, title TEXT, rank_type TEXT, posted_at TEXT, year INTEGER);
    """)
    out = []
    for uid, subject, body, sent_at, year in db.execute(
            f"SELECT uid, subject, {'body_fresh' if has_fresh else 'body'}, sent_at, year FROM messages "
            f"WHERE genre = 'job' AND subject NOT LIKE 'Re:%'"):
        head = f"{subject}\n{(body or '')[:1200]}"
        inst = next((m.group(0).strip(" ,.;:") for m in INSTITUTION.finditer(head)
                     if not re.match(r"^(College (English|Composition)|University (Press|Writing))", m.group(0).strip())), "")
        title = TITLE_CLEAN.sub("", subject or "").strip(" -–:") or subject
        out.append((uid, inst, title[:200], rank_type(head), sent_at, year))
    db.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?)", out)

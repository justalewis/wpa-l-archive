# Research tools: schema and migration plan

Response to `SPEC.md` §0 (the research-tools package of 2026-10-27). Nothing in the code
changes until this plan is agreed.

## §0 answers: what the repo is today

| Question | Answer |
|---|---|
| Stack | Flask 3 + Jinja, served by gunicorn (1 process, 4 threads). SQLite with FTS5 (`porter unicode61`, external-content over `messages`). No JS today: CSP is `script-src 'none'`, `style-src 'self'`, `font-src 'self'`. Fly.io app `wpa-l-archive` (iad, 512 MB, auto-stop), volume `/data`. |
| Build | `python -m wpal.build` reads the 341 logs in `data/raw/` (process pool), parses (`wpal/parse.py`), threads (`wpal/threads.py`), writes `data/archive.db` from scratch in ~9 min, plus `reports/coverage.md`. The database is uploaded to the volume separately; code deploys don't carry it. |
| Messages | `messages` table, one row per message. Public id `uid`: 12 hex. It is the hash of the Message-ID when there is one, otherwise of the raw header block and the first 4 KB of raw body, so it stays stable across rebuilds (checked over three). Duplicates get `-2`. |
| Threads | `threads` table, integer `id` assigned in build order. **Not stable across rebuilds**: any threading change renumbers them. `messages.thread_id`, `parent_uid`, `thread_method` (`header`, `sibling`, `subject`, `root`). |
| Posters | No table. Each message carries `from_name` (as posted), `name_key` (sorted lowercase tokens) and `poster_key`: HMAC-SHA256 of the lowercased address with the salt in `data/poster.salt`. The salt is local only, never deployed, and must be kept. |
| Opt-outs | `data/requests.db` (on the volume, never rebuilt): `optout_requests` from the form, and `suppression` (kind `poster` → `poster_key`, kind `name` → `name_key`). Every reader query goes through the temp view `visible`, which drops suppressed rows. |

## The core decision: three kinds of data, three homes

The spec says every feature must be regenerable from the build, but also that review
decisions must survive rebuilds. Those fit together if every table belongs to exactly one
of three homes:

1. **`archive.db`: derived.** Everything the build computes: messages, threads, posters,
   genres, topics, trends denominators, networks, citations, institutions. It is dropped and
   rebuilt every time and is never edited by hand or by the app.
2. **`decisions/`: curated, in git.** Human judgments that shape the build, as plain CSV or
   YAML, reviewed in diffs:
   - `poster_merges.csv` and `poster_splits.csv`
   - `thread_overrides.csv` (merge or split)
   - `genre_labels.csv` (the ~1,500 hand-coded sample)
   - `topic_labels.csv`
   - `citation_matches.csv` and `institution_matches.csv` (accepted or rejected)
   - `events.yaml`

   The build reads them; a decision applies on the next build.
3. **`state.db` (today's `requests.db`, renamed): live, on the volume.** Things the public
   or the operator create at runtime, plus anything sensitive:
   - opt-out requests (they carry contact details) and suppressions
   - thread flags from readers
   - saved corpora

   This file is never rebuilt and is backed up with the volume snapshots.

A review queue moves work between homes. The build writes candidates into `archive.db`; a CLI
(`python -m wpal.review merges|threads|citations|institutions`) or a token-protected admin
page lists them; an accepted decision is appended to the matching `decisions/` file. Flags
from readers land in `state.db`, and an operator turns them into `decisions/` rows.

Every decision file keys on identifiers that survive a rebuild:
- message `uid`
- `poster_key`, an HMAC, so no address ever enters git
- `person_id`, defined below
- normalized names
- never `thread_id`

## Stable identifiers

- **Messages:** `uid`, unchanged.
- **Threads:** add `threads.key` = the root message's `uid`. URLs become `/t/{root-uid}`, and
  `/thread/{int}` redirects for one release. A thread override that changes a root records the
  old key in `thread_redirects`, which the build regenerates from `decisions/`.
- **Persons:** `person_id` = 10 hex from the hash of the cluster's earliest `poster_key`. Stable
  unless a decision moves that earliest key, in which case `person_redirects` maps old to new.
- **Saved corpora:** a random 10-char id in `state.db`, with the frozen list of message `uid`s
  and the build timestamp. "Identical IDs after a rebuild" holds because `uid` is stable. A
  corpus can also re-run its query against the current build, as a separate, clearly labeled
  view.

## Schema additions to `archive.db`, by item

| Item | Tables (derived) | Decision files | `state.db` |
|---|---|---|---|
| 0 Restyle | none | none | none |
| 2 Export/API | `month_totals(year, month, messages, threads)` (also feeds 4) | none | `corpora(id, name, query_json, uids_json, build_id, created_at)` |
| Ethics | `persons.public` (passes the thresholds and isn't suppressed) | none | `suppression` gains kind `person` |
| 4 Trends | `month_totals` (above), plus `messages.body_fresh` (body with quoted text and signature removed) and a second FTS index over it (`fresh_fts`) for the "exclude quoted text" toggle | none | none |
| 1 Identity | `persons(person_id, display_name, first_at, last_at, n_posts, n_started, public)`, `person_aliases(person_id, name, name_key, n)`, `person_keys(person_id, poster_key)`, `affiliations(person_id, institution_raw, first_at, last_at, n)`, `merge_candidates(...)`, `person_redirects`; `messages.person_id` | `poster_merges.csv`, `poster_splits.csv` | none |
| 3 Confidence | `edges(child_uid, parent_uid, link_method)`; `threads.key`, `confidence`, `confidence_reason`; `thread_redirects`; `split_candidates` | `thread_overrides.csv` | `thread_flags(thread_key, reason, created_at, status)` |
| 6 Genre | `messages.genre`, `genre_scores`; `jobs(uid, institution_raw, title, rank_type, posted_at)`; `genre_eval(class, precision, recall, n)` | `genre_labels.csv` | none |
| 5 Topics | `topics`, `topic_terms`, `topic_months`, `topic_threads`, `topic_run(version, params_json, seed)` | `topic_labels.csv`, `topics.toml` (pinned config) | none |
| 7 Network | `person_edges(era, src, dst, n, link_methods)`, `person_era_stats`, `concentration(year, top1, top10, gini)`, `cohorts(first_year, after_years, retained)` | none | none |
| 8 Flashpoints | `thread_metrics`, `week_signals`, `flashpoints(rank, kind, ref, score, label)` | none | none |
| 9 Citations | `mentions(uid, raw, kind, pinakes_id, confidence)`, `cited_works` | `citation_matches.csv` | none |
| 10 Institutions | `institutions(unitid, name, carnegie, state, lat, lon)`, `institution_aliases`, `person_institutions`, `unmatched_institutions` | `institution_matches.csv` | none |
| 11 Events | `events` | `events.yaml` | none |

**Migration path.** Because `archive.db` is fully derived, there is no in-place migration:
each item adds to `SCHEMA` and to the build, and the next build produces the new tables.
`state.db` gets a small versioned migration (`PRAGMA user_version`), applied at app start:
rename the file, add `corpora` and `thread_flags`, and add kind `person` to `suppression`.
`wpal.migrate` keeps handling index-only changes on the deployed database.

## Opt-out propagation (built with item 2)

- Suppression keys grow from `poster_key` and `name_key` to `person_id`. Suppressing a person
  covers every alias and address in their cluster.
- One rule, in one place: a `visible` view for messages, plus `visible_persons`. Exports, the
  API, person pages, network labels, citation-by-poster and institution rosters all read
  through them, and tests assert this for each surface.
- **Aggregates (your call; see the questions below).** The default I'd suggest: suppressed
  posters' messages stay in anonymous totals (monthly volume, trend denominators, genre
  shares), because removing them would quietly change the record's shape. They are removed
  from anything that names or profiles a person, or that shows text. This matches what the
  site already does: the home totals already drop them, so this would be a change.
- Withdrawn messages leave no text anywhere: not in exports, not in the API, not as snippets.

## Design restyle (step 0)

Copied from Pinakes `6323d3f` (cloned read-only to the scratchpad; Pinakes itself untouched):
- `tokens.css` is already a verbatim copy. I'll add a header line with the copy date and
  commit.
- `fonts.css`, `fonts/` and the license README.
- `_alexandrian_head.html` ported, with the key `wpa-figure`.
- `base-alexandrian.html` as the model for the shell.
- The listed sections of `pinakes.css`, lifted into `static/wpa.css`.
- The supplied sprite goes in as `templates/_icons_wpa.html`, with `icons/` copied to
  `tools/icons/`.

**CSP conflict.** Pinakes applies the stored figure with an inline pre-paint script and
allows `'unsafe-inline'`. The reader allows no scripts. I'll ship the pre-paint and toggle
as two small static files (`/static/figure.js`, loaded synchronously in `<head>`) and move
the CSP to `script-src 'self'`. No inline script or style, so `style-src 'self'` stays. The
charts the later items need (trends, topics, networks) also need `script-src 'self'`; I'll
draw them as server-rendered SVG where that's enough (timeline, histograms, stacked shares)
and use a small vendored script only for interactive ones (multi-term trends, network
graphs), served from `/static`, never from a CDN.

## Sequence and branches

The repo has no remote, so "its own branch" means a local branch per item, merged to `main`
after tests and a browser check, then deployed. Proposed order (the spec's, with one swap):

1. `restyle`: step 0, plus the `/design` page.
2. `export-api`: item 2 plus ethics opt-out propagation, the `state.db` migration, `/data`,
   `/ethics`.
3. `trends`: item 4, including quoted-text stripping (`body_fresh`), which items 5, 6 and 9
   also need.
4. `identity`: item 1. Then `confidence`: item 3, which also introduces stable thread keys.
5. `genre`: item 6. The rules bootstrap is mine. The hand-labeled sample of ~1,500 is
   yours, or an LLM labeler you approve: it is research coding, not something I should
   decide.
6. Then 11 (events: small, and it feeds the other charts), 7, 8, 10, 5, 9.

**Heavier dependencies to approve before their items:**
- 5 needs `bertopic`, `sentence-transformers` and torch: build-time only, never deployed.
  Embedding ~45k threads on CPU takes about an hour.
- 10 needs the IPEDS directory and Carnegie files (public NCES downloads).
- 9 needs an agreed exchange format with Pinakes: a periodic dump of authors and works, or an
  API.

## Decisions (Justin, 2026-10-27)

1. **Aggregates:** suppressed posters stay in anonymous counts (volume, trend denominators,
   genre shares) and are removed from everything that names, profiles, or shows text.
2. **Thresholds:** 25 posts for person-level views; 3 interactions for network edges.
3. **Public writes:** saved corpora and thread flags are open to anyone, rate-limited per IP,
   with a honeypot. Flags only queue for review.
4. **Scope:** all 11 items this round. Where the spec calls for hand coding (the genre sample),
   the labels are produced by rules and by Claude and are marked as such on `/data`, to be
   replaced or checked by a human coder.

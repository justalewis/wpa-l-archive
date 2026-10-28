# WPA-L Archive Reader: working notes

Status as of 2026-10-27. Deployed at https://wpa-l-archive.fly.dev/ (Fly app
`wpa-l-archive`, personal org, `iad`). The repo is not pushed anywhere, no one has been
contacted, and all page text in `templates/` is placeholder (marked as such on the page)
until Justin writes the public copy. The site is reachable by anyone with the URL; it is
noindex, so search engines are asked not to list it.

## Deploy

- One `shared-cpu-1x` machine, 512 MB, that stops when idle and starts on the next request
  (`auto_stop_machines = 'stop'`, `min_machines_running = 0`). Cost is the 3 GB volume plus
  the minutes the machine runs. A cold start adds a second or two.
- Volume `wpal_data` at `/data` holds `archive.db` (1.15 GB) and `requests.db` (opt-out
  requests and suppressions). Fly snapshots the volume daily and keeps 5.
- Secret: `WPAL_SECRET` (Flask session key for the form's CSRF token).
- The database is not in the image. Code deploys (`flyctl deploy --ha=false --remote-only`)
  ship only the app. To replace the database after a rebuild:
  1. `gzip` `data/archive.db` (about 387 MB compressed).
  2. Upload it with `flyctl ssh sftp put data/archive.db.gz /data/archive.db.gz`. In Git
     Bash, set `MSYS_NO_PATHCONV=1` first, or the remote path is rewritten to a Windows path.
  3. On the machine, `gunzip -c` it to `/data/archive.db.new` and `mv` it over
     `archive.db`.
  4. Compare MD5s with the local copy. Keep the machine awake during the upload: an SSH
     session alone doesn't count as traffic for auto-stop.
- `python -m wpal.migrate` adds new indexes to a deployed database in place. The covering
  indexes matter: on a cold volume, any query that touches message bodies for every row
  took about 40 s. With them, every page measured 0.2–2 s.
- Reading opt-out requests: `flyctl ssh console -C "sqlite3 ..."` doesn't work because the
  image has no sqlite3 CLI. Use `python -c` with the `sqlite3` module, or `sftp get`
  `/data/requests.db`.

## State

| Piece | State |
|---|---|
| Source | 341 monthly logs + README from the IA item (3.57 GB), all MD5-verified against IA's metadata, in `data/raw/` (gitignored, never modified). The "346" count is the item's total file count, which includes the torrent and IA metadata files. 2021 has Jan–May. |
| Parser, threading | `wpal/parse.py`, `wpal/threads.py`; 40 parser tests on fixtures from 1993, 1995, 1999, 2006, 2011, 2020 |
| Build | `python -m wpal.build` → `data/archive.db` (1.15 GB, FTS5) in about 9 minutes; writes `reports/coverage.md` |
| Reader | `wpal/app.py` (Flask): browse by year and month, thread view, permalinks with a citation line, keyword/phrase + name + year search with a per-year match chart, messages-per-month timeline, opt-out form; 12 app tests |
| Browser check | done in the pane: home, month, thread, permalink, search, timeline, opt-out submit, a live suppression (410 on the permalink, "1 message withdrawn" on the thread, totals drop), light/dark, 375 px width |

Run locally:

```bash
python -m flask --app wpal.app run --port 5057
```

`.claude/launch.json` has the same command as `wpal-reader`. Stop the server before a
rebuild, because Windows won't replace a database file another process has open.

## Parse coverage (full build)

186,436 messages; 186,429 dated; 161,961 (87%) in a thread of two or more; 45,660 threads,
21,185 with replies. Full per-year table in `reports/coverage.md`.

| Era | Messages | In a thread | How replies were linked |
|---|---:|---:|---|
| 1993–1999 | 26,337 | 87% | subject + time window; siblings sharing an In-Reply-To |
| 2000–2009 | 82,498 | 88% | same; no Message-ID in the logs |
| 2010 | 9,453 | 90% | Message-ID starts appearing (38% of linked replies by header) |
| 2011–2021 | 68,148 | 85% | Message-ID / In-Reply-To (91% of linked replies) |

Volume runs 7,500–10,600 a year from 2003 to 2013 and falls to about 2,700 in 2020.

Failure cases flagged in the build:

- **HTML-only** (2,174), mostly 2002–2005 and 2013–14: text comes from a crude HTML strip and
  reads a little rough.
- **Duplicate ids** (135): the same message logged twice (identical raw bytes or a resent
  Message-ID). Both copies are kept; the second gets `-2`.
- **No timezone** (122) or **unparsed date** (7): times treated as UTC, or month taken from
  the log file.
- **Date outside the log's month** (102): sender's clock was wrong; filed under the log month.
- **No text part** (75): attachment-only posts.
- **No From** (36), **Unnamed poster** (974): address-only From lines with no other post to
  borrow a name from, concentrated in 1993–96 (BITNET) and 2001–03.
- **Leak audit: 0.** A looser second pattern finds no address-like strings in any stored
  subject, name or body.

## Format findings

- Every file is a LISTSERV notebook log: messages separated by a line of exactly 73 `=`,
  each opening with a column-aligned `Date` / `Reply-To` / `Sender` / `From` / `Subject`
  block. Early logs end the header block with a line holding one space, not a blank line.
- 1990s: plain ASCII, no MIME; many bare-address From lines; some bodies hard-wrapped at 80
  columns mid-word. Late 1990s on: MIME, quoted-printable (some double-encoded in the 2000s),
  base64 attachments inline. Attachments are most of the 3.5 GB.
- No Message-ID before 2010. Older `In-Reply-To: Message of <date> from <address>` names a
  person, not a message. Some clients reused one In-Reply-To value across unrelated replies;
  joining on it alone chained a "Job Announcement" post to two months of other threads, so a
  shared In-Reply-To now joins only when subjects agree. Generic subjects ("Job
  Announcement", "CFP", "question") never join on subject alone.
- **Addresses hide in more places than headers and signatures:**
  - Outlook safe-links carry the reader's address in the URL (`%7Cname%40school.edu`). This
    affected over 1,100 spots in 2020 alone. Safe-links are now unwrapped to their real
    target.
  - Proofpoint and URLDefense rewrap those links again (`-2540`, `%2540`, `*40`).
  - Addresses get glued to URL-encoding or to neighboring text (`%22%20name@...`).
  - People spelled them out to dodge harvesters (`name at school dot edu`).

  All of these are scrubbed and covered by tests.
- Top-posted Outlook replies quote the earlier message without `>` marks, so the reader
  folds `>`-quoted text but shows Outlook-style quotes in full. Detecting `From: … Sent: …`
  blocks would fix most of it.

## What is stored and what is dropped

- Stored: UTC date, display name as posted, subject, plain-text body, attachment content
  types, source file and position, a stable id, a salted poster hash.
- Dropped: every email address (headers, bodies, signatures, `X wrote:` lines, GroupWise
  `>>>` lines, mailto links, encoded and spelled-out forms); `X-To`/`X-cc` headers;
  attachments; raw Message-IDs (only hashes kept, since some embed addresses).
- Names: where a From line had only an address, the name comes from that address's other
  posts (1,856 recovered); otherwise "Unnamed poster". Addresses exist only in memory
  during the build.
- Permalink ids hash the Message-ID, or the raw header block and first 4 KB of raw body, so
  parser changes don't move them (confirmed across three rebuilds).
- Poster hash: HMAC-SHA256 of the address with `data/poster.salt` (gitignored). **Back that
  file up**: losing it re-keys every poster and breaks address-keyed suppressions.
- Kept, and personal: phone numbers and street addresses in signatures; tracking parameters
  in URLs (`user_id=…`).

## Research tools (SPEC.md items 1–11, built 2026-10-27)

`SPEC.md` is the brief; `PLAN.md` is the schema plan and Justin's four decisions
(opted-out posters stay in anonymous totals; 25-post and 3-exchange thresholds; open,
rate-limited public writes; all eleven items this round). Each item landed on its own local
branch and was merged into `main`.

**Where data lives.**
- **`data/archive.db`:** derived, rebuilt by `python -m wpal.build`. Build stages
  (`wpal/stages/`) run after parsing and can re-run alone: `--stages NAME` re-runs that stage
  and every later one, and `--skip-stages topics` skips the slow one.
- **`decisions/`:** curated, in git, read by the build. Poster merges and splits, thread
  overrides, genre hand labels, `events.yaml`, `topics.toml` and topic labels, citation and
  institution matches.
- **`data/state.db`:** live, on the volume (formerly `requests.db`, migrated by
  `PRAGMA user_version`). Opt-out requests, suppressions, saved corpora, thread flags, the
  write-rate log.
- **Review queues:** `python -m wpal.review merges | flags | citations | institutions`.
  Accepting an item appends a row to `decisions/`.

| Item | Pages | Full-corpus result |
|---|---|---|
| 0 Restyle | every page; `/design` | Alexandrian Suite from Pinakes `6323d3f`. The WPA-L sprite is generated by `tools/icons/build_icons.py`; its two accent styles are allowed by CSP hash, with no `'unsafe-inline'`. |
| 1 Identity | `/people`, `/person/{id}` | 8,800 addresses → 6,515 persons; 1,070 have pages (25+ posts). 2,285 name-based merges and 145 nickname/prefix pairs are listed for review. |
| 2 Data | `/data`, `/ethics`, `/corpus/{id}`, `/export`, `/api/v1/` | Corpora freeze uids and survive rebuilds (tested). CSV/JSONL exports, standard or pseudonymized, behind a terms click. The API is limited to 60 requests a minute per client. |
| 3 Confidence | badge, method table and flag form on every thread | low 7,364 · medium 6,564 · high 7,257 (threads of 2+). Overrides in `decisions/thread_overrides.csv`. |
| 4 Trends | `/trends`; per-1,000 view on search | Up to 6 terms; rates per 1,000 messages or 10,000 words; own-words option. |
| 6 Genre | `/genres`, `/jobs`; search filter; export field | Rules + logistic regression. Evaluated on 299 Claude-coded messages (named as coder on every row): weighted P 0.75 / R 0.66; strong for job, CFP, tribute; weak for "other". **Needs a human-coded sample.** |
| 11 Events | `/events`; overlay on `/timeline`, `/trends` | 38 events. CCCC host cities checked against list messages; months approximate. |
| 7 Network | `/participation` | 97,170 person-to-person edges in six eras; concentration, Gini, cohort retention, roles. |
| 8 Flashpoints | `/flashpoints` | 9,915 threads and 1,393 weeks scored; neutral labels. |
| 10 Institutions | `/institutions`, `/institution/{unitid}` | 1,336 names matched to 846 IPEDS units; 1,007 unmatched, listed for review. |
| 9 Citations | `/cited`, `/cited/work/{id}`, linked works on messages | Matched against 67,281 Pinakes works (`tools/pinakes_dump.py`): 1,820 title and 53 DOI matches accepted; 1,659 in review. Some accepted titles are books whose journal review carries the same title (Pinakes records don't mark reviews). |
| 5 Topics | `/topics`, `/topic/{key}` | 59 topics over 40,465 thread documents (19,158 fit none). BERTopic, seeded, config in `decisions/topics.toml`, embeddings cached under `data/reference/topics/` (25 minutes to embed the first time, about 3 to refit). |

**Reference data** (gitignored, under `data/reference/`):
- IPEDS `HD2022.zip` (NCES directory, about 1 MB);
- the Pinakes works dump (re-run `tools/pinakes_dump.py` to refresh);
- the topic embedding cache and the `all-MiniLM-L6-v2` model (downloaded by
  sentence-transformers on first fit).

Build-only libraries are in `requirements-build.txt`; the deployed app needs only Flask and
gunicorn.

**Known limits worth checking.**
- Name-based person merges can join two people who share a name.
- Debate is a structural label (a big, fast thread), not a reading of tone.
- Signatures under-represent contingent faculty, so the institution shares describe signed
  voices only.
- Pinakes holds journal articles, so books the list discussed don't match.
- Topic labels are automatic until someone names them in `decisions/topic_labels.csv`.

## Opt-out design

- `data/state.db` (on the volume, never rebuilt) holds `optout_requests` and `suppression`:
  kind `poster` keyed on the poster hash, `name` keyed on sorted lowercase name tokens, or
  `person`.
- Withdrawing one address or name withdraws the whole person it belongs to.
- Every query that names or shows text goes through a temp view, `visible`, and this covers
  exports, the API, corpora, person pages, networks, citations and institution rosters.
- Anonymous totals keep withdrawn posters (decision 1).
- The form records a request and suppresses nothing until Justin reviews it. There is no
  admin screen yet; suppressions are added by SQL.

## Open ethics and policy questions (for Justin)

1. **Verifying opt-outs.** The archive no longer holds addresses, so "reply from the address
   you posted from" can't be checked against our data. Options: check against the IA copy
   by hand; accept a current institutional address plus a match on name and example posts;
   or honor any plausible request, since withdrawing from a reading room costs little.
   Estates and representatives need a rule too.
2. **Quoted text.** An opted-out poster's words survive inside others' replies, with their
   name in the `X wrote:` line (and in Outlook `From:` blocks). Leave, strip the name,
   collapse their quote blocks, or redact? The poster hash can't see these, so this would
   key on the name.
3. **Name vs. address keys.** Names collide and people post under variants ("Rice, Rich" /
   "Rich Rice"). Address-keyed is precise but needs the requester's old addresses, hashed at
   request time against the salt.
4. **Noindex vs. discoverability.** Noindex keeps name searches from landing here but hides
   the archive from people who'd want it. Middle path: index home, year, month and about
   pages; keep threads and messages noindex.
5. **The "posted by" search field** is also a name lookup. Require a keyword with it?
6. **Bulk-download terms**: research use, no republishing contact details, honor withdrawals
   in derived datasets, a citation request.
7. **Signatures**: strip trailing signature blocks (phones, street addresses), or keep the
   record as posted?
8. **Fixtures**: `tests/fixtures/` holds real list text with addresses, sender names, phone
   numbers and some named people swapped for fake ones (`tools/make_fixtures.py`). Other
   names in bodies remain (e.g. a 2011 board announcement). Review before the repo goes
   public.

## Next steps

- Fold Outlook-style quoted blocks; better HTML-to-text for the 2,174 HTML-only posts.
- Topic timelines: saved queries (labor, assessment, Turnitin/plagiarism software, dual
  credit, the 2019 Chair's Address and 2019–2020 governance crisis) plotted per month.
- Bulk download (JSONL or SQLite, addresses already gone) behind a terms-of-use click.
- Admin view for requests and suppressions; email verification for requests.
- An email alert when an opt-out request arrives (requests now sit in `/data/requests.db`
  until someone looks).
- A custom domain, if one is wanted (`flyctl certs add`).
- Public copy for the about and opt-out pages in Justin's voice (`justin-email` /
  `justin-writing-master`, then the extended `no-ai-trace` pass). No CompPile comparisons.
- The NACR conversation (Justin's to start).

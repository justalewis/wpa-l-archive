# WPA-L Archive Reader: working notes

Status as of 2026-10-27 (save point, mid-build). Nothing is deployed, nothing is pushed,
no one has been contacted, and all page text in `templates/` is placeholder.

## Where things stand

| Step | State |
|---|---|
| Repo, design tokens (Alexandrian Suite `tokens.css`, `fonts.css`, `fonts/` copied from Pinakes) | done |
| IA file list | done: the item holds **341 monthly logs + README** (3.57 GB). The "346" count is the item's total file count, which includes the torrent and IA metadata files. 2021 has 5 months (Jan–May). |
| Download to `data/raw/` (gitignored) | **149 of 341 done.** See "Resume" below. |
| Parser `wpal/parse.py`, threading `wpal/threads.py` | written; 28 tests pass on fixtures from 1993, 1995, 1999, 2006, 2011, 2020 |
| Build `wpal/build.py` (SQLite + FTS5 + coverage report) | written; run on 1993 only so far (782 messages, all dated) |
| Flask reader `wpal/app.py`, `templates/`, `static/reader.css` | written; imports and serves `/` against the 1993 test DB. **Not yet checked in the browser pane.** |
| App tests (suppression honored everywhere, noindex headers, opt-out POST) | not written |
| Full build, coverage table, browser check | not done |

## Resume

1. Finish the download. A single direct call worked with curl (Python's urllib hit an
   expired-certificate error on some IA mirror hosts; do not turn off verification). The
   job list is regenerated from `ia_meta.json`, skipping files already at full size:

   ```bash
   curl -sS https://archive.org/metadata/WPA-L-1993-2021-Complete-Uncompresse -o ia_meta.json
   ```

   then loop `curl -sSL --retry 3 -o data/raw/YYYY/YYYY-MM.txt <url>` over the missing
   files, three or four at a time, checking each file's size against the metadata.
   Generate the list with Unix line endings (a `\r` in the size field is what made the
   first curl pass reject good files).
2. `python -m wpal.build` (all files; expect tens of minutes and a DB of a few GB).
   It writes `data/archive.db` and `reports/coverage.md`.
3. Write `tests/test_app.py`: build a DB from the fixtures (copy them to a temp dir as
   `YYYY-MM.txt`), add a `suppression` row, and assert the poster disappears from month,
   thread, permalink (410), search, and timeline; check `X-Robots-Tag`, robots meta and
   `/robots.txt`; post the opt-out form.
4. Add `.claude/launch.json` (`flask --app wpal.app run --port 5057`), run it in the
   browser pane, and check each page in light and dark.
5. Fill in parse coverage below, commit.

## Format findings (from spot reads across eras)

- Every file is a LISTSERV notebook log: messages separated by a line of exactly 73 `=`,
  each starting with a column-aligned `Date:` / `Reply-To:` / `Sender:` / `From:` /
  `Subject:` block. The split anchors on that line followed by `Date:`.
- 1993–late 1990s: plain ASCII, no MIME. The header block ends with a line holding a single
  space, not an empty line (the parser treats it as the end of headers). Many From lines are
  bare addresses (`ewhite@...`, BITNET `C509379@MIZZOU1`) with no display name. Some 1995
  bodies are hard-wrapped at 80 columns mid-word, so an address can be split across lines.
- Late 1990s on: MIME headers; quoted-printable common; some 2000s clients double-encoded QP
  (`=3D` left after one decode), which the parser undoes when the leftovers are plentiful.
- Attachments (Word files, `winmail.dat`, images) are inlined as base64. That is most of the
  3.5 GB; 2010 onward runs 170–365 MB a year.
- **No `Message-ID` before about 2010**, and only scattered ones in 2008–2010. Earlier
  `In-Reply-To` values name messages the log never kept, so pre-2010 threading leans on
  normalized subjects (30-day window for replies, 3 days for unmarked posts), plus "siblings
  that share an In-Reply-To belong together." From 2011 every message has a Message-ID and
  header threading works.
- Older `In-Reply-To: Message of <date> from <address>` lines name a person, not a message;
  the parser ignores them for linking.
- Message-IDs sometimes embed an address (Entourage: `<...%name@host>`). Only hashes of IDs
  are stored.
- `X-To` / `X-cc` headers carry other lists' names and addresses; they are not stored.

## What the parser stores and drops

- Stored: date (UTC), display name as posted, subject, plain-text body, attachment content
  types, source file and position, a stable id, a salted poster hash.
- Dropped: every email address, in headers, bodies, signatures, `X wrote:` lines, GroupWise
  `>>> addr date >>>` lines, and `mailto:` links. Old forms (`user @ HOST`, `user@@host`,
  quoted local parts) are covered; `@handle` and `meet @ 3pm` are left alone.
- Name recovery: when a From line had only an address, the name comes from the same address's
  other posts (114 recovered in 1993 alone); otherwise "Unnamed poster" (117 in 1993).
  Addresses exist only in memory during the build.
- Stable id: hash of the Message-ID when there is one, otherwise of the raw header block and
  first 4 KB of raw body, so parser changes don't move permalinks.
- Poster hash: HMAC-SHA256 of the lowercased address with a salt in `data/poster.salt`
  (gitignored). **Keep that file**: losing it re-keys every poster and breaks address-keyed
  suppressions.

Known gaps: addresses split across hard-wrapped lines; addresses spelled out ("jdoe at
uni dot edu"); phone numbers and street addresses in signatures are kept (they are not
email addresses, but they are personal).

## Opt-out design (built, not yet browser-checked)

- `data/requests.db` (gitignored, separate from the rebuildable archive DB) holds
  `optout_requests` and `suppression` (kind `poster` keyed on poster hash, or `name` keyed on
  a normalized name).
- Every reader query goes through a temp view `visible` that drops suppressed rows. A
  withdrawn permalink returns 410; a thread notes how many messages were withdrawn.
- The form records requests only; nothing is suppressed until Justin reviews one. There is
  no admin screen yet; suppressions are added with SQL for now.

## Open ethics and policy questions (for Justin)

1. **Verifying opt-outs.** The archive no longer holds addresses, so "reply from the address
   you posted from" can't be checked against our data. Options: check against the IA copy by
   hand; accept a reply from a current institutional address plus a match on name and
   example posts; or honor any plausible request, on the view that withdrawing from a
   reading room costs little. Estates and representatives need a rule too.
2. **Quoted text.** An opted-out poster's words survive inside other people's replies, with
   their name in the `X wrote:` line. Choices: leave as is; strip the attribution name;
   collapse quote blocks attributed to them; or redact them. The poster hash can't see
   these, so this would key on the name.
3. **Name-keyed vs. address-keyed suppression.** Names collide (two people, one name) and
   one person posts under several names. Address-keyed is precise but needs the requester's
   old addresses, which we'd hash at request time against the salt.
4. **Noindex vs. discoverability.** Noindex keeps name searches from landing here but also
   hides the archive from people who would want it. A middle path: index the home, year and
   month pages and about page; keep threads and messages noindex.
5. **The "posted by" search filter.** Useful for research; it is also a name lookup. Could
   require a keyword alongside it.
6. **Terms of use for the bulk download.** Needs wording on research use, no re-publication
   of contact details, honoring withdrawals in derived datasets, and a citation request.
7. **Signatures.** Phone numbers and street addresses are personal data the deposit carries.
   Strip trailing signature blocks, or leave them?
8. **Fixtures.** `tests/fixtures/` holds real list text with addresses, sender names, phone
   numbers and a few named people swapped for fake ones (`tools/make_fixtures.py`). Other
   names in bodies remain (e.g. a 2011 board announcement). Review before the repo goes
   public.

## Next steps (after the reader works)

- Topic timelines: saved queries (labor, assessment, Turnitin/plagiarism software,
  dual credit, the 2019–2020 governance crisis) plotted per month.
- Bulk download (JSONL or SQLite, addresses already gone) behind a terms-of-use click.
- Admin view for requests and suppressions.
- Fly.io deploy (one small machine with a volume, as Pinakes), only after Justin approves.
- Final public copy in Justin's voice (`justin-email` / `justin-writing-master`, then the
  extended `no-ai-trace` pass). No CompPile comparisons.
- NACR conversation (Justin's to start).

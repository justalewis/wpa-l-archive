# WPA-L Archive Reader: Research Tools Build Spec

Build plan for adding research tooling to the WPA-L Archive Reader (https://wpa-l-archive.fly.dev/). Written to hand to Claude Code. Items are numbered 1–11 in rough dependency order; the ethics layer at the end applies to 1, 7, and 8 and should be built alongside them.

---

## 0. Before writing code

Claude Code should start by reading the repo and confirming:

- The stack (framework, templating, database or index, search engine, deploy config for Fly.io).
- How messages, threads, and posters are stored, and what the stable IDs are (message pages use 12-hex IDs like `/m/b18b876ce42a`; threads use integers like `/thread/13478`).
- How the build pipeline runs (the About page says it builds from 341 monthly LISTSERV files).
- How opt-out requests are stored and applied.

Then propose a schema migration plan before changing anything. Every new feature must be regenerable from the build pipeline, not hand-maintained.

## Current state (as of 2026-10-27)

**Corpus.** 186,436 messages, January 1993 to May 2021, from the Internet Archive deposit "WPA-L Archive 1993–2021 Complete Uncompressed." Yearly volume ranges from ~1,054 (2021, partial) and ~1,259 (1993) to a peak of 10,564 (2013), with steep decline from 2017 onward (5,735 → 5,098 → 3,432 → 2,678).

**Routes.**

| Route | What it does |
|---|---|
| `/` | Year × month volume table |
| `/browse/{year}`, `/browse/{year}/{month}` | Threads listed by first message that month, with poster, date, count |
| `/thread/{id}` | Full thread, messages anchored by ID |
| `/m/{id}` | Single message, "In reply to," collapsible quoted text |
| `/search` | `q` (words or "phrase"), `from` (name contains), `start`/`end` year; results ranked by relevance with a matches-per-year histogram |
| `/timeline` | Monthly bar chart linking to months |
| `/about` | Draft working description |
| `/opt-out` | Withdrawal requests |

No person pages, no API, no exports.

**Processing already done.** Email addresses stripped from headers, bodies, signatures, quoted lines. Attachments dropped (types listed). Names as posted; address-only From lines get a name from the same address's other posts, else "Unnamed poster." Site is noindexed.

**Threading.** From about 2010, Message-ID headers link replies to parents. Before that, grouping is by normalized subject (ignoring Re:/Fwd:) within a 30-day window, with parents inferred from "X wrote:" attributions. Known failure modes: long subjects get split; generic subjects ("job posting," "textbooks") get merged.

---

## Design: adopt the Pinakes "Alexandrian Suite"

The reader should look like a sibling of Pinakes (pinakes.wacclearinghouse.org). The design system lives in the Pinakes repo, `justalewis/Rhet-Comp-Index`. Claude Code should clone it read-only and copy from it; do not edit Pinakes from this project.

**Source files in the Pinakes repo**

| File | Role | How to bring it over |
|---|---|---|
| `static/tokens.css` | Palette, type, spacing, dark mode ("black-figure"), papyrus grain | Copy **verbatim**. Its header says it is itself a verbatim copy of `eunomiaslabors.xyz/static/tokens.css`; keep that chain intact and add a line noting the copy date and Pinakes commit. |
| `static/fonts.css` + `static/fonts/*.woff2` + `static/fonts/README.txt` | Self-hosted OFL faces: GFS Didot, EB Garamond, Literata, Atkinson Hyperlegible (latin, latin-ext, greek, greek-ext) | Copy verbatim with the license README. Pinakes ships all 23 faces (685 KB); the reader can drop the Greek subsets if no page sets Greek, but unicode-range means unused faces cost nothing. |
| `static/pinakes.css` | Components built on the tokens | **Do not copy whole** (2,546 lines, much of it Pinakes-specific). Lift the sections listed below into a new `wpa.css`. |
| `templates/_alexandrian_head.html` | Stylesheet order, `theme-color` metas, pre-paint theme script | Port to the reader's templating. Change the localStorage key from `pinakes-figure` to `wpa-figure`. |
| `templates/base-alexandrian.html` | Shell: `.layout` → `.capsa` sidebar + `.column` main, skip link, figure toggle | Use as the model for the reader's base template. |
| `templates/_icons_core.html`, `design_icons.html` | SVG sprite icon system | Reuse the **mechanics and drawing rules only**. The reader gets its own sprite with a rhet/comp vocabulary; see "The WPA-L icon sprite" below. Do not copy Pinakes symbols. |

### The WPA-L icon sprite

The reader keeps the Pinakes icon *grammar* but swaps the *vocabulary*. Pinakes draws from the Alexandrian critical apparatus and library objects (π, asteriskos, diple, volumen). The reader draws from the composition classroom, the writing program office, and the listserv's own apparatus: the quoted-reply chevron, the envelope, the quotation rule, the paperclip. Books, pencils, author profiles, syllabi, the red pen, the commonplace book, the conference lectern.

**Starter sprite provided.** `icons/_icons_wpa.html` (28 symbols) accompanies this spec, with `icons/preview-red-figure.png` and `icons/preview-black-figure.png` showing each icon at 72px and at 24px in both figures. Claude Code should drop it into the templates folder as a partial, include it once in the base template (as Pinakes includes `_icons_core.html`), and treat it as a first draft to refine, not final art.

**Drawing rules (inherited from Pinakes, unchanged)**

- 24×24 viewBox, content inset to 3–21, nothing on the bleed.
- `fill="none" stroke="currentColor" stroke-width="1.6"`, round caps and joins.
- Exactly **one** accented element per icon, and it carries the meaning (the ribbon on the commonplace book, the byline under the author, the lens on the index card).
- Accents use inline `style="stroke:var(--ic-accent)"` or `style="fill:var(--ic-accent);stroke:none"`, because document CSS does not reach into a `<use>` clone's shadow tree but inherited custom properties do.
- Usage: `<svg class="ic" aria-hidden="true"><use href="#ic-thread"/></svg>` beside a text label. Icon-only controls need an `aria-label`.
- Use `.ic-quiet` in dense lists (29 year rows, long thread lists) to protect the 5% terracotta budget; `.ic-absent` (faience) for empty states and opted-out posters.
- Every symbol gets a one-line Jinja comment naming the object and why it means what it means, as in the Pinakes sprite.

**Inventory and mapping**

| Symbol | Used for | Object and meaning |
|---|---|---|
| `ic-wpa` | Wordmark | The reply mark: a quoted line, then the cursor waiting for your answer. |
| `ic-browse` | Browse years | The stacks: volumes on a shelf, one year per spine. |
| `ic-month` | A month | The syllabus calendar: weeks in rows, one marked. |
| `ic-thread` | Thread | The quoted reply chain: a post, the chevron, the answer indented below. |
| `ic-message` | Message | The letter in its envelope; the flap is the opening. |
| `ic-search` | Search | The research index card under the lens. |
| `ic-timeline` | Timeline | The classroom ruler, with the term marked. |
| `ic-trends` | Term trends | Graph paper and a pencil line rising and falling. |
| `ic-person` | Author profile | Head and shoulders above a byline. |
| `ic-data` | Data and export | The portfolio folder, with the corpus going in to be carried away. |
| `ic-confidence` | Thread confidence | Two leaves and the stitching between them, some of it inferred. |
| `ic-topics` | Topics | The commonplace book: where topoi were kept, with its ribbon. |
| `ic-genre` | Genre | Binder tabs: query, CFP, job, announcement, each filed apart. |
| `ic-network` | Participation | The seminar table: the circle of conversation and its seats. |
| `ic-flashpoint` | Flashpoints | A margin comment with an exclamation: something here got heated. |
| `ic-citation` | Citations | The open book with its bookmark: a work the list talked about. |
| `ic-institution` | Institutions | Old Main: the pediment and columns every campus seems to have. |
| `ic-event` | Events | The conference lectern and its gooseneck microphone. |
| `ic-optout` | Opt out | A letter returned to its sender. |
| `ic-about` | About | The syllabus: a heading, then the policies. |
| `ic-quoted` | Quoted text | The quotation rule in the margin of a reply. |
| `ic-attachment` | Attachment | The paperclip: something was sent with this, and is not kept here. |
| `ic-job` | Job posting | A flyer pinned to the department corkboard. |
| `ic-cfp` | Call for papers | The abstract page, its corner turned to mark the deadline. |
| `ic-tribute` | Tribute | The pen nib, for a colleague remembered in writing. |
| `ic-annotate` | Annotate and code | The pencil, point down, writing in the margin. |
| `ic-method` | Methodology | The rhetorical triangle, with the text at its center. |
| `ic-redpen` | Feedback and flags | The red-pen check in a margin: reviewed, or flagged for review. |

**Where each icon goes**

- Sidebar destinations: `ic-browse` (Browse), `ic-search`, `ic-timeline`, `ic-trends` (item 4), `ic-person` (People, item 1), `ic-data` (Data and export, item 2), `ic-about`, `ic-optout`.
- Tool cards and page heads: `ic-confidence` (3), `ic-topics` (5), `ic-genre` (6), `ic-network` (7), `ic-flashpoint` (8), `ic-citation` (9), `ic-institution` (10), `ic-event` (11), `ic-method` (methodology folds under every tool).
- Genre labels on messages (item 6): `ic-cfp`, `ic-job`, `ic-tribute`, `ic-message` (announcement), `ic-thread` (discussion). Add `query` and `administrative` marks when the classifier lands (suggestions: a question-mark speech bubble; an envelope with a plus/minus).
- Message furniture: `ic-quoted` (quoted-text toggle), `ic-attachment` (attachment types listed), `ic-message` (single-message link).
- Review queues (items 1, 3, 9, 10): `ic-redpen` for flags and reviewed items; `ic-annotate` for researcher coding and notes.

**Open items for review**

- `ic-wpa` (the wordmark) is a quoted-reply chevron with an underscore. It may read as a terminal prompt; alternatives are a chevron inside a speech bubble, or an envelope whose flap is drawn as the `>` mark. The wordmark is the one icon allowed to be large and terracotta throughout (`.ic-wordmark`).
- Check `ic-network` (seminar table) and `ic-confidence` (stitched leaves) at 18px; they are the busiest at small sizes.
- Add new icons only when a new destination or label needs one, and draw them to the same rules. Extend the `/design` page to show the full sprite in both figures, with each icon's name, ID, and meaning, so the set stays one hand as it grows.

**Sections of `pinakes.css` to lift into `wpa.css`**

- Icon system (`.ic`, sizes, `.ic-quiet`, `.ic-absent`)
- Skip link and focus styles
- The shell (`.layout`, `.capsa`, `.column`, `.column-inner`, `.column-inner--read`), wordmark, navigation, capsa foot, figure toggle
- Narrow-viewport rules (capsa lies above the column)
- Column page furniture and tool cards
- Index entries, search and filters, periods, pagination, "nothing found" (these map directly onto month lists and search results)
- Chart colour (`--cat-1` … `--cat-8`, `--cat-other`, `--chart-*`) with its light and dark blocks
- Chart chrome (`.viz`, `.viz-controls`, `.viz-stats`, `.viz-legend`, `.viz-note`, `.viz-tip`, `.viz-empty`)
- Tables inside a panel; methodology fold under each tool

**Rules carried over from the Pinakes comments (keep them)**

- Never hard-code a colour in component CSS; everything resolves from tokens so light and dark both work.
- Terracotta is the only saturated hue outside charts, held under ~5% of any view. Use `.ic-quiet` in dense lists (month lists, long thread lists).
- Faience (`--faience`) marks absence only: e.g. a month with no messages, an opted-out poster, a tool with no data yet.
- Body text in Literata at the `--measure` of 62ch; message bodies and thread pages use the narrow reading column (`.column-inner--read`). UI chrome in Atkinson Hyperlegible; headings in GFS Didot.
- Message bodies, quoted text, and headers in `--f-mono` only where fixed-width matters (raw headers); quoted text should otherwise be Literata in `--dilute` with a left rule.
- Charts: the eight categorical colours in fixed order, never cycled; fold series past eight into `--cat-other`. A legend is mandatory whenever more than one series is drawn (the dark-mode `--cat-6`/`--cat-7` pair needs the secondary encoding). Controls sit in one row above the plot. One tooltip style (`.viz-tip`). This governs items 4, 5, 6, 7, 8, 10, and 11.
- No third-party requests for styles or fonts, so the CSP can stay at `style-src 'self'; font-src 'self'`. Pinakes currently allows `'unsafe-inline'` for styles; the reader should avoid needing it.

**Reader-specific adaptations**

- Wordmark: "WPA-L Archive Reader" with a subtitle (e.g. "The Writing Program Administration list, 1993–2021"), using `ic-wpa` in place of Pinakes' π.
- The capsa holds years (1993–2021) with message totals in place of Pinakes' journal tags, plus links to Search, Timeline, Trends, People, Data, About, Opt out.
- The home volume table becomes a heatmap-style grid using a single-hue sequential ramp built from `--terracotta-wash` → `--terracotta` (not the categorical palette), with counts kept as text for accessibility.
- Search results, month lists, and thread lists use the index entry styles.
- Add a `/design` page (like Pinakes' `design_icons.html`) showing tokens, type, icons, and chart chrome, so later tools stay consistent.

**Done when.** Every page renders on the Suite in both figures; no hard-coded colours remain in reader CSS; the figure toggle persists; the CSP needs no new sources; a side-by-side check against Pinakes shows matching type, colour, and chart chrome. Do this restyle before building items 4–11 so new pages are born in the system.

---

## 1. Poster identity resolver and person pages

**Why.** Disciplinography needs people as units: careers, institutional moves, activity spans, who sustained the list.

**Build.**
- A `posters` table with a canonical ID and a `poster_aliases` table mapping every observed name string to it.
- Name normalization: "Last, First" ↔ "First Last," middle initials, case, diacritics, trailing degrees ("Ph.D."). Use the pre-stripping address (kept only inside the build, never published) as the strongest merge signal; name similarity as secondary.
- Affiliation extraction from signature blocks: detect the signature (after `--`, or the last 2–8 lines before quoted text), pull institution names, and store `(poster_id, institution, first_seen, last_seen)`.
- A review queue: a CLI or admin page listing low-confidence merges for manual confirmation. Store decisions so rebuilds keep them.
- `/person/{id}` page: name variants, first and last post, posts per year sparkline, threads started vs. replies, affiliations over time, top co-participants (subject to ethics thresholds).
- Link poster names everywhere they appear (month lists, threads, search results).

**Done when.** Rebuild produces stable person IDs; known variant pairs merge; review decisions persist; opted-out posters get no person page.

## 2. Corpus builder and export

**Why.** Researchers need to cite, share, and analyze defined subsets. This makes every other tool citable.

**Build.**
- "Save this search" turns any search (q, from, years, plus future filters like genre or topic) into a named, permanent corpus with a stable URL and a frozen snapshot of matching IDs and the build date.
- Export formats: CSV and JSONL with `message_id`, `thread_id`, `poster_id`, poster name, date (UTC), subject, body, quoted-text-stripped body, permalink. Optional pseudonymized variant (see ethics layer).
- Citation strings per message in MLA 9 and APA 7 for listserv posts, with a copy button on each message page.
- Read-only JSON API: `/api/messages/{id}`, `/api/threads/{id}`, `/api/search?…`, `/api/corpus/{id}`, `/api/posters/{id}`. Paginated, rate-limited, versioned (`/api/v1/`).
- A short `/data` page documenting fields, build provenance, and how to cite the archive itself.

**Done when.** A saved corpus URL returns identical IDs after a rebuild; exports round-trip; opted-out messages never appear in exports or API.

## 3. Thread confidence layer

**Why.** Pre-2010 threads are reconstructions. Researchers need to know which groupings to trust.

**Build.**
- Store a `link_method` per reply edge: `message-id`, `wrote-line`, `subject-window`, `unlinked`.
- Thread-level confidence score from the share of header-linked vs. inferred edges, subject genericness (frequency of the normalized subject across the corpus), and time spread.
- Show a small badge on thread pages ("Rebuilt from subject lines; medium confidence") with a link to an explanation.
- "Flag this thread" for suspected bad split/merge; flags go to an admin queue; accepted corrections persist as overrides through rebuilds.
- Optional: suggest candidate merges for split threads (same normalized subject, adjacent windows, overlapping posters).

**Done when.** Every thread shows its method and confidence; overrides survive rebuild.

## 4. Normalized term trajectories

**Why.** The current search histogram shows raw counts, but annual volume varies roughly tenfold. Raw counts mostly track list activity, not attention to a topic. This is the most important interpretive fix.

**Build.**
- `/trends?terms=assessment,outcomes,portfolio` page.
- For each term or phrase: matches per 1,000 messages, by month and by year, plus a toggle for raw counts and for "messages containing" vs. "occurrences."
- Option to exclude quoted text (otherwise heavy quoting in replies inflates counts).
- Multiple terms on one chart, with a smoothing control (none, 3-month, 12-month).
- Click a point to open the matching messages for that period.
- Add the normalized view to the existing search histogram as a toggle.
- Precompute monthly denominators at build time.

**Done when.** Charts render for up to ~6 terms; quoted-text exclusion works; point-click lands on the right results.

## 5. Topic model over time

**Why.** Shows what the list was about in each era without a researcher having to guess the search terms.

**Build.**
- Offline pipeline (Python): strip quoted text, signatures, and boilerplate; embed thread-level documents (thread is a better unit than single messages); fit BERTopic with dynamic topics over time. Consider STM as a comparison if covariates (era, genre) matter.
- Store topic IDs, top terms, representative threads, and per-month prevalence.
- Hand-label topics through a simple admin page; keep labels across refits by matching on representative documents.
- `/topics` page: stacked or small-multiple prevalence chart; each topic page lists representative threads and its trajectory.
- Record model version, parameters, and random seed on the page so results are reproducible and citable.

**Done when.** Topic pages render with labels, trajectories, and threads; the model run is reproducible from a pinned config.

## 6. Genre classifier

**Why.** WPA-L changed function over time, from discussion toward announcements. Genre makes that visible, and job ads alone become a labor-market dataset.

**Build.**
- Labels: `query`, `reply/discussion`, `debate`, `CFP`, `job ad`, `announcement` (publications, events), `obituary/tribute`, `administrative` (subscribe/unsubscribe, list business), `other`.
- Start with rules (subject tags like "CFP," "Job," "position"; keywords) to bootstrap; hand-label a stratified sample of ~1,500 across eras; train a classifier (or use an LLM labeler with a fixed prompt and spot-check). Report per-class precision and recall on a held-out set on the `/data` page.
- Genre becomes a search filter and an export field.
- `/genres` page: share of each genre per year.
- Job-ad extraction: institution, position title, rank/type (WPA, WC director, tenure-line, NTT), posting date. `/jobs` page with counts by year and institution type.

**Done when.** Genre filter works in search; per-year genre chart renders; evaluation metrics are published.

## 7. Participation and network views

**Why.** Who held the conversation, who joined, who stayed, and how concentrated the voice of the list was.

**Build.**
- Reply edges (poster → poster) from thread structure, with each edge's `link_method` so pre-2010 inferred edges can be filtered.
- Per era (configurable, e.g. 5-year windows): network graph of the top N posters, core/periphery measure, degree and betweenness.
- Concentration: share of posts from the top 1% and top 10% of posters per year; Gini coefficient.
- Newcomer cohorts: posters grouped by year of first post, retention curves (share still posting 1, 3, 5 years later).
- Thread-starter vs. replier ratio per poster and per era.
- All individual-level views follow the ethics thresholds below.

**Done when.** Era networks render; concentration and cohort charts are computed at build time; opted-out posters appear only as anonymous nodes or are excluded.

## 8. Flashpoint detector

**Why.** Frames the list's turning points, including the 2018–2019 conflicts and the decline visible in monthly counts, as researchable events.

**Build.**
- Per-thread metrics: message count, distinct posters, duration, reply depth, posts per hour at peak, share of posters who rarely post.
- Corpus-level signals per week: volume anomaly against a rolling baseline, spikes in administrative messages (unsubscribe, "leaving the list," moderation notices), spikes in meta-talk about the list itself ("this list," "listserv," "moderator," "civility").
- Rank weeks and threads by a combined score; `/flashpoints` page listing them with a small trace chart and links.
- Neutral, descriptive labeling only ("high-volume, many-participant thread"), no evaluative labels on people.

**Done when.** The ranked list surfaces known high-activity episodes; every flagged item links to its threads; ethics thresholds apply.

## 9. Citation extractor linked to Pinakes

**Why.** Compares who and what the field cites informally on the list with formal citation patterns in journals indexed by Pinakes (pinakes.xyz).

**Build.**
- Extract references from message bodies (excluding quoted text): author–title patterns, "(Author, Year)," italic or quoted titles, DOIs, URLs to journals and publishers, and bare scholar names.
- Use a named-entity step plus fuzzy matching against Pinakes author and work records. Store matches with a confidence score; leave low-confidence matches in a review queue.
- Message pages show linked works; work pages show "Mentioned on WPA-L N times, first in YEAR."
- `/cited` page: most-mentioned works and scholars per era; a comparison view of list mentions vs. Pinakes citation counts for the same works.
- Export of the mention table for research use.
- Coordinate the matching interface with the Pinakes data model (shared IDs, an API call or a periodic data dump).

**Done when.** A sample of high-confidence matches checks out by hand; work pages show list mention counts.

## 10. Institution mapper

**Why.** Shows which institutions the list represented over time, including how present two-year colleges were relative to R1s.

**Build.**
- Uses affiliations from item 1 plus institution names in message bodies.
- Normalize names to IPEDS unit IDs; attach Carnegie classification, state, and lat/long.
- `/institutions` page: map by era, share of posts by Carnegie type per year, top institutions per era.
- Institution pages: posters affiliated over time, post counts, job ads posted (from item 6).
- Keep unmatched names visible in a review list rather than silently dropping them.

**Done when.** Carnegie-type share chart renders; spot-checked matches are correct; unmatched names are reviewable.

## 11. Event overlay

**Why.** Reads the list's rhythms against the field's calendar.

**Build.**
- An `events` table: date or date range, label, category (CCCC, CWPA conference, position statement, major news or policy event, list administration such as host or moderation changes), source URL.
- Seed it by hand from a YAML or CSV file in the repo so it's easy to extend and review.
- Toggleable overlay on `/timeline`, `/trends`, `/topics`, and `/flashpoints` charts.
- Seasonality view: average volume by month of year and by week relative to CCCC.

**Done when.** Events render on all charts; adding an event is a one-line file change plus rebuild.

---

## Ethics layer (build with 1, 7, and 8)

These are living colleagues, and the list was a semi-public professional space. This layer is part of the research design.

- **Opt-out propagation.** Opted-out posters are removed from person pages, exports, API, network node labels, citations-by-poster, and institution rosters. They may count toward anonymous aggregates only, or be excluded entirely (decide and document).
- **Minimum-count thresholds.** Network and person-level views only show individuals with at least N posts (suggest 25) and only show edges with at least M interactions (suggest 3).
- **Pseudonymized exports.** Stable per-export pseudonyms for IRB-reviewed work; original names only in the standard export.
- **No evaluative labels on people.** Flashpoints and networks describe activity, not character.
- **Keep the noindex.** Extend it to all new pages and the API.
- **Documentation.** An `/ethics` page explaining what is shown, thresholds, opt-out effects, and how to request changes.

---

## Suggested sequence

0. The Pinakes design restyle, so every new page starts in the system.
1. Item 2 (exports and API) and the ethics layer's opt-out propagation.
2. Item 4 (normalized trends), since it fixes the existing search histogram.
3. Item 1 (identity), then 3 (thread confidence).
4. Item 6 (genre), which feeds 7, 8, and 10.
5. Items 9, 5, 7, 8, 10, 11 as interest and time allow.

Each item should land as its own branch with tests for the build pipeline and a short note added to `/about` or `/data`.

# social-video-tag-analyzer — INSTRUCTIONS

Stage 3+4 of the pipeline that starts with `competitor-brand-discovery` and
`social-best-video-scraper`. Tag taxonomy finalized 2026-10-16 after the user reviewed every
category across two rounds (`lidia_notes/社媒达人视频分析skill调研.md` Stage 4 carries the full
revision history — read it before redesigning any tag, do not rediscuss settled decisions).
`reference/tag_definitions.json` is the single source of truth for both the Gemini prompt
(`scripts/build_prompt.py`) and the glossary tab (`scripts/row_builder.py`) — edit the
taxonomy there, never hand-maintain the prompt or glossary content separately.

## Responsibility

Given a `social-best-video-scraper` manifest (or an equivalent list of brand/platform/video
entries with local file paths), analyze every successfully-downloaded video with Gemini
against the finalized tag taxonomy, and produce a 3-tab report: analysis results, a glossary
explaining what every tag means (rewritten every run, not a one-time document), and a
frequency-based "similarity analysis" findings table (see "Frequency-count findings (Tab 3)"
below).

This skill does **not**:
- discover competitor brands (→ `competitor-brand-discovery`)
- search or download videos from Instagram/Facebook (→ `social-best-video-scraper`)
- create a brand-new Google Sheet from scratch — see "Output format choice" below for why

## Input Contract

| Input | Required | Notes |
|---|---|---|
| Manifest path | Yes | `social-best-video-scraper`'s `manifest.json`, or an equivalent structure |
| Videos root directory | Optional | Default: the manifest's own directory. Where `local_file` paths inside the manifest resolve from. |
| Output format | Optional | `xlsx` (default) or `gsheet` — see below |
| Output path (xlsx) | Optional | Default: `<manifest dir>/video_analysis.xlsx` |
| Spreadsheet ID (gsheet) | Required when `--output-format gsheet` | Must be an **existing** sheet the caller already has edit access to |
| `--target-country` | Optional | Overrides the manifest's own `target_country` field (written by `social-best-video-scraper --target-country`) if both are given. Enables the `country_match` assessment — see "Country/market scoping" below. |
| Concurrency | Optional | Default 3 (parallel Gemini calls via `ThreadPoolExecutor`) |

## Output format choice — read this before changing anything about auth

**Default is `xlsx` — a local file, zero Google auth needed.** This is deliberate, not a
placeholder: this skill is meant to be usable by many different people across different
machines/environments (including a plain Codex session with no GCE instance, no ADC, no
shared infrastructure at all), and a local file is the only output that works identically
everywhere with no setup.

**`gsheet` writes into an EXISTING spreadsheet the caller already has access to — it never
creates a new one.** This is not a missing feature, it's a deliberate boundary, for two
verified reasons (2026-10-16):

1. **Creating a new spreadsheet needs the `drive` scope, and personal-account requests for
   that scope got blocked live** by this org's Google Workspace policy against unverified
   apps requesting sensitive scopes (`gcloud auth application-default login
   --scopes=...drive` → "This app is blocked" on this exact account, this exact day — not a
   hypothetical). The only paths around it are a GCE instance service account (works in the
   anylive bot's own production environment, not portable elsewhere) or a dedicated
   service-account key file — which leads to the second reason:
2. **A shared skill has no safe shared secret to distribute.** Baking a service-account key
   into a skill meant for many people to run is a real security problem (whoever gets the
   skill gets the key, no per-person audit trail, no access-control granularity). Requiring
   an *existing* sheet the caller already has access to sidesteps this entirely: each person
   authenticates as themselves (see below), and can only write to sheets they already have
   permission for — correct behavior, not a limitation to engineer around.

**`gsheet` auth**: plain `google.auth.default()` (`gsheet_output.py`), scope
`https://www.googleapis.com/auth/spreadsheets` only — never request `drive`. In the anylive
bot's production environment this resolves to the GCE instance service account automatically.
Anywhere else (a developer's laptop, a Codex session), each person needs a **one-time**
local setup:

```
gcloud auth application-default login --scopes="https://www.googleapis.com/auth/spreadsheets"
```

(Must be pasted as one line — a line break before `--scopes` makes the shell treat it as a
separate, invalid command and silently fall back to default scopes, which breaks this path.
Hit live on 2026-10-16.) After that one-time login, the target spreadsheet must be shared
with whatever Google account that login used (Editor access) — same pattern this repo
already documents for the bot's own service account email in `.claude/CLAUDE.md`.

A spreadsheet the current identity can't access raises `SpreadsheetNotAccessible` with an
actionable message (share it and retry) — never a raw stack trace, and never a silent
fallback to creating a new one.

## Gemini video analysis

`scripts/gemini_video_analysis.py` uses `scripts/gemini_auth.build_client()` for auth (ADC →
`GOOGLE_API_KEY` fallback) — a self-contained COPY of `bot/gemini_client.py`'s auth logic kept
local to this skill (not imported across a `sys.path` reach-up into the repo root), so this
skill works standalone when distributed outside anyai-live as a shared skill pack. An earlier
revision imported `bot.gemini_client` directly; that broke the moment the skill was copied
without `bot/` alongside it — caught live 2026-10-19 when a user tested the packaged skill and
Codex correctly flagged the missing import before running anything. See `gemini_auth.py`'s
own docstring if `bot/gemini_client.py`'s auth logic changes and the two need re-syncing.
`gemini_video_analysis.py` adds the video-specific plumbing `gemini_auth.py` doesn't cover:
MIME detection, inline-bytes (Vertex) vs. upload-and-poll (AI Studio), and a **call-time**
fallback to `GOOGLE_API_KEY`
when a Vertex AI call is rejected with a permission error even though ADC successfully built
a Vertex client — this happened live on 2026-10-16 (ADC resolved to a real Vertex project the
account lacks `aiplatform.endpoints.predict` on) and mirrors `avatar-qc`'s own
`_analyze_gemini` fallback, the only other place in this repo that sends video to Gemini.

A single video's analysis failure (bad file, Gemini error, malformed JSON response) is
isolated — it produces a `"failed"` row with the error message, never aborts the batch and
never gets silently dropped.

## Country/market scoping

Neither Apify actor `social-best-video-scraper` uses supports a native country filter
(verified live 2026-10-16 against both actors' real input schemas — see that skill's
INSTRUCTIONS.md). So when a `target_country` flows in (from the manifest, or `--target-country`
overriding it), this skill's Gemini pass is the only point in the whole pipeline that can
actually judge market fit — from the video's own language, on-screen text, and cultural
cues, not from which hashtag happened to surface it.

`build_system_prompt(target_country=...)` conditionally adds a `country_match` instruction
block and an output field (`{"assessment": "likely_match|uncertain|likely_mismatch",
"evidence": "..."}`) only when a country was actually given — the prompt and the output JSON
both omit it entirely otherwise, and `row_builder.py` renders the two "国家匹配" columns blank
in that case (never `"uncertain"` as a default — blank means "not assessed," `uncertain` means
"assessed, evidence was inconclusive," those are different facts).

**This isn't a theoretical safeguard — it already caught something real.** Live-tested
2026-10-16 (CeraVe, IG hashtags `cerave`+`ceraveskincare`, target country "United States"): of
5 "best performing" candidates ranked purely by view count, Gemini flagged **3 of them as
`likely_mismatch`** — Brazilian Portuguese, Italian, and Marathi (Indian) audio, including the
single highest-viewed candidate in the whole batch (8.2M views). The Marathi one carried
English on-screen subtitles but Gemini still correctly flagged it, keying off the actual
spoken language rather than visible text — evidence the check isn't just pattern-matching on
caption language. Without this step, that 8.2M-view Brazilian video would have been reported
as the brand's best-performing *US* content with nothing to say otherwise.

## Performance metrics in the output

`row_builder.py` surfaces **Views / Likes / Comments / Shares** as four separate columns
(right after Metric Type/Metric Value, before URL) — pulled straight from
`social-best-video-scraper`'s manifest (`rank_and_select.py` already spreads the full
normalized row, which carries these fields, into each kept result; this skill just needed to
expose them as columns instead of leaving them unsurfaced). These are raw, platform-reported
numbers, not Gemini's doing — they're populated even when the Gemini analysis itself failed,
same as Brand/Platform/URL.

**Blank, never `"0"`, when a platform doesn't expose a metric** — Instagram never exposes
shares; Facebook rarely exposes views (see `social-best-video-scraper`'s INSTRUCTIONS.md). A
`0` would misreport "we observed zero" when the truth is "this platform never told us." No
glossary entry needed for these four — they're plain factual counts, not a judgment call,
consistent with how Brand/Platform/Metric Type aren't glossed either.

**Scope note**: this is deliberately *only* the raw public engagement numbers — not the
deeper first-party analytics (completion rate, watch-time, audience demographics, traffic
source) a brand can see for its own account, and not e-commerce conversion data (GMV, SKU
orders) which lives in a seller's own Shopee/TikTok Shop backend. Both of those are
structurally unreachable for a competitor's content via any public scraping method, not a gap
in this skill — see `lidia_notes/社媒达人视频分析skill调研.md` "Stage 5" for the fuller writeup
of the correlation-analysis idea this scoping conversation led to, now built as
`frequency_analysis.py` (see "Frequency-count findings (Tab 3)" below).

## Tag taxonomy — Stage 4b additions (2026-10-16)

After reviewing the Sofy-TH-TikTok correlation-analysis reference sheet, the user asked for
additional tag categories covering Hook细节/人物出演/商品露出/诉求信息/CTA/战略框架, explicitly
**excluding** 画角剪辑 (camera framing/movement/cuts), 音声字幕 (sound/captions), and the
品类特有 taboo-specific category from that reference — those three are deliberately absent, not
an oversight. `reference/tag_definitions.json`'s `_meta` block and category list document
exactly what was added; see that file before redesigning.

Two new `selection_type` shapes were introduced to hold these additions without exploding the
category count: `boolean_group` (`content_flags` — 8 independent yes/no sub-fields) and
`numeric_group` (`quantitative_observations` — 6 independent numeric sub-fields, Gemini is
told to give a best estimate rather than `null` since these feed Tab 3's bucketed counts). The
remaining additions (`talent_gender`, `talent_age_appearance`, `talent_expertise_signal`,
`cta_type`, `job_dimension`, `cep_occasion_type`) reuse the existing `multi_closed`/`multi_open`
shapes — no new code needed for those.

## Frequency-count findings (Tab 3)

**Methodology — the user's own simplified redesign, not the "group average vs. baseline"
approach originally proposed and rejected as too complicated.** `scripts/frequency_analysis.py`
selects, **independently**, the top `TOP_N_PER_BRAND` (10) videos per brand by Views and the
top 10 per brand by Engagement — a video missing the relevant metric is excluded from that
ranking, not treated as zero — then pools each selection across all brands (1 brand → 10
videos per metric; 5 competitors → 50, matching the user's own framing). Within each pooled
set, it counts how often every tag value (or, for numeric fields, every bucketed range —
`_NUMERIC_BUCKETERS`, a documented judgment call with no empirical calibration yet) appears,
and keeps the top `MAX_VALUES_PER_FACTOR` (3) most frequent per factor category. This is why
`social-best-video-scraper`'s kept-per-platform default was widened from 10 to 20 (2026-10-16,
"方案A" the user chose over skipping the Engagement table) — the Views-best and
Engagement-best selections can be largely different videos, and both need to be drawable from
one tagged batch.

`scripts/reason_generator.py` then sends the findings to Gemini in chunks of `BATCH_SIZE` (25,
concurrent) — not one call per finding, and not one call for the entire list either — to
generate the `原因分析` one-sentence explanation column. This is explicitly required to be
LLM-generated, never a hardcoded template, so it can account for context (sample size relative
to pool size, target country) a template can't. Batching (not a single giant call) is itself a
fix for something hit live 2026-10-16: a real run can easily produce 100+ findings (categories
× up to `MAX_VALUES_PER_FACTOR` × 2 metrics), and asking Gemini for 100+ one-sentence strings
in one JSON array reliably got truncated mid-string at the output token limit. A batch that
fails (truncation, malformed JSON, wrong array length) degrades to an honest fallback string
on every row IN THAT BATCH ONLY — never a crash, never a fabricated-sounding explanation, and
every other batch's real reasons are unaffected.

`row_builder.build_findings_rows()` formats the result into the exact 5-column table the
business asked for — `指标 | 影响因素 | 具体标签or数值 | 原因分析 | 样本量` — already
sorted/grouped so every Views finding precedes every Engagement finding; this function only
formats, it never re-sorts or re-filters.

## Decision Principles

**The glossary tab is not optional and not one-time.** Every output — xlsx or gsheet —
carries both tabs, every run. A business user who opens any single report must be able to
understand what a tag means without hunting down a separate document. Do not add a "skip
glossary" flag or treat a pre-existing glossary tab as already-sufficient — always rewrite it
from `tag_definitions.json`, so it can never drift from what the prompt actually asked Gemini
to tag.

**`row_builder.py` is the only place result rows and glossary rows get built.** Both output
backends (`xlsx_output.py`, `gsheet_output.py`) call into it rather than building rows
themselves — this is what keeps the Excel output and the Google Sheet output from silently
diverging in content or column order over time.

**A video that wasn't downloaded gets a row explaining why, not silence.** `download_status
!= "ok"` in the manifest produces a result row with every tag column blank and the failure
reason in the last column — this is itself useful information (which candidates couldn't be
analyzed and why), not noise to filter out.

**`rtb_type` candidates prefixed `NEW:`** are Gemini introducing a value outside the given
list because the evidence didn't fit any existing one (see `build_prompt.py`'s explicit
instruction for this) — surface these to the user as "worth reviewing," not as a parsing
quirk to clean up or silently drop.

**Facebook `metric_type: "engagement_score"` rows are an estimate, not a views count** —
this is carried through unchanged from `social-best-video-scraper`'s manifest; never relabel
it as `views` anywhere in this skill's output.

## Self-Check Before Completion

- [ ] Every analyzed video's row reflects the actual Gemini response — no field invented
      when the response was missing or malformed (that's a `"failed"` row instead)
- [ ] Views/Likes/Comments/Shares are populated straight from the manifest's `video_result`
      (never from Gemini, never 0 when a platform simply didn't expose the field) — and are
      present even on a `"failed"` row, since they don't depend on Gemini succeeding
- [ ] When a target country was given, `country_match` was requested and its two columns are
      populated per video; when none was given, those columns are blank (not "uncertain")
- [ ] The glossary tab is present and current in every output, xlsx or gsheet
- [ ] Tab 3 (findings) is present in every output even when empty (no videos had both a usable
      metric and a successful analysis) — header-only, never a missing/skipped tab
- [ ] Every findings row's `原因分析` came from the batched Gemini call, not a hardcoded string
      — a Gemini failure there produces the same honest fallback text on every row, not a crash
- [ ] A `gsheet` run that hit `SpreadsheetNotAccessible` reported it actionably, never crashed
      raw or silently fell back to creating a new spreadsheet
- [ ] `--output-format gsheet` without `--spreadsheet-id` was rejected before any API call,
      not attempted and failed later
- [ ] No `drive` scope anywhere in this skill's own code — only `spreadsheets`

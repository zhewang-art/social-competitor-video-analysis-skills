# social-video-analysis-pipeline — INSTRUCTIONS

A **Chain Skill** (see `.claude/skills/skill-creator/knowledge/skill-taxonomy.md`) — it owns
no domain processing of its own. Every real operation (web search, Apify scraping, yt-dlp
download, Gemini tagging, correlation analysis) lives in one of three Primitive Skills this
one orchestrates:

1. `competitor-brand-discovery` — brand name → competitor brand name list
2. `social-best-video-scraper` — brand/account/URL list → best-performing videos, downloaded
3. `social-video-tag-analyzer` — downloaded videos → tag analysis + correlation report

This skill's only job is deciding **which of these three to run, in what order, with what
arguments** — based on what the user actually provided. Never reimplement a primitive's logic
here; if a step needs real processing, that processing belongs in the primitive, not in prose
or a script in this directory.

## Responsibility

Given a request to analyze competitor/brand video content, determine the correct **entry
point** into the three-stage pipeline (not every request needs all three stages run in full),
assemble each stage's input from the previous stage's output (and from whatever the user
supplied directly), and hand the final artifact back to the user. This skill does not decide
*how* to discover competitors, *how* to scrape videos, or *how* to tag/analyze them — those
decisions belong entirely to the primitive being invoked.

## The four entry points

Before doing anything, classify what the user gave you. These are not mutually exclusive
**across brands in one request** — see "Mixed-mode requests" below — but for any ONE named
brand/account/set of links, exactly one applies:

| # | What the user gave you | Starts at | `social-best-video-scraper` mode |
|---|---|---|---|
| A | A brand name, asking to find/analyze its **competitors** | Stage 1 | `search` (hashtags/queries derived from Stage 1's output) |
| B | A list of brand/competitor names **already decided** by the user — no "find competitors" ask | Stage 2 | `search` (hashtags/queries resolved directly from the names given) |
| C | Specific **video URLs** the user already has | Stage 2 | `direct_url` (`instagram_urls`/`facebook_urls`) |
| D | Specific **account/page URLs** (a KOL, a known competitor's account) | Stage 2 | `account` (`instagram_accounts`/`facebook_pages`) |

**If it's not clear which of these the user means — ASK, don't guess.** The costliest mistake
here is silently running Entry Point A (competitor discovery) when the user actually meant B
(they already named the exact brands they want analyzed) — it produces a plausible-looking but
wrong candidate set and wastes the rest of the pipeline analyzing the wrong accounts. A single
clarifying question ("这几个品牌本身就是你想分析的对象，还是要我先帮你找它们的竞品？") resolves
this in one round; this matches the repo's general CLARIFY-before-EXECUTE discipline
(`.claude/CLAUDE.md` Phase State Machine) and applies here regardless of which agent runtime
is driving this session.

## Step sequencing per entry point

### Entry Point A — find competitors first

1. **Run `competitor-brand-discovery`** with the brand name (+ country/category if given).
   Its own contract already says its output is "a candidate list for human confirmation
   before it is handed to `social-best-video-scraper`" — **this skill must honor that: show
   the candidate list to the user and get their go-ahead (or edits) before proceeding to
   Stage 2.** Do not auto-chain Stage 1 → Stage 2 without this checkpoint; skipping it is the
   single most likely way this pipeline produces a report nobody asked for.
2. Once confirmed, build `social-best-video-scraper`'s `--brands-json` — one entry per
   confirmed competitor, `hashtags`/`queries` derived the same way `competitor-brand-discovery`
   already recommends (it typically supplies a derived hashtag per competitor; reuse it rather
   than re-deriving).
3. Continue with "Stage 2 → Stage 3 handoff" below.

### Entry Point B — brand/competitor list already decided

1. Skip Stage 1 entirely.
2. Build `--brands-json` directly from the given names: `{"brand": name}` per entry, adding
   `hashtags`/`queries` only if the user supplied them — otherwise let
   `social-best-video-scraper`'s own fallback derivation handle it (see that skill's
   INSTRUCTIONS.md "Input Contract").
3. Continue with "Stage 2 → Stage 3 handoff" below.

### Entry Point C — specific video links

1. Skip Stage 1 entirely.
2. Build `--brands-json` with `instagram_urls`/`facebook_urls` populated per brand/account
   grouping the user implied (if the user gave one flat list of links with no grouping, ask
   how they want them grouped into "brands" for the report — or, if a single ad-hoc analysis
   with no real brand distinction is clearly what's wanted, use one placeholder brand name
   and say so explicitly in the handoff, never silently invent a brand name that looks
   official).
3. Continue with "Stage 2 → Stage 3 handoff" below — but note: direct URL mode does NOT apply
   `--per-platform-limit` truncation or `--months-back` filtering (see that skill's
   INSTRUCTIONS.md "Direct URL input mode") — every link the user gave is kept and analyzed,
   by design.

### Entry Point D — specific account/page links

1. Skip Stage 1 entirely.
2. Build `--brands-json` with `instagram_accounts`/`facebook_pages` populated per brand — that
   account's OWN best-performing content. If the user instead wants content OTHER accounts
   posted that tagged/mentioned this one (an account's "Tagged" tab — e.g. "不是这个达人自己的
   reels，而是tagged这个页面里的视频"), use `instagram_tagged_accounts` instead (IG only, no
   verified Facebook equivalent) — these two can be combined on the same brand entry when the
   user wants both.
3. Continue with "Stage 2 → Stage 3 handoff" below — this mode DOES apply
   `--per-platform-limit` and `--months-back` (it's a discovery step from that account's feed,
   not a "keep everything named" step — see that skill's INSTRUCTIONS.md "Account input
   mode").

### Mixed-mode requests

A single run can combine entry points **per brand entry** — `social-best-video-scraper`'s own
`--brands-json` schema already supports this natively (each entry independently carries
whichever of `hashtags`/`queries`/`instagram_urls`/`facebook_urls`/`instagram_accounts`/
`facebook_pages` applies to it; see that skill's INSTRUCTIONS.md "Input Contract" for the
priority order when more than one is set on the same entry). Example: the user names two
competitors to discover-and-search (Entry Point A/B) while also pasting three specific links
for a KOL they already know about (Entry Point C) — assemble ONE `--brands-json` array mixing
both shapes rather than running the scraper twice. Do not force every brand in a request into
the same mode just because most of them share one.

## Stage 2 → Stage 3 handoff

`social-best-video-scraper` writes `manifest.json` under its `--out-dir`. Before invoking
`social-video-tag-analyzer` with `--manifest <path>`:

- **Check for an `APIFY_APPROVAL_REQUIRED` sentinel** in Stage 2's output — if present, STOP,
  surface it verbatim, and do not proceed to Stage 3 until it's resolved (this is Stage 2's
  own existing contract; this skill does not add a second approval layer on top of it).
- **Skim the manifest for zero-result brands** (`video_candidates: 0`) — tell the user which
  brands/platforms came back empty before spending Gemini cost analyzing the rest; don't
  silently drop them or silently proceed as if everything found something.
- **No separate manual gate is required between Stage 2 and Stage 3 by default** — Stage 3's
  own cost (Gemini tagging) is typically modest relative to the whole pipeline and that
  primitive has its own per-video failure isolation. For an unusually large batch (many
  brands × high `--per-platform-limit`), mention the video count to the user before running
  Stage 3 as a courtesy, not as a hard requirement.
- Decide `--output-format` (`xlsx` default, `gsheet` opt-in — see that skill's INSTRUCTIONS.md
  "Output format choice") the same way you would if calling that skill directly; this chain
  skill has no opinion of its own on this choice.

## What this skill does NOT decide

- The tag taxonomy, the correlation/findings methodology, and the output report shape are
  entirely owned by `social-video-tag-analyzer` — never restate or second-guess them here.
- Which Apify actor or data source to use, how gaps get merged/explained, recency scoping —
  entirely owned by `social-best-video-scraper` — never restate or second-guess them here.
- How competitors get found (search strategy, confidence levels, escalation) — entirely owned
  by `competitor-brand-discovery`.

If a request seems to need a NEW capability none of the three primitives has (e.g. a brand new
discovery mode, a new tag category, a new output shape), that is a primitive-skill change, not
something to patch around inside this chain skill's prose.

## Self-Check Before Completion

- [ ] The entry point classification was either unambiguous from the request, or the user was
      asked — never silently assumed when genuinely unclear
- [ ] Stage 1's candidate competitor list was shown to the user and confirmed before Stage 2
      ran, whenever Entry Point A was used
- [ ] Mixed-mode requests produced ONE assembled `--brands-json`, not multiple separate
      scraper runs
- [ ] Any `APIFY_APPROVAL_REQUIRED` sentinel from Stage 2 was surfaced verbatim and halted the
      run — never bypassed to reach Stage 3 faster
- [ ] Zero-result brands/platforms from Stage 2 were reported to the user, not silently
      dropped from the Stage 3 handoff
- [ ] No step reimplemented, duplicated, or second-guessed a primitive's own internal logic

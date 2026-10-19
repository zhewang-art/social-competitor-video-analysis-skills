# competitor-brand-discovery — INSTRUCTIONS

Based on this project's own research (`lidia_notes/社媒达人视频分析skill调研.md`, Stage 1): structured
competitor databases (SimilarWeb, G2/Capterra, Crunchbase, data.ai) were evaluated and rejected —
they either charge $500–5000+/month, or only cover SaaS/App/B2B categories that don't fit this
repo's actual customer base (Southeast Asian FMCG/beauty/mother-baby consumer brands on
Shopee/TikTok/social media). Open-source "competitor discovery" GitHub projects produce deep
per-competitor analysis reports, not a search-ready brand name list — wrong output shape for this
need. The only approach that fits is web-search-grounded reasoning, which this skill owns.

## Responsibility

Given one brand name, produce a list of its direct competitor brands at a granularity that is
immediately usable as an Instagram/Facebook search term — a brand name or close variant, not an
industry category, not a market report. This is the first stage of a longer pipeline
(brand → competitors → best-performing videos per competitor → content analysis); this skill owns
only the first arrow.

This skill does **not**:
- search social media or fetch/rank/download videos (→ `social-best-video-scraper`)
- produce a full competitive positioning / whitespace analysis
- guarantee completeness — a brand's full competitive set is often larger than what a few search
  queries surface, especially for long-tail regional brands; this skill finds a reasonable,
  evidence-grounded candidate set for a human to confirm, not an exhaustive market map

## Input Contract

| Input | Required | Notes |
|---|---|---|
| Brand name | Yes | |
| Country/region | Optional (recommended) | Same brand name competes against very different sets in different markets (e.g. a Thai skincare brand's competitors in TH are not its competitors in VN), so suggest supplying it. But it is a genuine option, not a soft-required field: when the user declines or doesn't care, run an unrestricted global search instead of blocking on it or silently picking a country. See "Scoped vs. global search" below — the two modes use a different inclusion rule, not just a different query. |
| Category/product hint | Optional | Skip the search's own category-identification step when already known (e.g. "sunscreen", "baby wipes") — saves a search round and reduces ambiguity for multi-category brands. |
| Desired competitor count | Optional | Default **5**. State this default to the user up front, before searching (see "Announce the default before searching" below) — not just after the fact. Honor whatever the user explicitly asks for instead (the user may say "give me 10" or "just the top 3") — this default is a starting point, not a cap to defend. |

## Output / Handoff Contract

A markdown list (or a JSON array when the caller needs it machine-readable, e.g. piping straight
into `social-best-video-scraper`'s `--brands-json`), one entry per competitor:

```json
[
  {"brand": "Competitor A", "hashtag": "competitora", "rationale": "Same category (sunscreen), same price tier, found via <query/source>", "confidence": "cross_validated"},
  {"brand": "Competitor B", "hashtag": "competitorb", "rationale": "...", "confidence": "single_mention"}
]
```

- `rationale` must name what made this a competitor — same category, same price tier, same
  target demographic, appeared in the same "alternatives to X" search result, etc. A rationale
  that only restates "it's a competitor" with no evidence trail is not acceptable — the user needs
  to be able to sanity-check the list before it drives paid API calls downstream.
- `confidence` is `"cross_validated"` (appeared consistently across independent searches) or
  `"single_mention"` (found once, not independently confirmed — only offered when the confident
  set fell short of the requested count; see "Falling short of the requested count" below). Omit
  the field, or mark everything `cross_validated`, when every candidate cleared the normal bar.
- This is a **candidate list for human confirmation**, not an auto-feed — present it and let the
  user drop/add before it goes into `social-best-video-scraper`. Wrong competitors waste real
  Apify spend one stage downstream.
- If fewer cross-validated competitors are found than requested (e.g. asked for 5, only 3 are
  well-evidenced) even after widening the search, say so explicitly — list the confident ones,
  then any `single_mention` candidates found along the way, and let the user decide whether to
  accept a shorter confident list or include the weaker ones. Never pad silently with weak or
  speculative matches presented as equally solid (Evidence Policy, `.claude/CLAUDE.md`).

## Decision Principles

**Announce the default before searching.** Before running any search, tell the user "I'll find 5
competitors by default — let me know if you want a different number" (or equivalent) as part of
the opening confirmation, the same way other skills in this repo surface their defaults up front
rather than silently applying them (see `schedule-design`'s "present pattern + defaults" step for
the pattern this follows). This is a brief heads-up, not a blocking question — proceed with the
default unless the user responds with a different number.

**Scoped vs. global search — two different inclusion rules, not just two different queries.**
Whether the user supplied a country/region changes what counts as acceptable evidence for
including a competitor, not only what gets typed into the search box:

- **Scoped (country/region supplied).** Search within that category + region combination (e.g.
  "sunscreen brands Thailand"). A candidate needs some evidence of relevance to *that market*
  specifically (sold there, marketed there, compared there) to be included — region relevance is
  part of what "competitor" means once a region has been named.
- **Global/unscoped (no country/region given, by the user's choice).** Search for
  category + positioning competitors without a region filter. Here, **the absence of
  region-specific proof is not a reason to exclude a candidate** — if a brand matches on category,
  positioning, and price tier, and nothing contradicts it being a competitor, keep it even when no
  search result happens to confirm it is sold in any particular country. The user did not ask for
  a regional claim, so do not manufacture one as a filter. (Concretely: a same-category
  dermatologist-brand candidate that only turned up in general/global comparisons, with no
  country-specific availability confirmed one way or the other, belongs in an unscoped result —
  excluding it there would be applying a regional bar the user explicitly chose not to set.)

Do not let "no country given" quietly default into a guessed country, and do not let "no
region-specific evidence found" quietly become "exclude it" in unscoped mode — those are two
different failure modes this principle rules out.

**Two-step grounding, not a single direct question.** Asking "who are Brand X's competitors"
directly tends to surface only large, well-known players and miss regional/long-tail brands that
actually share the same audience — exactly the brands this project's customers care about. Instead:

1. First establish the brand's actual category/positioning (skip this step if the caller already
   supplied it) — search for what the brand sells, its price tier, its target market.
2. Then search for competitors/alternatives within that category — region-qualified when one was
   given (e.g. "sunscreen brands Thailand" rather than just "Brand X competitors"), unqualified
   when it wasn't (e.g. "sunscreen brands dermatologist sensitive skin" / "alternatives to Brand
   X"). Either way this surfaces brands that compete on category and positioning, which is what
   actually matters for a social-video benchmarking use case — region is an additional filter when
   the user wants it, not a required ingredient of the query.
3. Normalize candidate names into clean, searchable brand names (drop marketing taglines, legal
   suffixes like "Co., Ltd.", retailer prefixes) and de-duplicate.

**Cross-check before finalizing.** Run the category+region competitor search more than once
(different phrasing, or a second independent pass) and keep names that show up consistently —
a name appearing only once in one ambiguous search result is weaker evidence than one confirmed
across multiple searches. This is a quality bar, not a hard requirement to re-run every single
query twice regardless of confidence.

**Falling short of the requested count is an escalation, not a stopping point.** If the
confident, cross-validated candidate set is smaller than what the user asked for (or the default
5), do not simply hand back the short list and consider the job done — try, in order:

1. **Widen the search** before lowering the bar: different phrasing ("alternatives to X" /
   "brands similar to X" / "X vs ___" comparison pages / "best X in <category>" roundups), adjacent
   category terms if the brand spans more than one, and — if the original search was scoped to a
   region and came up short — explicitly ask the user whether dropping the region filter is
   acceptable, rather than silently widening to global on their behalf.
2. **If still short after widening**, present the confident candidates plus the additional,
   weaker-evidence candidates found along the way, clearly labeled by confidence (e.g. "cross-
   validated" vs. "single mention, not independently confirmed") — let the user decide whether a
   lower-confidence match is good enough for their purpose, rather than the skill deciding for them
   either by silently including it as if equally solid, or by silently withholding it.
3. **Never fabricate a brand to hit the number.** A real brand with thin evidence, honestly
   labeled, is acceptable to offer; a brand that doesn't actually exist or has no evidentiary basis
   is not — that violates the Evidence Policy regardless of how insistent the request for a full
   count is.

**E-commerce category ranking as a secondary cross-check (optional, not default).** If the brand
is known to sell through Shopee/Lazada and the user wants higher-confidence results, the
category's own bestseller/category ranking page is a strong corroborating signal — brands ranked
in the same category are competing for the same customers by definition. This is **not** wired up
as an automatic step in this skill (no scraping logic is implemented for it yet) — mention it as
an available option to the user rather than silently skipping it or silently attempting to build
scraping logic for it ad hoc.

**A brand with an unclear category is a reason to ask, not guess.** Some brand names are
ambiguous out of context (a short, generic-sounding name, or one that spans multiple product
lines). If the category-identification step returns conflicting or ambiguous results, surface
that ambiguity to the user and ask which category/product line they mean rather than silently
picking one and proceeding — a wrong category assumption invalidates every competitor found
afterward.

## Self-Check Before Completion

- [ ] The default competitor count (5, or whatever the user adjusted it to) was stated before
      searching, not only reported after
- [ ] Country/region was either supplied, explicitly offered as an option, or the user chose
      global/unscoped — never silently assumed in either direction
- [ ] If the search ran unscoped, no candidate was excluded merely for lacking region-specific
      proof — only scoped search applies that bar
- [ ] Every returned competitor has a `rationale` naming concrete evidence, not a generic restatement
- [ ] If the confident result count fell short of what was requested, the search was widened
      (broader phrasing / adjacent category / offered to drop region) before settling for fewer —
      a short list was not handed back on the first pass without that attempt
- [ ] A short final list, or one that mixes confidence levels, says so explicitly — never padded
      with fabricated brands, and never silently presenting a weak match as equally solid
- [ ] Candidate names are clean, searchable brand names — no taglines, legal suffixes, or
      category words mistaken for a brand name
- [ ] The result is presented as a candidate list awaiting the user's confirmation, not
      auto-forwarded to `social-best-video-scraper`

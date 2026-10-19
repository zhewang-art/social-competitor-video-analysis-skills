# social-best-video-scraper — INSTRUCTIONS

Ported from `AnyMindGroup/anyagents`'s `skills/fetch-sns` (Instagram/Facebook search paths
only) and its `skills_lib.apify_guard` cost-preflight pattern — adapted to run standalone in
this repo (Apify only, no Data365, no anyagents session-dir infra). See
`reference/apify-actors.md` for the adopted actor IDs, their verified field mappings, and why
they were chosen over alternatives (ScrapeCreators, other Apify actors).

## Responsibility

Given a list of brand/competitor names, search Instagram and Facebook for each and return the
`N` best-performing videos per brand per platform — ranked by view count where the platform
exposes it, falling back to an engagement-weighted score on Facebook when it does not — then
download the actual video files and write one manifest recording what was found, what was
downloaded, and what it cost.

**Or**, given a list of specific video URLs the caller already has (direct URL input mode,
added 2026-10-17 — see "Direct URL input mode" below), fetch exactly those videos' metadata
instead of searching for candidates at all.

This skill does **not**:
- decide which brands are competitors (→ `competitor-brand-discovery`, not yet built)
- analyze video content, story structure, or selling points (→ a future video-analysis skill)
- use Data365 (deferred; the provider seam exists in the ported code's shape but is not wired up)

## Input Contract

| Input | Required | Notes |
|---|---|---|
| Brand/competitor names | Yes | One or more. Each becomes an independent search. |
| Hashtags per brand (IG) | Optional | A list — one actor call searches all of them at once, results deduped by post_id. Falls back to the single `hashtag` key, then to one derived from the brand name (lowercased, spaces stripped) when neither is given. A derived/single hashtag often returns zero or noisy results for small/niche brands — treat an empty IG result as evidence to ask the user for a better hashtag, not a skill bug. |
| Queries per brand (FB) | Optional | A list — one actor call **per** query (this actor has no native multi-term support), results deduped by post_id across calls. Falls back to `[brand]` when not given. |
| `fb_location` per brand | Optional | Facebook's own pinned-location filter — narrow (matches posts with a geotag, not a general market), see "Country/market scoping" below before relying on it as a country filter. |
| Platforms | Optional | Default: both `instagram` and `facebook`. |
| Videos per brand per platform | Optional | Default: **20**, applied to every platform in this run. Can instead be a per-platform JSON object, e.g. `--per-platform-limit '{"instagram": 30, "facebook": 10}'` (added 2026-10-19 — a real requirement: different platforms legitimately need different counts in one run). Widened 2026-10-16 from the original 10 so `social-video-tag-analyzer`'s downstream frequency analysis can draw two independent top-N selections — by Views, by Engagement — from one tagged batch; a top-10-by-views set and a top-10-by-engagement set can be almost entirely different videos. |
| Candidate pool size | Optional | Default: 60. How many posts to pull **per search term** (or per account, in account mode) before ranking and keeping the top N — the accuracy/cost dial (see Decision Principles). Cost and raw volume scale with the number of hashtag/query/account variants given. |
| `--target-country` | Optional | Recorded in the manifest for `social-video-tag-analyzer`'s Gemini pass to assess per-video market match — see "Country/market scoping" below. Does **not** filter this skill's own results; neither Apify actor supports a true country filter. |
| `--months-back` | Optional | Default **3** — historical content is explicitly not useful for this skill's purpose, so this is the default, not an opt-in. `0` disables the window entirely. See "Recency scoping" below for what this does and doesn't guarantee per platform. |
| `instagram_urls` / `facebook_urls` per brand | Optional | Specific post/reel URLs the caller already has — see "Direct URL input mode" below. Highest priority for that platform when given. |
| `instagram_accounts` / `facebook_pages` per brand | Optional | Account/page URLs to discover that account's OWN best-performing videos — see "Account input mode" below. Priority over hashtags/queries, below the direct-URL fields above. |
| `instagram_tagged_accounts` per brand | Optional, IG only | Account URLs to discover videos OTHER accounts posted that tagged/mentioned this one (that account's "Tagged" tab) — e.g. KOL/UGC content a brand's own account never posted. Added 2026-10-19 on explicit request. Can be combined with `instagram_accounts` for the same brand (both get scanned, results combined) — no verified Facebook equivalent yet. |
| `APIFY_API_KEY` | Infra | Must be set in `.env`. Currently a test token (expect rotation — update `.env` only, nothing else changes). |

## Direct URL input mode

Added 2026-10-17 after a demo run surfaced the need: a user may already have specific video
links (pasted directly, or sourced some other way) rather than wanting this skill to discover
candidates by hashtag/query. This is a **different shape of problem**, not a parameter tweak
on search mode — there is no "best performing" selection to make when the caller already
chose the exact videos they want.

**What changes in this mode** (set `instagram_urls`/`facebook_urls` on a brand entry instead
of, or in addition to, `hashtags`/`queries` — direct URLs take priority for that platform when
both are given):
- Fetches via `apify/instagram-scraper` (`directUrls` input) / `apify/facebook-posts-scraper`
  (`startUrls` input) instead of the hashtag/query search actors — see reference/
  apify-actors.md for why these needed to be different actors entirely (neither search actor
  accepts a URL as input at all).
- **No `--per-platform-limit` truncation** — every URL that resolves is kept. The caller
  already decided these videos matter; silently dropping some to fit a "top N" cutoff designed
  for ranking down a search's candidate pool would be wrong here, not a safety net.
- **No `--months-back` recency filtering** — same reasoning: the caller chose these specific
  videos on purpose, "too old" doesn't apply to an intentional pick the way it applies to a
  stale hashtag search result.
- **No `--candidate-pool` concept** — there is no scan-then-rank step; one URL maps to one
  result.
- The manifest records `"discovery_mode": "direct_url"` (vs `"search"`) per brand/platform,
  and `search_terms_used` holds the URLs themselves — so downstream consumers (and a human
  reading the manifest) can tell at a glance this wasn't an auto-discovered candidate set.
- Rows still flow through `rank_and_select()` (with `top_n = len(rows)`, i.e. nothing is cut)
  so the output row shape — `rank`/`metric_type`/`metric_value`/`is_estimated` — stays
  identical to search mode; `social-video-tag-analyzer` and `frequency_analysis.py` consume
  that shape unchanged either way.

**Instagram by-URL gets a real `views` number where the hashtag actor sometimes didn't** —
observed live 2026-10-17 comparing the same post fetched both ways.

**Multi-source merge, never a single point of failure**: after the actor call, every row is
cross-checked against a plain `yt-dlp -J` metadata fetch (`ytdlp_metadata.py`) and any of
views/likes/comments/shares still missing gets filled from whichever source actually has it
(`field_merge.fill_missing_fields`) — see reference/apify-actors.md "Multi-source metric
merge" for the full rationale and verified examples. This is why Facebook's "no views" gap,
stated as an absolute above in earlier revisions of this doc, turned out to be fixable in
practice: yt-dlp's own page-scrape often exposes a view count neither Facebook actor does.
Whatever is STILL missing after trying every source gets a plain-language, business-reader
explanation in that row's `data_gap_note` field (`data_gaps.py`) — e.g. *"转发数：Instagram
不会公开这个数字，用任何工具都抓不到，不是漏抓。"* Never phrase a gap in terms of which tool
or actor was tried; the reader doesn't know or care, they want to know why a specific number
is missing for this video.

A URL that isn't actually a video (e.g. a photo post someone pasted by mistake) is not
pre-filtered out in this mode, unlike search mode's video-only filtering — the caller chose
this URL on purpose, so it's not this skill's place to second-guess it. `download.py` will
honestly report a download failure for it later; that's the correct place for that fact to
surface, not a silent drop upstream.

## Account input mode

Added 2026-10-19 for a third input shape: the caller has a specific KOL/competitor **account**
(not a specific video, not a hashtag) and wants that account's best-performing recent videos —
e.g. "find the best videos from this specific competitor's Instagram" without a hashtag search
turning up other accounts too. Set `instagram_accounts`/`facebook_pages` on a brand entry
(profile/page URLs, e.g. `"https://www.instagram.com/someaccount/"`) — priority over
hashtags/queries for that platform, but below the direct-URL fields (a caller who names both
specific videos AND an account for the same platform almost certainly means "analyze these
specific videos," not "also go discover more from that account").

**IG also supports `instagram_tagged_accounts`** — videos OTHER accounts posted that
tagged/mentioned this account (its "Tagged" tab), not this account's own posts. Verified live
2026-10-19 against a real brand account: returns real videos from DIFFERENT owner accounts
(KOL/UGC content), each with its own real view/like counts — genuinely different content from
`instagram_accounts`, not a filtered view of it. Can be combined with `instagram_accounts` for
the same brand entry (both get scanned in separate actor calls, results combined before
ranking) — useful when the caller wants both a brand's own content AND what others post about
it. No verified Facebook equivalent exists yet.

**Unlike direct URL mode, this IS a discovery step** — closer in spirit to hashtag/query
search than to "keep everything": it scans a `--candidate-pool`-sized sample from the
account's own feed, applies `--months-back` recency filtering, and ranks down to
`--per-platform-limit` via `rank_and_select()`. The manifest records `"discovery_mode":
"account"`.

**Both actors natively support this — verified live 2026-10-18, no new actor needed**:
- **Instagram**: the same `apify/instagram-scraper` actor used for direct URL mode, pointed
  at a profile URL with `resultsType="reels"` instead of a specific post with `resultsType`
  matched to that URL's path. Returns the account's own reels with real `videoPlayCount`
  values — `resultsType="posts"` on the same profile URL was tested too and returns a mix of
  photos/carousels instead, confirming `"reels"` is the right choice here (same reasoning as
  `search_instagram`'s existing `"reels"` choice). Output shape is identical to every other
  use of this actor in this skill — `instagram_fetcher._normalize_item` is reused as-is.
- **Facebook**: `apify/facebook-posts-scraper`'s own DOCUMENTED primary use case (`startUrls`
  pointed at a page) — this skill just hadn't used that mode until now, only ever pointing it
  at direct post/reel URLs. Crawling a page's feed returns a **THIRD distinct output shape**
  from this actor (on top of the two already documented for direct URL mode in
  reference/apify-actors.md) — see `facebook_fetcher._normalize_page_feed_item` and that
  reference doc's "Multi-source metric merge" section for the full field mapping. This shape
  uniquely carries `media[].__typename` (`"Video"` vs `"Photo"`), which is how non-video posts
  get filtered out of a page's mixed-content feed — the other two shapes are always a single
  already-known post, so that distinction was never needed there.

Account mode gets the same yt-dlp multi-source merge as direct URL mode (same non-overlapping-
gaps rationale, same actors) — a Facebook account-mode row commonly gets its `views` filled in
by yt-dlp exactly like a direct Facebook URL does.

**Live-verified end to end** (2026-10-18, SOFY's own IG/FB accounts, `--candidate-pool 15`,
`--per-platform-limit '{"instagram": 3, "facebook": 3}'`, total cost $0.0545): Instagram scanned
15 reels and kept the top 3 by views (one at 1.17M views the account's own hashtag-matched
content hadn't surfaced); Facebook scanned 15 feed posts, correctly filtered down to 4 actual
videos (11 were photos), and kept the top 3 by views (filled in via the yt-dlp merge, since
neither Facebook actor exposes views directly).

## Recency scoping

Facebook and Instagram are **not symmetric here** — verified live 2026-10-16 against both
actors' real input schemas:

- **Facebook has a native date window.** `scraper_one/facebook-posts-search` takes `startDate`
  (and `endDate`) directly — this is a genuine search-time boundary; the actor itself only
  returns posts within range, so cost and candidate volume reflect the scoped window, not an
  unbounded search filtered down afterward.
- **Instagram has no date parameter at all.** `apify/instagram-hashtag-scraper`'s hashtag/reels
  search returns whatever finite, Instagram-ranked sample it returns for `resultsLimit` — this
  skill applies `date_filter.filter_within_date_window()` to that sample *after the fact*,
  dropping anything older than the cutoff. This is **not** an exhaustive scan of all
  within-window Instagram content for that hashtag — if the hashtag's top/reels results skew
  toward older evergreen virality, filtering can shrink the kept pool well below the requested
  `--candidate-pool`, with no way to "page further" specifically within the window. A brand
  whose `video_candidates` comes back much smaller than `candidates_scanned` for this reason is
  worth a larger `--candidate-pool`, not a sign of a bug.

A row whose `posted_at` can't be parsed at all is **excluded**, not kept — "historical content
isn't useful" implies a candidate with unconfirmable recency doesn't meet that bar either. The
manifest's `excluded_too_old` and `excluded_unparseable_date` counts (per brand/platform) keep
these two failure modes distinguishable — collapsing them into one "excluded" number would hide
which one actually happened.

## Country/market scoping

Verified live 2026-10-16 against both actors' actual Apify input schemas (not marketing
copy): **neither `apify/instagram-hashtag-scraper` nor `scraper_one/facebook-posts-search`
supports a country/region filter.** The IG actor's only inputs are `hashtags` /
`keywordSearch` / `resultsType` / `resultsLimit` — no geography field at all. The FB actor has
a `location` field, but it filters to posts with a **pinned** Facebook location tag (a
geotag) — most ordinary brand marketing content carries no pinned location, so this
under-matches badly as a general "scope to country X" mechanism; it's exposed as `fb_location`
for narrow supplementary use, not the primary lever.

The actual working approach, split across two stages:

1. **Here (Stage 2), bias the search with localized query variants.** When the caller
   specifies a target country, prepare hashtag/query variants in that market's language
   (plus an English variant, since brands often post in English even in non-English markets)
   and pass them all as `hashtags`/`queries`. This script does **not** translate anything
   itself — generating good localized variants is the caller's judgment call, not something
   safe to automate with a static lookup table (mirrors `AnyMindGroup/anyagents`'s TikTok
   search protocol, which pushes the same decision to the calling agent for the same reason).
   This is a bias, not a filter — it increases the odds that candidates are from the right
   market, it does not guarantee it.
2. **In `social-video-tag-analyzer` (Stage 4), Gemini makes the real content-based judgment.**
   Since this skill can't filter by country, the only point in the pipeline that can actually
   tell whether a candidate matches the target market is the stage that watches the video —
   pass `--target-country` through (or just let it flow via the manifest, which
   `social-video-tag-analyzer` reads automatically) and that skill's Gemini pass adds a
   `country_match` assessment per video.

**This split matters in practice, not just in theory** — live-tested 2026-10-16 against
real CeraVe Instagram results (hashtags `cerave`+`ceraveskincare`, target country "United
States"): of 5 "best performing" candidates by views, **3 were not from the target market**
at all (Brazilian Portuguese, Italian, and Marathi/Indian audio — one of them the single
highest-viewed candidate in the batch, 8.2M views). Hashtag search alone would have silently
reported that video as the brand's best-performing US content. Gemini's content-based check
correctly caught all three mismatches, including the Marathi one despite it carrying English
on-screen subtitles — it keyed off the actual spoken language, not just visible text.

## Output / Handoff Contract

Per run, under a caller-chosen output directory (e.g. `output/social-best-video-scraper/<brand>-<date>/`):

- `videos/{platform}_{brand_slug}_{rank}_{post_id}.mp4` — downloaded video files, one per kept result that downloaded successfully
- `manifest.json` — one row per candidate that was *ranked into the top N*, whether or not its
  download succeeded:

  ```json
  {
    "generated_at": "2026-10-16T03:00:00Z",
    "target_country": "United States",
    "months_back": 3,
    "date_cutoff": "2026-07-18T03:00:00+00:00",
    "brands": [
      {
        "brand": "Brand A",
        "platform": "instagram",
        "search_terms_used": ["branda", "branda_local_language_variant"],
        "candidates_scanned": 58,
        "video_candidates": 42,
        "excluded_too_old": 15,
        "excluded_unparseable_date": 1,
        "results": [
          {
            "rank": 1,
            "post_id": "...",
            "url": "https://www.instagram.com/p/.../",
            "metric_type": "views",
            "metric_value": 184000,
            "is_estimated": false,
            "local_file": "videos/instagram_branda_01_abc123.mp4",
            "download_status": "ok",
            "download_error": null
          }
        ]
      }
    ],
    "cost_summary": {
      "apify_calls": [
        {"actor_id": "apify/instagram-hashtag-scraper", "quantity": 58, "estimated_usd": 0.203}
      ],
      "total_estimated_usd": 0.406
    }
  }
  ```

- `video_candidates` reflects filtering by content type (photos excluded) **and**, when
  `months_back > 0`, by recency — `excluded_too_old` / `excluded_unparseable_date` account for
  the rest of the gap from `candidates_scanned` (see "Recency scoping" above). `months_back`/
  `date_cutoff` are `null` at the manifest's top level when `--months-back 0` disabled the
  window for that run.
- `candidates_scanned` is the **raw** pool size the Apify actor returned (what was actually
  paid for); `video_candidates` is how many of those were video posts after filtering out
  photos/links. On Facebook these commonly differ — `scraper_one/facebook-posts-search`'s
  "top" search for a bare brand keyword often returns mostly photo/graphic posts, so
  `video_candidates: 0` out of `candidates_scanned: 15` is a real, expected outcome for some
  brands, not a bug. On Instagram the two are always equal (`resultsType=reels` pre-filters
  server-side). `results` must reflect what was *actually* found — if a brand's hashtag only
  surfaces 7 video posts, report 7, never pad to 10 with fabricated rows (Evidence Policy,
  `.claude/CLAUDE.md`).
- Direct URL mode (`"discovery_mode": "direct_url"`) adds `"data_gap_note"` to each result row
  — a plain-language explanation of any views/likes/comments/shares field still missing after
  the multi-source merge (empty string when nothing is missing). See "Direct URL input mode"
  above and reference/apify-actors.md "Multi-source metric merge."
- A `video_candidates: 0` Facebook brand is worth retrying with a more specific query (e.g.
  appending a descriptive word like "review" or "unboxing" to the brand name) before
  concluding the brand has no discoverable Facebook video content — verified live 2026-10-15
  that specific/descriptive queries surface native video posts (UGC/creator content) far more
  often than a bare brand name, which mostly surfaces the brand's own photo-heavy page posts.
- `metric_type` is `"views"` when the platform exposed a real view/play count, or
  `"engagement_score"` (Facebook fallback) when it did not — `is_estimated: true` in the
  latter case. Never relabel an engagement score as views.
- A failed download keeps its manifest row (`download_status: "failed"`, `download_error`
  set) rather than being silently dropped — the ranking information is still useful even
  when yt-dlp could not fetch the file (private/removed/geo-blocked post).

## Decision Principles

**Cost preflight runs before every Apify call, not after.** Port `scripts/apify_guard.py`'s
`estimate_apify_cost()` / `check_apify_preflight()` as-is in spirit: a per-call estimate from a
fixed per-item USD price (see `reference/apify-actors.md`), compared against an auto-approve
threshold (default $10/call, env `APIFY_AUTO_APPROVE_USD`). Under threshold → run directly. Over
threshold, or an actor not in the price table → raise/report the same
`APIFY_APPROVAL_REQUIRED_BEGIN ... END` sentinel block the caller must surface verbatim and stop
— never silently retry, never silently proceed. Approval is a plain-text reply in the thread;
once given, the calling agent (not this script) marks that exact request hash cleared (see the
script's module docstring for the marker-file mechanism) and reruns. This mirrors
`.claude/skills/_shared`-equivalent paid-fetch discipline from the anyagents source — there is
no Slack-button gate here, a worded reply is sufficient.

**Candidate pool size is the accuracy/cost dial.** A larger pool (more posts fetched and ranked
before keeping the top N) gives higher confidence the kept results really are the brand's best
performers, at roughly linear cost (`$0.0035 × pool_size` per search term per platform per
brand, both adopted actors share that unit price). Default 60 is a reasonable middle ground;
raise it for a brand whose top-10 selection materially matters (e.g. the final report) and
lower it for a quick scan. Never silently shrink the pool to save cost without surfacing that
trade-off. **Multiple hashtag/query variants multiply this cost** (IG: one call scanning
`pool × n_hashtags` items; FB: `n_queries` separate calls each scanning `pool` items) — this is
the real cost of the "Country/market scoping" approach above, surface it when proposing more
than one or two variants per brand.

**Facebook views are opportunistic, not guaranteed.** `scraper_one/facebook-posts-search`
sometimes exposes a real view count and sometimes does not (Facebook's own limitation, not an
actor bug) — verified live 2026-10-15: `scraper_one/facebook-posts-search` exposed no
`views`/`viewsCount` field at all across every checked sample, so in practice every Facebook
row currently falls to the engagement fallback. Compute
`engagement_score = likes + 2*comments + 3*shares` (placeholder weights — flag to the user that
these need business calibration, do not present them as settled) and rank by that instead.
`likes` here is `reactionsCount` — Facebook's **total reactions** (like+love+haha+wow+support+
anger+sorry combined), not a literal like-button-only count; never present it to a user as
"likes" without that caveat. Never mix the two metrics within one ranked list silently — a
brand's Facebook results may legitimately contain some real-view rows and some
engagement-score rows; `metric_type` per row makes this explicit rather than hiding it behind
one unlabeled "score" column.

**The recency window is a correctness requirement, not a nice-to-have.** Live-tested
2026-10-16 (CeraVe, `#cerave`, `--months-back 3`): Instagram's post-hoc filter correctly
excluded 1 of 15 real candidates as too old, keeping only the 14 within the window (all 5
kept results spanned 2026-08-11 to 2026-10-09, correctly inside a 2026-07-18 cutoff) — proof
the exclusion logic fires on genuine data, not just synthetic unit-test rows.

**A brand with zero results is a reportable fact, not a retry loop.** If Instagram's
hashtag-scraper or Facebook's post-search returns nothing for a brand, say so in the manifest
and move on — do not keep inventing hashtag variants without the user's input, and do not pad
the result with unrelated posts.

## Self-Check Before Completion

- [ ] Every kept result's `metric_type`/`is_estimated` accurately reflects whether the number is
      a real views count or an engagement-score fallback
- [ ] `candidates_scanned` matches what the actor actually returned, not the requested pool size
- [ ] No brand's result count was padded past what was actually found
- [ ] `cost_summary` reflects the actual Apify calls made (actor id, quantity, estimated USD per
      call) — this is the number the user asked to be able to report
- [ ] A failed download is recorded in the manifest, never silently dropped
- [ ] If any call needed approval, the sentinel block was surfaced verbatim and the run paused
      — it was never bypassed
- [ ] A brand/platform using `instagram_urls`/`facebook_urls` shows `"discovery_mode":
      "direct_url"` in the manifest, kept EVERY resolved URL (no per_platform_limit
      truncation), and was not date-filtered regardless of `--months-back`
- [ ] Direct URL mode rows were cross-checked against yt-dlp metadata before giving up on a
      missing field — a gap is only reported in `data_gap_note` after BOTH the actor and the
      yt-dlp fallback came up empty, and the note is phrased for a business reader (platform/
      content terms), never naming a tool or actor
- [ ] A brand/platform using `instagram_accounts`/`facebook_pages` shows `"discovery_mode":
      "account"` in the manifest, WAS ranked down to `--per-platform-limit` and WAS
      date-filtered when `--months-back` > 0 — unlike direct URL mode, this is a discovery
      step, not a "keep everything the caller named" step
- [ ] `--per-platform-limit` was applied per-platform when given as a JSON object — a brand
      analyzed on both platforms in one run can legitimately keep a different count per
      platform; this was never silently collapsed to one shared number
- [ ] If a `--target-country` was given, it was passed through as a bias (localized
      hashtag/query variants), never claimed as an actual filter — the manifest's
      `target_country` field exists so `social-video-tag-analyzer` can do the real check
- [ ] Cross-hashtag/cross-query duplicates were deduped before ranking — a post found under
      two searched terms is not double-counted or double-ranked
- [ ] When recency scoping was active (`--months-back` > 0), `excluded_too_old` /
      `excluded_unparseable_date` account for the full gap between `candidates_scanned` and
      `video_candidates` beyond the platform's own content-type filtering — nothing silently
      disappeared unaccounted for

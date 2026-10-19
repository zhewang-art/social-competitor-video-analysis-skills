# Adopted Apify actors (reference)

Ported from `AnyMindGroup/anyagents`'s `skills/fetch-sns/references/apify-actors-matrix.md` and
`fetch_sns/sources/instagram/adapters/apify.py` / `fetch_sns/fetchers/facebook_search_fetcher.py`
— these are the actors that repo's production routing actually calls for Instagram/Facebook
search, not a fresh evaluation. Re-evaluate only if one of these starts failing in practice.

## Instagram: `apify/instagram-hashtag-scraper`

Official Apify actor. One call searches by hashtag(s) and returns posts directly — no
separate per-post detail call needed to get the view count (unlike some other providers).

**Input** — note `resultsType: "reels"`, not `"posts"` (see below):
```json
{
  "hashtags": ["branda", "branda_local_language_variant"],
  "resultsLimit": 60,
  "resultsType": "reels",
  "proxy": {"useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"]}
}
```

**Full official input schema verified live 2026-10-16** by pulling the actor's actual build
definition from the Apify API (not the marketing/docs page, which under-documents it): the
ONLY fields are `hashtags` (array), `keywordSearch` (bool), `resultsType` (enum:
posts/reels/stories), `resultsLimit` (int). **There is no country/region/location field at
all** — this actor cannot filter by geography; see INSTRUCTIONS.md "Country/market scoping"
for the actual working approach (localized query variants + a downstream content-based
check). The `proxy` field in the example above is not part of this actor's own documented
schema (Apify's generic platform-level proxy config, harmless to pass, does not provide
geo-filtering of results).

`hashtags` is a native array — passing multiple hashtags in **one call** is supported and
costs `resultsLimit × len(hashtags)` per the actor's own pricing note ("if you set this to 5,
you will scrape 5 posts for each hashtag"). Chunk into groups of at most
`APIFY_HASHTAG_CAP_PER_RUN` (default 20 in the source repo; this port need not enforce a cap
for realistic hashtag-variant counts) per actor call. Results across hashtags are deduped by
post_id in `instagram_fetcher.py` — the same post can surface under more than one searched tag.

**`resultsType: "reels"`, not `"posts"`** — `fetch-sns`'s original ported code used `"posts"`.
Verified live 2026-10-15 against `#nike` that this was wrong for our goal: `"posts"` crawls the
hashtag's chronological "Recent" grid — mixed photo/video, newest first, and every result had
exactly 0 likes/comments because they'd been posted literally seconds before the call (no time
to accumulate engagement). `"reels"` returned real videos spanning several days with real,
correctly-descending view counts (115K/67K/54K/44K/39K in one test). Use `"reels"` for this
skill's "best performing video" goal.

**Relevant output fields per item** (shape captured 2026-09-02 for `fetch-sns`'s original
`"posts"` mode, re-verified live 2026-10-15 for `"reels"` — read defensively, not a formally
versioned contract):
- `shortCode`, `id`, `url` — post identity (build the URL from `shortCode` when `url` is absent)
- `ownerUsername`, `ownerFullName` — poster identity
- `timestamp` — ISO post time
- `videoPlayCount` / `videoViewCount` — use whichever is present for the view metric
- `likesCount`, `commentsCount` — **Instagram returns `-1` here when the poster has hidden the
  count**, not a real negative number; treat `-1` as unknown/missing, never report "-1 likes"
  (hit live on 2026-10-15, one post in five)
- `videoUrl` — direct video URL (feed into `yt-dlp` or download directly; both work, `yt-dlp`
  is more robust to URL expiry)
- `displayUrl` — thumbnail
- `caption`, `hashtags`
- **No share-count field anywhere in this actor's output** — this is a platform/actor limit,
  not a bug to fix. `instagram_fetcher.py` normalizes `shares` to `None`, never `0` — a `0`
  would misreport "verified zero shares" when the truth is "Instagram never told us." Caught
  live 2026-10-16 as an actual bug (a hardcoded `0` had been shipping since this skill's first
  version) once `social-video-tag-analyzer` started surfacing this field directly in its
  output sheet, where it would have looked like real data.

**Price**: $0.0035/result (this port's price table; re-check Apify's actor page if costs look
off — this is a preflight estimate, not a billing source of truth).

## Facebook: `scraper_one/facebook-posts-search`

**Input**:
```json
{
  "query": "Brand A",
  "resultsCount": 60,
  "searchType": "top",
  "location": "Bangkok"
}
```

`searchType`: `"top"` (default) or `"latest"`. `startDate` / `endDate` (`"YYYY-MM-DD"`) are a
**native, search-time date boundary** — not a post-hoc filter — wired up in
`facebook_fetcher.py`'s `date_after`/`date_before` params for `social-best-video-scraper`'s
`--months-back` recency scoping (see that skill's INSTRUCTIONS.md "Recency scoping"). This is
the one actual advantage this actor has over the Instagram one for date-bounded searches —
Instagram has no equivalent field at all.

**Full official input schema verified live 2026-10-16** (pulled directly from the actor's
real build definition via the Apify API): `query` (required, single string — unlike the IG
actor, **no native multi-term array support**, so multiple query variants cost one actor call
each), `resultsCount`/`maxResults`/`maxItems` (aliases for the same limit), `searchType`,
`location`, `startDate`, `endDate`.

**`location` exists but is narrow — not a general country filter.** Its own description:
"Limit posts to a given **pinned** location. Enter a location name or a Facebook Location
ID." This matches posts with an actual geotag/check-in attached — most ordinary brand
marketing videos carry no pinned location at all, so this field will under-match badly if used
as a stand-in for "scope to country X." Exposed in this skill as the optional `fb_location`
brand-entry field for narrow supplementary use (e.g. a campaign genuinely tied to a specific
city) — see INSTRUCTIONS.md "Country/market scoping" for the actual recommended approach
(localized query variants here + Gemini's content-based `country_match` check in
`social-video-tag-analyzer`).

**Relevant output fields per item** — this skill's own `facebook_fetcher.py` trusts the
**live-verified** shape below (checked 2026-10-15 with two real calls), not the field names
`fetch-sns`'s ported code originally assumed (`likes`/`content`/`videoUrl` top-level — those
do not appear in the actor's current output; the shape has drifted since that code was
written):
- `postId`, `url` — post identity (`url` is `facebook.com/.../posts/...` or
  `facebook.com/reel/.../` for Reels)
- `author.name`, `author.profileUrl` — poster identity
- `timestamp` — **epoch milliseconds** (not an ISO string — convert before use)
- `postText` — caption
- `reactionsCount` — **total reactions across all types** (like+love+haha+wow+support+anger+
  sorry), reported as this skill's `likes` field but is not a literal like-only count
- `commentsCount`, `sharesCount` — present and correctly named
- `attachments[]` — `{"type": "video"|"photo"|..., "url": "..."}`; a video post's direct CDN
  URL is `attachments[n].url` where `type == "video"`. Posts with no video attachment are
  filtered out (this skill only wants videos).
- **No `views`/`viewsCount` field was observed in either live sample** — always fall back to
  the engagement-weighted score (see INSTRUCTIONS.md) until/unless a future check finds one.

**Price**: $0.0035/result, same as the Instagram actor.

**Practical yield note** (verified live 2026-10-15): a bare brand-name query with
`searchType=top` (e.g. `"Nike"`) returned video posts at a *much* lower rate than a more
descriptive/specific query (e.g. `"Nike shoes review"`) — the former skews toward the brand's
own photo-heavy official content, the latter surfaces organic creator/UGC video content. A
brand returning `video_candidates: 0` is worth retrying with a more descriptive query before
concluding it has no discoverable Facebook video content.

## "By URL" actors (direct URL input mode, added 2026-10-17)

Both actors above are **search/discovery** actors — they take a hashtag or query and return
whatever they find. Neither accepts "give me this exact post URL" as input. When the caller
already has specific video URLs (see `run.py`'s `instagram_urls`/`facebook_urls` brand-entry
fields, INSTRUCTIONS.md "Direct URL input mode"), two different, officially-maintained Apify
actors are used instead:

### Instagram: `apify/instagram-scraper`

The large official "Instagram Scraper" (213M+ runs at verification time). Input:
```json
{"directUrls": ["https://www.instagram.com/p/ABC123/"], "resultsType": "posts", "resultsLimit": 1}
```

**`resultsType` must match the URL's own path** (verified live 2026-10-17 against the actor's
real input schema): `/p/...` URLs need `"posts"`, `/reel/...` URLs need `"reels"` — mixing
both under one `resultsType` silently returns nothing for the mismatched ones.
`instagram_fetcher.fetch_instagram_by_urls` splits the input URLs into the two groups and
sends up to 2 actor calls, one per group.

**Output shape is the SAME field naming as `apify/instagram-hashtag-scraper`'s own output**
(verified live 2026-10-17 by inspecting real dataset items: `shortCode`, `likesCount`,
`commentsCount`, `videoPlayCount`, `videoUrl`, `ownerUsername`, `timestamp`, `caption`,
`displayUrl` — identical key set) — both are `apify`-org actors sharing the same underlying IG
scraping schema, so `instagram_fetcher._normalize_item` is reused as-is, no separate
normalizer needed. **Notably this actor DOES return a real `videoPlayCount`** on posts where
the hashtag-scraper actor's own output didn't carry one in a side-by-side check — by-URL
lookup appears to get fuller post detail than a hashtag-search hit. No `shares` field either,
same platform-level gap as the hashtag-scraper actor.

**Price**: $0.0023/result (free tier; re-check Apify's actor page if costs look off).

### Facebook: `apify/facebook-posts-scraper`

The official "Facebook Posts Scraper" (50M+ runs). Can crawl N posts from a page URL, or —
our use case — return exactly one result per **direct post/reel URL** (its own `resultsLimit`
is documented as "ignored when scraping direct post URLs," confirmed live):
```json
{"startUrls": [{"url": "https://www.facebook.com/reel/1700880531368315"}]}
```

**Output shape is COMPLETELY DIFFERENT from `scraper_one/facebook-posts-search`'s** — a
deeply-nested GraphQL-shaped payload, not a flat one. And it is NOT even one consistent shape
itself: the actor returns one of (at least) **two incompatible shapes depending on the input
URL's own format** — a real trap caught live, not a hypothetical:

**Shape A** — `/reel/<id>` URLs that stay reel posts (verified 2026-10-17):
- `message.text` — caption
- `likes` — a plain int matching `unified_reactors.count` in the same payload (total
  reactions, same "not literally like-only" caveat as the other actor's `reactionsCount`)
- `total_comment_count`, `share_count_reduced` — comments and shares, both present
- `creation_time` — epoch SECONDS, not milliseconds like the other actor's `timestamp`
- `pageName` — a real page name

**Shape B** — `fb.watch` short links, which this actor internally canonicalizes to a BARE
VIDEO NODE rather than a post (verified 2026-10-18, surfaced by a real `fb.watch/v/...` link
in a demo batch):
- **No `message` key at all** — caption is unrecoverable from this actor for this shape, a
  genuine gap, not a bug
- **No `total_comment_count`/`share_count_reduced` keys** — but a plain `comments` int field
  IS present in BOTH shapes with the SAME value as Shape A's `total_comment_count` (confirmed
  by comparing both on real items), so `_normalize_direct_item` reads `comments` as the
  primary source for comment count, with `total_comment_count` only as a defensive fallback.
  `share_count_reduced` has no equivalent in Shape B at all — confirmed by walking the full
  payload structure — a real, unrecoverable gap for this shape.
- `publish_time` instead of `creation_time` (same epoch-seconds unit) — `_normalize_direct_item`
  tries `creation_time` first, falls back to `publish_time`.
- **`pageName` comes back as the literal string `"reel"`** — not a real page name, looks like
  an internal placeholder the actor emits when it can't resolve the real page for this
  canonicalized shape. Treating it as real data would show "reel" as the video's uploader
  handle, confidently wrong — `_normalize_direct_item` detects this exact placeholder value
  and leaves `username` blank instead.

Neither shape has a views/play-count field anywhere (confirmed by walking the full nested
structure of both, including `short_form_video_context.playback_video`) — same platform-level
gap either way. Neither has a direct CDN video URL either (Facebook's video delivery is behind
DASH manifests, not a plain field) — `video_url` is left empty in the normalized row;
`download.py` already tries the post's own page URL first with yt-dlp, which is what actually
downloads the file in practice, so this is not a functional gap.

`facebook_fetcher._normalize_direct_item` is a separate function from `_row` (not a shared
normalizer) because the two search-vs-direct actors' shapes have nothing in common beyond the
final row schema this skill produces — and it itself has to defensively read fields that work
across Shape A and Shape B, per the above.

**Price**: PAY_PER_EVENT, $0.005/post on the free tier (re-check Apify's actor page if costs
look off).

## Account input mode: a THIRD Facebook actor shape (added 2026-10-19)

`apify/instagram-scraper` pointed at a profile URL (instead of a specific post) with
`resultsType="reels"` needs no new normalizer — verified live 2026-10-18, it returns that
account's own reel content using the EXACT SAME field names as every other use of this actor
in this skill (`_normalize_item` is reused as-is). `resultsType="posts"` on the same profile
URL was tested too and returns a mix of photos/carousels instead — confirming `"reels"` is the
right choice for account-mode video discovery, same reasoning as the hashtag actor's existing
`"reels"` choice.

`apify/facebook-posts-scraper` pointed at a PAGE URL (its own documented primary use case —
this skill just hadn't used that mode until now) returns a **THIRD distinct shape** from this
actor, on top of Shape A/B already documented above for direct post/reel URLs. Verified live
2026-10-18 crawling a real page (`facebook.com/SOFYMalaysia/`):
- `likes`, `comments`, `shares` — all plain top-level ints (unlike Shape A's nested
  `total_comment_count`/`share_count_reduced`). `comments` was observed `None` on most
  low-engagement posts in the same real crawl even when `likes`/`shares` had real nonzero
  values — unclear whether that's a genuine absence or an actor quirk on low-engagement posts;
  `_normalize_page_feed_item` passes it through as-is (`None` stays `None`, never assumed 0).
- `text` — caption (plain field, unlike Shape A's nested `message.text`)
- `postId`, `url` — `url` here is the REAL per-post permalink (unlike Shape B's `facebookUrl`,
  which in page-feed mode is just the page URL repeated identically on every row — a trap this
  shape does NOT have)
- `pageName` IS reliable in this shape — confirmed a real handle (`"SOFYMalaysia"`), not Shape
  B's `"reel"` placeholder bug
- `timestamp` — epoch SECONDS, same convention as Shape A's `creation_time`
- **`media[].__typename`** (`"Video"` vs `"Photo"`) — this shape UNIQUELY carries a way to
  tell video posts from photo posts before picking one, since a page's feed genuinely mixes
  both; Shapes A/B are always a single already-known post, so this distinction was never
  needed there. `_normalize_page_feed_item` returns `None` for a non-video post, mirroring
  `_row`'s video-only filtering for the original search actor.
- **Still no views field** — same platform-level gap as every other shape this actor returns.

Live end-to-end test (2026-10-18, `facebook.com/SOFYMalaysia/`, `resultsLimit=15`): 15 feed
posts scanned, 11 were photos (filtered out), 4 were videos — all 4 got their `views` filled
in via the yt-dlp merge below, since (as always) the actor itself exposed none.

## Multi-source metric merge (direct URL mode AND account mode, added 2026-10-18)

Neither by-URL actor ever exposes every metric a caller might want — and critically, **their
gaps are different, non-overlapping ones**: the Instagram actor never exposes shares; the
Facebook actor never exposes views (any of its three shapes). A third source turns out to
sometimes fill exactly the gap the actor leaves: plain `yt-dlp -J <url>` (see
`ytdlp_metadata.py`) parses whatever a platform's own web page publicly displays, independent
of either actor's scraping method. Verified live 2026-10-17/18: yt-dlp's `view_count` was
populated for every Facebook URL tested (direct `/reel/`, `fb.watch`-resolved, AND page-feed
posts) even though NO Facebook actor shape exposes a views field at all — genuinely
complementary data, not redundant.

So, in direct URL mode and account mode (`fetch_instagram_by_urls` / `fetch_facebook_by_urls`
/ `fetch_instagram_by_accounts` / `fetch_facebook_by_pages` — never in hashtag/query search
mode, where running yt-dlp per candidate in a large batch would be wasteful), each
actor-sourced row is cross-checked against `fetch_ytdlp_metadata(row["url"])` and any of
views/likes/comments/shares still `None` gets filled from whichever source has it —
`field_merge.fill_missing_fields`: the actor's value always wins when both sources have one; a
yt-dlp failure (private/removed/timeout — it returns `{}`, never raises) just means that row
keeps whatever the actor already gave it, never aborts the batch.

Whatever is STILL `None` after trying every source gets a plain-language explanation in that
row's `data_gap_note` field (see `data_gaps.py`) — written for a business reader in terms of
the platform/content, e.g. *"转发数：Instagram 不会公开这个数字，用任何工具都抓不到，不是漏
抓。"* — never in terms of which tool or actor was tried and failed. Only a short list of
gaps are confidently attributed to a confirmed, always-true platform limit (currently just
Instagram shares, verified across two independent actors); everything else still missing
after the merge gets an honest, non-overclaiming "this content didn't expose it" note rather
than a confident guess at the mechanism.

## Why these two (search actors), not alternatives

- **ScrapeCreators** (a non-Apify unified API) was evaluated in this project's own research
  (`lidia_notes/社媒达人视频分析skill调研.md`) before this port decision: cheaper per raw request, but
  its Instagram hashtag search doesn't return views at all (needs one extra per-post detail
  call per candidate) and is capped at ~11 pages of pagination — a much shallower candidate
  pool than Apify's `resultsLimit`. Dropped once `fetch-sns`'s already-production-proven Apify
  routing was found; may be revisited later as a cost optimization.
- **Other Instagram Apify actors** (`zaver.api/instagram-reel-scraper`, community-maintained)
  were not adopted — `fetch-sns` already uses the *official* `apify/instagram-hashtag-scraper`,
  which is the safer default (official actors get maintained; community actors can go stale).
- **Data365** is `fetch-sns`'s actual primary provider for IG/FB (cheaper, and IG hashtag search
  returns Instagram's own "top post" ordering) but is deliberately **not** ported in this first
  pass — the team doesn't yet have Data365 account access. The code is written so a
  `data365_fetcher.py` sibling could be added later without reshaping the public interface
  (`search_instagram`, `search_facebook` return the same row shape regardless of provider).

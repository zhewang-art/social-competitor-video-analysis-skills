"""Facebook post search via `scraper_one/facebook-posts-search`.

Row normalization written against the actor's **actual live output shape**, verified
2026-10-15 (see below) — NOT the shape `AnyMindGroup/anyagents`'s
`fetch_sns/fetchers/facebook_search_fetcher.py` assumed (`likes`/`likesCount`/`content`/
`videoUrl` top-level fields). The actor's output has visibly drifted since that code was
written; this module trusts the live-verified shape, not the ported one.

Verified live shape (two real calls, query "Nike" / "Nike shoes review", `searchType=top`):
- `postText` (not `content`/`text`/`message`) — caption
- `timestamp` — **epoch milliseconds**, not a date string
- `reactionsCount` — total reactions across all reaction types (not a "likes-only" count;
  FB's UI itself no longer distinguishes a separate plain-like count front-and-center, so this
  is reported as `likes` for continuity with the rest of this skill's schema, but is really
  "total positive reactions" — do not present it to a user as literally "like count")
- `commentsCount`, `sharesCount` — present and correctly named, unlike `reactionsCount`
- `author.name`, `author.profileUrl` — poster identity
- `attachments[]` — `{"type": "video"|"photo"|..., "url": "..."}`; a video post's direct CDN
  URL lives at `attachments[n].url` where `type == "video"`
- **No `views`/`viewsCount` field anywhere in either sample** — Facebook does not expose a
  view count via this actor at all (not "sometimes", at least not observed once in these
  checks); the engagement-weighted fallback in `rank_and_select.py` is the only ranking
  signal for Facebook until/unless a future check finds a view-count-bearing result.

Only video posts are kept — photo-only posts are filtered out here (mirrors Instagram's
`resultsType=reels` pre-filter) since this skill's job is video discovery.

**`location` input** (verified live 2026-10-16 against the actor's real input schema):
filters to posts with a **pinned** Facebook location tag matching the given name/ID — this is
NOT a general country/region filter. Most ordinary brand marketing content carries no pinned
location at all, so this field will under-match for a "scope to country X" goal; it's exposed
here as an optional narrow supplementary filter, not the primary country-scoping mechanism
(see INSTRUCTIONS.md "Country/market scoping" for the actual recommended approach — localized
query variants, cross-checked by Gemini during Stage 4 analysis).

**Multiple queries**: unlike the Instagram actor, this actor's `query` field is a single
required string (no native multi-term support) — each query variant costs its own actor call.
Results across calls are deduped by `postId` before ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apify_runner import run_actor_guarded
from field_merge import fill_missing_fields
from ytdlp_metadata import fetch_ytdlp_metadata

ACTOR_ID = "scraper_one/facebook-posts-search"

# Fields the yt-dlp fallback is allowed to fill in direct URL mode — not `video_url`/
# `caption`/`username`, which stay strictly actor-sourced (yt-dlp's `uploader` field isn't a
# reliable match for `username`'s meaning here, and mixing video-file-discovery concerns into
# this metadata merge isn't this function's job).
_YTDLP_FILLABLE_FIELDS = ["views", "likes", "comments", "shares"]

# "By URL" actor (added 2026-10-17): for when the caller already has specific post/reel
# URLs rather than a search query — see fetch_facebook_by_urls below. Its output shape is
# COMPLETELY DIFFERENT from ACTOR_ID's (a deeply-nested GraphQL-shaped payload, not the flat
# postText/reactionsCount/attachments[] shape above), verified live 2026-10-17 — needs its
# own normalizer, _normalize_direct_item, not a reuse of _row.
DIRECT_ACTOR_ID = "apify/facebook-posts-scraper"


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _video_url(item: dict[str, Any]) -> str:
    for att in item.get("attachments") or []:
        if isinstance(att, dict) and att.get("type") == "video" and att.get("url"):
            return str(att["url"])
    return ""


def _posted_at(item: dict[str, Any]) -> str:
    ms = _as_int(item.get("timestamp"))
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _row(item: dict[str, Any]) -> dict[str, Any] | None:
    video_url = _video_url(item)
    if not video_url:
        return None  # photo/link/other non-video post — out of scope for this skill

    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    return {
        "platform": "facebook",
        "post_id": str(item.get("postId") or ""),
        "url": str(item.get("url") or ""),
        "posted_at": _posted_at(item),
        "username": str(author.get("name") or ""),
        "caption": str(item.get("postText") or ""),
        "likes": _as_int(item.get("reactionsCount")),  # total reactions, see module docstring
        "comments": _as_int(item.get("commentsCount")),
        "shares": _as_int(item.get("sharesCount")),
        "views": None,  # not exposed by this actor — see module docstring
        "video_url": video_url,
        "thumbnail_url": "",
    }


def _epoch_seconds_to_iso(value: Any) -> str:
    """`apify/facebook-posts-scraper`'s `creation_time` is epoch SECONDS (verified live
    2026-10-17 — a 10-digit value that converts to a sane date, unlike ACTOR_ID's `timestamp`
    above which is epoch milliseconds). Two different actors, two different units — do not
    assume they match."""
    secs = _as_int(value)
    if secs is None:
        return ""
    try:
        return datetime.fromtimestamp(secs, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


_PAGENAME_PLACEHOLDER_VALUES = {"reel"}


def _normalize_direct_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalizes `apify/facebook-posts-scraper`'s output — which has (at least) TWO
    DIFFERENT, INCOMPATIBLE SHAPES depending on the input URL's own format, both verified
    live (2026-10-17 for "Shape A", 2026-10-18 for "Shape B" after a `fb.watch` short-link
    input surfaced it):

    - **Shape A** (`/reel/<id>` URLs that stay reel posts): nested payload with
      `message.text` (caption), `total_comment_count`, `share_count_reduced`, `creation_time`
      (epoch seconds), a real `pageName`.
    - **Shape B** (`fb.watch` short links, which the actor internally canonicalizes to a BARE
      VIDEO NODE rather than a post): no `message` key at all (caption is unrecoverable from
      this actor for this shape — a genuine gap, not a bug), no `total_comment_count`/
      `share_count_reduced` keys, `publish_time` instead of `creation_time`, and — a real
      trap caught live — **`pageName` comes back as the literal string `"reel"`**, which is
      not a page name at all (looks like an internal placeholder the actor emits when it
      can't resolve the real page for this canonicalized shape). Treating it as real data
      would show "reel" as the video's uploader handle, confidently wrong.

    Fields handled to work across BOTH shapes:
    - `comments`: a plain int field present in BOTH shapes with the SAME value as Shape A's
      `total_comment_count` (confirmed by comparing both on the same real items) — used as
      the primary source instead of `total_comment_count`, with that as a defensive fallback.
    - `posted_at`: `creation_time` (Shape A) or `publish_time` (Shape B) — both epoch seconds.
    - `username`: `pageName`, UNLESS it's the known placeholder value, in which case left
      blank (never fabricate a handle) — see `_PAGENAME_PLACEHOLDER_VALUES`.
    - `shares`: only Shape A ever carries this (`share_count_reduced`) — Shape B genuinely
      has no share data anywhere in the payload (confirmed by walking its full structure),
      so `shares` stays `None` for Shape B rows; this is a real gap, not a parsing miss.

    **No views/play-count field in EITHER shape** (confirmed by walking the full nested
    structure of both, including `short_form_video_context.playback_video`) — see
    `fetch_facebook_by_urls`'s yt-dlp fallback for how this gap actually gets filled in
    practice for most posts.

    **No direct CDN video URL** in either shape either (Facebook's video delivery is behind
    DASH manifests, not a plain field) — `video_url` is left empty; `download.py` already
    tries the post's own page URL (`row["url"]`) with yt-dlp FIRST, which is what actually
    downloads the file in practice, so this is not a functional gap."""
    message = item.get("message")
    caption = (message or {}).get("text", "") if isinstance(message, dict) else ""
    page_name = item.get("pageName")
    username = "" if page_name in _PAGENAME_PLACEHOLDER_VALUES else str(page_name or "")
    posted_at_raw = item.get("creation_time") if item.get("creation_time") is not None else item.get("publish_time")
    return {
        "platform": "facebook",
        "post_id": str(item.get("post_id") or item.get("id") or ""),
        "url": str(item.get("facebookUrl") or ""),
        "posted_at": _epoch_seconds_to_iso(posted_at_raw),
        "username": username,
        "caption": caption,
        "likes": _as_int(item.get("likes")),
        "comments": _as_int(item.get("comments")) if item.get("comments") is not None else _as_int(item.get("total_comment_count")),
        "shares": _as_int(item.get("share_count_reduced")),
        "views": None,
        "video_url": "",
        "thumbnail_url": "",
    }


def _dedupe_by_post_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped = []
    for row in rows:
        pid = row.get("post_id") or row.get("url")
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        deduped.append(row)
    return deduped


def search_facebook(
    queries: list[str],
    limit: int,
    *,
    search_type: str = "top",
    location: str | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Search one or more query variants (one actor call per query — see module docstring)
    and return (deduped video-only rows, raw candidate count across all calls before
    dedup/video-filtering, a cost-summary entry per call made).

    `date_after`/`date_before` (each `"YYYY-MM-DD"`) map directly to this actor's own
    `startDate`/`endDate` input fields (verified live 2026-10-16 against the actor's real
    schema) — this is a genuine search-time boundary, not a post-hoc filter, so passing a
    recency window here is both more accurate and cheaper than fetching unbounded results and
    discarding old ones client-side (Instagram has no equivalent; see date_filter.py).

    The raw count is reported separately from `len(rows)` because Facebook's "top" search
    for a bare brand keyword often returns mostly photo posts — a brand/query with few or no
    native videos will legitimately show a raw count much larger than its video count, and
    collapsing the two would hide that fact rather than reporting it (Evidence Policy).

    Raises (propagated from `run_actor_guarded`) on the FIRST query that needs approval or
    hits a configuration error — later queries in the list are never attempted once an
    earlier one raises, matching this skill's "never silently proceed past an unapproved
    call" rule.
    """
    if not queries:
        raise ValueError("search_facebook requires at least one query")

    raw_rows: list[dict[str, Any]] = []
    cost_entries: list[dict[str, Any]] = []
    for query in queries:
        run_input: dict[str, Any] = {
            "query": query,
            "resultsCount": max(1, int(limit)),
            "searchType": search_type,
        }
        if location:
            run_input["location"] = location
        if date_after:
            run_input["startDate"] = date_after
        if date_before:
            run_input["endDate"] = date_before
        items, estimate = run_actor_guarded(ACTOR_ID, run_input, approvals_dir=approvals_dir)
        raw_rows.extend(i for i in items if isinstance(i, dict))
        cost_entries.append({
            "actor_id": ACTOR_ID,
            "quantity": estimate.quantity,
            "estimated_usd": round(estimate.estimated_usd, 4),
        })

    video_rows = [r for i in raw_rows if (r := _row(i)) is not None]
    rows = _dedupe_by_post_id(video_rows)
    return rows, len(raw_rows), cost_entries


def fetch_facebook_by_urls(
    urls: list[str],
    *,
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Fetch specific Facebook post/reel URLs directly via `apify/facebook-posts-scraper`
    (given a page URL it would crawl N posts from that page; given a direct post/reel URL —
    our case here — it returns just that one post, its own `resultsLimit` is documented as
    ignored in that mode). No ranking/top-N concept applies: every given URL that resolves is
    returned, nothing is dropped for "not good enough" — unlike `search_facebook`, this does
    NOT filter out non-video posts, since the caller already chose these specific URLs on
    purpose; a non-video URL just won't have a downloadable video later (download.py reports
    that honestly, it is not this function's job to pre-judge it).

    One actor call for the whole URL list — `startUrls` accepts multiple URLs natively.

    After the actor call, each row is cross-checked against `fetch_ytdlp_metadata` (one
    yt-dlp subprocess call per URL — acceptable overhead only because direct URL mode is
    low-volume by nature, never done in `search_facebook`) and any of
    views/likes/comments/shares still `None` gets filled from whichever source actually has
    it — see `field_merge.fill_missing_fields`. This exists because the two sources have
    DIFFERENT, non-overlapping gaps (verified live 2026-10-17/18): the actor never exposes
    views at all, but yt-dlp's own page-scrape sometimes does; conversely yt-dlp often misses
    likes/comments/shares that the actor has. A yt-dlp failure for one URL (private/removed/
    timeout) never aborts the batch — `fetch_ytdlp_metadata` returns `{}` on any failure, so
    that row just keeps whatever the actor already gave it.

    Returns (normalized rows in input order, same count since there is no filtering/dedup
    step here, a one-entry cost-summary list — kept as a list for interface uniformity with
    `search_facebook`)."""
    if not urls:
        raise ValueError("fetch_facebook_by_urls requires at least one URL")

    run_input = {"startUrls": [{"url": u} for u in urls]}
    items, estimate = run_actor_guarded(DIRECT_ACTOR_ID, run_input, approvals_dir=approvals_dir)
    rows = [_normalize_direct_item(i) for i in items if isinstance(i, dict)]

    merged_rows = []
    for row in rows:
        fallback = fetch_ytdlp_metadata(row["url"]) if row.get("url") else {}
        merged_rows.append(fill_missing_fields(row, fallback, _YTDLP_FILLABLE_FIELDS))

    cost_entry = {
        "actor_id": DIRECT_ACTOR_ID,
        "quantity": estimate.quantity,
        "estimated_usd": round(estimate.estimated_usd, 4),
    }
    return merged_rows, len(merged_rows), [cost_entry]


def _normalize_page_feed_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalizes `apify/facebook-posts-scraper`'s output when crawling a PAGE's feed
    (`startUrls` pointed at a page URL, not a direct post/reel URL) — a THIRD shape from this
    same actor, verified live 2026-10-18, distinct from both Shape A and Shape B in
    `_normalize_direct_item`'s docstring:
    - `likes`, `comments`, `shares` are all plain top-level ints (unlike Shape A's nested
      `total_comment_count`/`share_count_reduced`) — though `comments` was observed `None` on
      most low-engagement posts in a real page crawl even when `likes`/`shares` had real
      values; unclear if that's a real absence or an actor quirk, so it's passed through as
      whatever the actor gave (None stays None, never assumed to be 0).
    - `text` — caption (plain field, unlike Shape A's nested `message.text`)
    - `postId`, `url` — `url` here is the REAL per-post permalink (unlike Shape B's
      `facebookUrl`, which in page-feed mode is just the page URL repeated on every row)
    - `pageName` IS reliable in this shape (confirmed a real handle, e.g. "SOFYMalaysia" —
      not Shape B's "reel" placeholder bug)
    - `timestamp` — epoch SECONDS, same convention as Shape A's `creation_time`
    - `media[].__typename` — `"Video"` vs `"Photo"` lets this shape (uniquely) distinguish
      video posts from photo posts BEFORE normalizing, unlike Shapes A/B which are always a
      single already-known post. Returns `None` for a non-video post — mirrors `_row`'s
      video-only filtering for the search actor.
    """
    media = item.get("media") or []
    is_video = any(isinstance(m, dict) and m.get("__typename") == "Video" for m in media)
    if not is_video:
        return None
    return {
        "platform": "facebook",
        "post_id": str(item.get("postId") or ""),
        "url": str(item.get("url") or ""),
        "posted_at": _epoch_seconds_to_iso(item.get("timestamp")),
        "username": str(item.get("pageName") or ""),
        "caption": str(item.get("text") or ""),
        "likes": _as_int(item.get("likes")),
        "comments": _as_int(item.get("comments")),
        "shares": _as_int(item.get("shares")),
        "views": None,
        "video_url": "",
        "thumbnail_url": "",
    }


def fetch_facebook_by_pages(
    page_urls: list[str],
    candidate_pool: int,
    *,
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Discover videos from one or more Facebook PAGE feeds (account/page URLs, e.g.
    "https://www.facebook.com/SOFYMalaysia/") via `apify/facebook-posts-scraper`'s native
    page-crawl mode (`startUrls` pointed at a page, not a direct post — `resultsLimit` caps
    posts scraped PER page here, unlike `fetch_facebook_by_urls`'s direct-post mode where it's
    ignored). This is "account mode": a DISCOVERY step like `search_facebook` (scan a
    candidate pool, filter to video posts, let the caller rank/cut to top-N) — NOT like
    `fetch_facebook_by_urls`'s "keep everything" direct URL mode, since the caller is pointing
    at an account's whole feed, not naming specific videos they already picked.

    One actor call for the whole page list — `startUrls` accepts multiple page URLs natively
    (confirmed by the same actor's already-proven multi-URL support in `fetch_facebook_by_urls`).

    Non-video posts (photos, text-only, link shares) are filtered out via
    `_normalize_page_feed_item`'s `media[].__typename` check — this shape is the only one of
    the three this actor can return where that distinction is even knowable before picking a
    specific post, since a page feed mixes photo and video content.

    Each kept row is cross-checked against `fetch_ytdlp_metadata` exactly like
    `fetch_facebook_by_urls` (same non-overlapping-gaps rationale — see that function's
    docstring).

    Returns (deduped + merged + video-only rows, raw scanned-post count across all pages
    BEFORE video filtering — mirrors `search_facebook`'s `candidates_scanned` semantics, a
    one-entry cost-summary list)."""
    if not page_urls:
        raise ValueError("fetch_facebook_by_pages requires at least one page URL")

    run_input = {"startUrls": [{"url": u} for u in page_urls], "resultsLimit": max(1, int(candidate_pool))}
    items, estimate = run_actor_guarded(DIRECT_ACTOR_ID, run_input, approvals_dir=approvals_dir)
    raw_items = [i for i in items if isinstance(i, dict)]

    video_rows = [r for i in raw_items if (r := _normalize_page_feed_item(i)) is not None]
    rows = _dedupe_by_post_id(video_rows)

    merged_rows = []
    for row in rows:
        fallback = fetch_ytdlp_metadata(row["url"]) if row.get("url") else {}
        merged_rows.append(fill_missing_fields(row, fallback, _YTDLP_FILLABLE_FIELDS))

    cost_entry = {
        "actor_id": DIRECT_ACTOR_ID,
        "quantity": estimate.quantity,
        "estimated_usd": round(estimate.estimated_usd, 4),
    }
    return merged_rows, len(raw_items), [cost_entry]

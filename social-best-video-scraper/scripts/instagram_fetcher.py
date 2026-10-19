"""Instagram hashtag search via `apify/instagram-hashtag-scraper`.

Row normalization ported from `AnyMindGroup/anyagents`'s
`fetch_sns/sources/instagram/adapters/apify.py` (`InstagramApifySearchAdapter._normalize_item`),
trimmed to the fields this skill needs (drops `fetch-sns`'s full multi-provider /
multi-platform result envelope).

Multiple hashtags (e.g. an English + a local-language variant for a specific target market —
see INSTRUCTIONS.md "Country/market scoping") are sent as ONE actor call, matching the
actor's own native multi-hashtag support (verified live 2026-10-16 against the actor's real
input schema: `resultsLimit` applies PER hashtag, so N hashtags costs ~N× a single hashtag).
Results are deduped by post_id before ranking — the same post can surface under more than one
searched hashtag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apify_runner import run_actor_guarded
from field_merge import fill_missing_fields
from ytdlp_metadata import fetch_ytdlp_metadata

ACTOR_ID = "apify/instagram-hashtag-scraper"

# Fields the yt-dlp fallback is allowed to fill in direct URL mode. In practice this actor
# already covers views/likes/comments itself (verified live 2026-10-17/18) — `shares` is the
# one field yt-dlp could theoretically help with, though live testing found yt-dlp doesn't
# expose it for Instagram either (platform-level gap, not a parsing miss; see
# reference/apify-actors.md). Kept as a real fallback attempt anyway, not hardcoded off, in
# case that changes — this mirrors facebook_fetcher's identical merge step for consistency
# and defense-in-depth against a future actor regression.
_YTDLP_FILLABLE_FIELDS = ["views", "likes", "comments", "shares"]

# "By URL" actor (added 2026-10-17): for when the caller already has specific post/reel
# URLs rather than a hashtag to search — see fetch_instagram_by_urls below. Confirmed live
# 2026-10-17 that its dataset items use the EXACT SAME field names as ACTOR_ID's output
# (shortCode/likesCount/commentsCount/videoPlayCount/videoUrl/etc — both are `apify`-org
# actors sharing the same underlying IG scraping schema), so _normalize_item is reused
# as-is — no separate normalizer needed for this actor.
DIRECT_ACTOR_ID = "apify/instagram-scraper"


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_nonneg_int(value: Any) -> int | None:
    """Instagram returns -1 for a count the poster has hidden (e.g. "hide like count"),
    not a real negative number — treat it the same as missing, never report a post as
    having "-1 likes"."""
    n = _as_int(value)
    return None if n is not None and n < 0 else n


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    short_code = item.get("shortCode", "")
    owner = item.get("ownerUsername", "")
    return {
        "platform": "instagram",
        "post_id": str(item.get("id") or short_code or ""),
        "url": item.get("url") or (f"https://www.instagram.com/p/{short_code}/" if short_code else ""),
        "posted_at": str(item.get("timestamp") or ""),
        "username": owner,
        "caption": item.get("caption") or "",
        "likes": _as_nonneg_int(item.get("likesCount")),
        "comments": _as_nonneg_int(item.get("commentsCount")),
        # None, not 0 — Instagram does not expose a share count on this actor at all. A
        # literal 0 would misreport "we observed zero shares" (a verified fact) when the
        # truth is "this platform never told us" — caught live 2026-10-16 when
        # social-video-tag-analyzer started surfacing this field directly in its output
        # sheet and a hardcoded 0 would have looked like real data.
        "shares": None,
        "views": _as_nonneg_int(item.get("videoPlayCount")) or _as_nonneg_int(item.get("videoViewCount")),
        "video_url": item.get("videoUrl") or "",
        "thumbnail_url": item.get("displayUrl") or "",
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


def search_instagram(
    hashtags: list[str],
    limit: int,
    *,
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Search one or more hashtags (one actor call — the actor natively supports multiple
    hashtags per call) and return (deduped normalized rows, raw candidate count before
    dedup, a one-entry cost-summary list — kept as a list for interface uniformity with
    `search_facebook`, which makes one call per query and so returns one entry per call).
    `resultsType=reels` pre-filters to video content server-side.

    Returns `([], 0, [cost_entry])` on a zero-result search — never raises for "no posts
    found", only for configuration/approval problems (those propagate from
    `run_actor_guarded`).
    """
    if not hashtags:
        raise ValueError("search_instagram requires at least one hashtag")

    run_input = {
        "hashtags": list(hashtags),
        "resultsLimit": max(1, int(limit)),
        # "reels" (not "posts"): we need video content, and in practice "posts" crawls the
        # hashtag's chronological "Recent" grid (near-zero engagement, newest first, mixed
        # photo/video) rather than anything performance-ranked. Verified live 2026-10-15
        # against #nike: resultsType=posts returned 5 photo-only results, all posted within
        # the same minute as the call, all with likes/comments at 0; resultsType=reels
        # returned 5 real videos spanning several days with real view counts (115K/67K/
        # 54K/44K/39K, correctly descending) and populated likes/comments. "reels" is the
        # verified, correct choice for this skill's "best performing video" goal.
        "resultsType": "reels",
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }
    items, estimate = run_actor_guarded(ACTOR_ID, run_input, approvals_dir=approvals_dir)
    raw_rows = [_normalize_item(i) for i in items if isinstance(i, dict)]
    rows = _dedupe_by_post_id(raw_rows)
    cost_entry = {
        "actor_id": ACTOR_ID,
        "quantity": estimate.quantity,
        "estimated_usd": round(estimate.estimated_usd, 4),
    }
    return rows, len(raw_rows), [cost_entry]


def fetch_instagram_by_urls(
    urls: list[str],
    *,
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Fetch specific Instagram post/reel URLs directly via `apify/instagram-scraper`'s
    `directUrls` mode — for a caller that already has exact video URLs (e.g. links a user
    pasted in), not a hashtag to search. No ranking/top-N concept applies here: every given
    URL that resolves is returned, nothing is dropped for "not good enough."

    The actor's own schema (verified live 2026-10-17) requires `resultsType` to match the
    URL's own path: `/reel/` URLs need `resultsType="reels"`, everything else (`/p/...`) needs
    `"posts"` — mixing them in one call under the wrong type returns nothing for the
    mismatched URLs. URLs are split into the two groups client-side and sent as (at most) two
    actor calls, each billed only for the URLs it actually contains.

    After dedup, each row is cross-checked against `fetch_ytdlp_metadata` and any of
    views/likes/comments/shares still `None` gets filled from whichever source has it — see
    `field_merge.fill_missing_fields` and `facebook_fetcher.fetch_facebook_by_urls`'s matching
    step for the full rationale (the two platforms' fetchers share this pattern).

    Returns (deduped + merged normalized rows, raw count before dedup, one cost entry per
    actor call made — 1 or 2 depending on whether both URL shapes were present).
    """
    if not urls:
        raise ValueError("fetch_instagram_by_urls requires at least one URL")

    reel_urls = [u for u in urls if "/reel/" in u]
    post_urls = [u for u in urls if "/reel/" not in u]

    raw_rows: list[dict[str, Any]] = []
    cost_entries: list[dict[str, Any]] = []
    for url_group, results_type in ((reel_urls, "reels"), (post_urls, "posts")):
        if not url_group:
            continue
        run_input = {"directUrls": url_group, "resultsType": results_type, "resultsLimit": 1}
        items, estimate = run_actor_guarded(DIRECT_ACTOR_ID, run_input, approvals_dir=approvals_dir)
        raw_rows.extend(_normalize_item(i) for i in items if isinstance(i, dict))
        cost_entries.append({
            "actor_id": DIRECT_ACTOR_ID,
            "quantity": estimate.quantity,
            "estimated_usd": round(estimate.estimated_usd, 4),
        })

    rows = _dedupe_by_post_id(raw_rows)
    merged_rows = []
    for row in rows:
        fallback = fetch_ytdlp_metadata(row["url"]) if row.get("url") else {}
        merged_rows.append(fill_missing_fields(row, fallback, _YTDLP_FILLABLE_FIELDS))
    return merged_rows, len(raw_rows), cost_entries


_ACCOUNT_CONTENT_TYPE_TO_RESULTS_TYPE = {
    "own": "reels",
    "tagged": "mentions",
}


def fetch_instagram_by_accounts(
    account_urls: list[str],
    candidate_pool: int,
    *,
    content_type: str = "own",
    approvals_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Discover videos associated with one or more Instagram accounts (profile URLs, e.g.
    "https://www.instagram.com/sofysingapore/") via the same `apify/instagram-scraper` actor's
    `directUrls` mode — but pointed at a profile URL instead of a specific post/reel URL.
    Two distinct sub-modes, both verified live (2026-10-18/19), both using this SAME actor and
    `_normalize_item` (no new normalizer needed either way — identical field names):

    - `content_type="own"` (default) → `resultsType="reels"`: that account's OWN video/reel
      content (real `videoPlayCount` values). `resultsType="posts"` on the same profile URL
      was tested too and returns a mix of photos/carousels instead, confirming `"reels"` is
      correct here, same reasoning as `search_instagram`'s existing `"reels"` choice.
    - `content_type="tagged"` → `resultsType="mentions"`: videos OTHER accounts posted that
      tagged/mentioned this account — i.e. the account's "Tagged" tab (a user asked for this
      by name — "不是这个达人自己的reels，而是tagged这个页面里的视频" — 2026-10-19). Verified
      live against a real account: returns real videos from DIFFERENT `ownerUsername` values
      (not the named account itself), each with its own real `videoPlayCount`/`likesCount` —
      this is genuinely a different content set, not a filtered view of "own". Useful for
      finding UGC/KOL content about a brand that the brand's own account never posted.

    This is "account mode": a DISCOVERY step like `search_instagram` (scan a candidate pool,
    let the caller rank/cut to top-N) — NOT like `fetch_instagram_by_urls`'s "keep everything"
    direct URL mode, since the caller is pointing at an account's whole feed (or tagged-in
    content), not naming specific videos they already picked.

    One actor call for the whole account list — `directUrls` accepts multiple profile URLs
    natively (same documented multi-value support already relied on for hashtag batching and
    multi-post direct-URL mode elsewhere in this module). All accounts in one call share the
    same `content_type` — call this function twice (once per `content_type`) if a caller wants
    both an account's own content AND its tagged content.

    `resultsLimit` here caps results PER account (matches the actor's own per-URL semantics
    used everywhere else in this skill).

    Each row is cross-checked against `fetch_ytdlp_metadata` exactly like
    `fetch_instagram_by_urls` (same non-overlapping-gaps rationale).

    Returns (deduped + merged rows, raw count before dedup, one cost entry)."""
    if not account_urls:
        raise ValueError("fetch_instagram_by_accounts requires at least one account URL")
    if content_type not in _ACCOUNT_CONTENT_TYPE_TO_RESULTS_TYPE:
        raise ValueError(f"content_type must be one of {sorted(_ACCOUNT_CONTENT_TYPE_TO_RESULTS_TYPE)}, got {content_type!r}")

    results_type = _ACCOUNT_CONTENT_TYPE_TO_RESULTS_TYPE[content_type]
    run_input = {"directUrls": list(account_urls), "resultsType": results_type, "resultsLimit": max(1, int(candidate_pool))}
    items, estimate = run_actor_guarded(DIRECT_ACTOR_ID, run_input, approvals_dir=approvals_dir)
    raw_rows = [_normalize_item(i) for i in items if isinstance(i, dict)]
    rows = _dedupe_by_post_id(raw_rows)

    merged_rows = []
    for row in rows:
        fallback = fetch_ytdlp_metadata(row["url"]) if row.get("url") else {}
        merged_rows.append(fill_missing_fields(row, fallback, _YTDLP_FILLABLE_FIELDS))

    cost_entry = {
        "actor_id": DIRECT_ACTOR_ID,
        "quantity": estimate.quantity,
        "estimated_usd": round(estimate.estimated_usd, 4),
    }
    return merged_rows, len(raw_rows), [cost_entry]

#!/usr/bin/env python3
"""CLI entrypoint for social-best-video-scraper.

Usage:
    uv run python .claude/skills/social-best-video-scraper/scripts/run.py \\
      --brands-json '[{"brand": "Brand A", "hashtags": ["branda", "branda_th"]}, {"brand": "Brand B"}]' \\
      --platforms instagram,facebook \\
      --per-platform-limit '{"instagram": 30, "facebook": 10}' \\
      --candidate-pool 60 \\
      --target-country Thailand \\
      --months-back 3 \\
      --out-dir output/social-best-video-scraper/run1

`--per-platform-limit` takes EITHER a plain integer (applied to every platform in this run)
OR a JSON object giving each platform its own count, e.g. the example above (added 2026-10-19
— a real requirement: different platforms legitimately need different counts in the same run,
not a guess at a nice-to-have). `--platforms` already let the caller pick a subset of
instagram/facebook; this makes "how many" equally a per-platform, per-run choice instead of
one number applied blindly everywhere.

`--brands-json` accepts either an inline JSON string or a path to a .json file. Each entry:
  {"brand": str (required),
   "hashtags": [str, ...]           (optional — Instagram search terms, one actor call
                                     searches all of them at once; falls back to the single
                                     "hashtag" key, then to one derived from the brand name if
                                     neither is given),
   "queries": [str, ...]            (optional — Facebook search terms, one actor call PER
                                     query; falls back to [brand] if not given),
   "fb_location": str               (optional — Facebook's pinned-location filter; narrow, see
                                     INSTRUCTIONS.md "Country/market scoping" before relying on
                                     it as a country filter),
   "instagram_urls": [str, ...]     (optional, added 2026-10-17 — specific IG post/reel URLs
                                     the caller already has, e.g. pasted by a user. HIGHEST
                                     priority for this brand's Instagram side: when given,
                                     both hashtag search and account mode are skipped),
   "facebook_urls": [str, ...]      (optional, same idea for Facebook — priority over
                                     facebook_pages/queries),
   "instagram_accounts": [str, ...] (optional, added 2026-10-19 — IG account/profile URLs,
                                     e.g. "https://www.instagram.com/someaccount/". Discovers
                                     best-performing videos FROM THESE SPECIFIC ACCOUNTS —
                                     e.g. a known KOL or competitor account — instead of a
                                     hashtag search. Priority over hashtags/hashtag, but below
                                     instagram_urls — see "Account input mode" below),
   "facebook_pages": [str, ...]     (optional, same idea for Facebook — priority over
                                     queries, below facebook_urls),
   "instagram_tagged_accounts": [str, ...] (optional, IG only, added 2026-10-19 — videos OTHER
                                     accounts posted that tagged/mentioned this account, i.e.
                                     its "Tagged" tab, not its own posts. Can combine with
                                     instagram_accounts on the same entry — both get scanned,
                                     results combined. No Facebook equivalent yet)}

A derived/single hashtag is a weak default — small/niche brands may get zero Instagram
results. See INSTRUCTIONS.md "Country/market scoping" for why, when `--target-country` is
set, the CALLER (not this script) should supply localized hashtag/query variants per brand —
this script does not translate anything itself.

`instagram_urls`/`facebook_urls` ("direct URL mode", see INSTRUCTIONS.md "Direct URL input
mode") is a fundamentally different shape of input than hashtag/query search: the caller
already knows exactly which videos they want, so there is nothing to rank down to a top-N
from — EVERY given URL that resolves is kept (no `--per-platform-limit` truncation), and
`--months-back` recency filtering does not apply (the caller chose these specific videos on
purpose; silently dropping an "old" one they explicitly asked for would be wrong, not safe).
`--candidate-pool` has no meaning here either (there is no scan-then-rank step, one URL = one
result). A brand/platform using direct URL mode records `"discovery_mode": "direct_url"` in
the manifest instead of `"search"`, and `search_terms_used` holds the URLs themselves.

Direct URL mode also multi-sources each row's views/likes/comments/shares — the Apify "by
URL" actor result is cross-checked against a yt-dlp metadata fetch, filling whichever fields
the actor's own output left blank (see instagram_fetcher.py / facebook_fetcher.py — the two
sources have different, non-overlapping gaps, verified live). Whatever is STILL `None` after
that gets a plain-language reason in each row's `data_gap_note` field (see data_gaps.py) —
written for a business reader, never in terms of which tool/actor was used.

`instagram_accounts`/`facebook_pages` ("account mode", added 2026-10-19, see INSTRUCTIONS.md
"Account input mode") is for when the caller has a specific KOL/competitor ACCOUNT (not a
specific video, not a hashtag) and wants that account's best-performing recent videos. Unlike
direct URL mode, this IS a discovery step — like hashtag/query search, it scans a candidate
pool from the account's feed, applies `--months-back` recency filtering, and ranks down to
`--per-platform-limit` via `rank_and_select()`. Verified live: both actors natively support
crawling a profile/page URL's own feed (not just a single named post) — `apify/
instagram-scraper` with `resultsType="reels"` on a profile URL returns that account's reels
directly; `apify/facebook-posts-scraper` already supports page URLs as `startUrls` (its
documented primary use case, just not one this skill had used until now). Account mode also
gets the same yt-dlp multi-source merge as direct URL mode (see above) — same non-overlapping-
gaps rationale, same actors, same gap-filling logic.

`--target-country` is recorded in the manifest for `social-video-tag-analyzer` to pick up
(its Gemini pass can then assess per-video market/language match) — it does not filter
Apify's results itself (neither actor supports a true country filter; see reference/
apify-actors.md).

`--months-back` (default 3 — "historical content isn't useful" is this skill's default
assumption, not an opt-in) scopes results to recent content. Facebook supports this natively
(the actor's own `startDate`, a real search-time boundary); Instagram has no date parameter at
all, so it's an after-the-fact filter on whatever finite sample the hashtag search returned —
see INSTRUCTIONS.md "Recency scoping" for what this does and doesn't guarantee. Pass
`--months-back 0` to disable the window entirely (an explicit choice, not a silent default).

On an APIFY_APPROVAL_REQUIRED sentinel, this script exits non-zero with the sentinel block on
stdout and does NOT write a partial manifest for the brand/platform that triggered it — the
calling agent must surface the block verbatim, get approval, call
`apify_guard.mark_approved(request_hash, approvals_dir)`, and rerun.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apify_guard import ApifyApprovalRequired
from apify_runner import ApifyNotConfigured
from data_gaps import explain_data_gaps
from date_filter import DEFAULT_MONTHS_BACK, compute_cutoff_date, filter_within_date_window
from download import download_video
from facebook_fetcher import fetch_facebook_by_pages, fetch_facebook_by_urls, search_facebook
from instagram_fetcher import fetch_instagram_by_accounts, fetch_instagram_by_urls, search_instagram
from rank_and_select import rank_and_select

# Widened from 10 to 20 (2026-10-16) so social-video-tag-analyzer's downstream frequency
# analysis can draw two INDEPENDENT top-N selections (by Views, by Engagement) from one
# tagged batch per brand/platform — a top-10-by-views set and a top-10-by-engagement set
# can be almost entirely different videos. Does not change Apify cost: this is the KEPT/
# downloaded count, not the scan pool (DEFAULT_CANDIDATE_POOL below, which Apify bills on).
DEFAULT_PER_PLATFORM_LIMIT = 20
DEFAULT_CANDIDATE_POOL = 60


def _derive_hashtag(brand: str) -> str:
    return re.sub(r"[^a-z0-9]", "", brand.lower())


def _slug(brand: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-")
    return slug or "brand"


def _load_brands(raw: str) -> list[dict]:
    path = Path(raw)
    text = path.read_text(encoding="utf-8") if path.exists() else raw
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("--brands-json must be a JSON array of {brand, hashtags?/queries?} objects")
    return data


def _resolve_hashtags(entry: dict) -> list[str]:
    if entry.get("hashtags"):
        return list(entry["hashtags"])
    if entry.get("hashtag"):
        return [entry["hashtag"]]
    return [_derive_hashtag(entry["brand"])]


def _resolve_queries(entry: dict) -> list[str]:
    if entry.get("queries"):
        return list(entry["queries"])
    return [entry["brand"]]


def _parse_per_platform_limit(raw: str, platforms: list[str]) -> dict[str, int]:
    """`--per-platform-limit` accepts EITHER a plain integer (applied to every platform in
    this run — the common case) OR a JSON object giving each platform its own count, e.g.
    `'{"instagram": 30, "facebook": 10}'` — added 2026-10-19 because real usage needs
    different counts per platform in one run (an explicit user requirement, not a guess: a
    global single number can't express "30 from IG, 10 from FB"). A platform in `platforms`
    missing from the JSON object is an error, not a silent default — better to fail loudly
    than silently analyze 0 videos for a platform the caller explicitly asked for."""
    try:
        n = int(raw)
        return dict.fromkeys(platforms, n)
    except ValueError:
        pass

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--per-platform-limit must be an integer or a JSON object like "
            f'\'{{"instagram": 30, "facebook": 10}}\': {exc}'
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("--per-platform-limit JSON must be an object mapping platform name to an integer count")

    missing = [p for p in platforms if p not in parsed]
    if missing:
        raise ValueError(f"--per-platform-limit JSON is missing an entry for: {', '.join(missing)}")
    return {p: int(parsed[p]) for p in platforms}


def _process_brand_platform(
    entry: dict,
    platform: str,
    *,
    candidate_pool: int,
    per_platform_limit: int,
    approvals_dir: Path,
    cutoff: datetime | None,
) -> tuple[dict, list[dict]]:
    """Returns (brand_result, cost_entries). Raises ApifyApprovalRequired / ApifyNotConfigured
    to the caller — those are run-stopping, not per-brand failures to swallow.

    `cutoff` is None when recency scoping is disabled (--months-back 0) — in that case no
    date filtering happens at all, on either platform. Direct URL mode (see module docstring)
    never applies date filtering or per_platform_limit truncation regardless of `cutoff`."""
    brand = entry["brand"]
    direct_urls = entry.get("instagram_urls") if platform == "instagram" else entry.get("facebook_urls")
    account_urls = entry.get("instagram_accounts") if platform == "instagram" else entry.get("facebook_pages")
    # IG-only — no verified Facebook equivalent of a "Tagged" tab via either actor yet.
    tagged_account_urls = entry.get("instagram_tagged_accounts") if platform == "instagram" else None

    if direct_urls:
        fetch_fn = fetch_instagram_by_urls if platform == "instagram" else fetch_facebook_by_urls
        rows, raw_count, cost_entries = fetch_fn(list(direct_urls), approvals_dir=approvals_dir)
        search_terms_used = list(direct_urls)
        discovery_mode = "direct_url"
        # No ranking-down-to-N for direct URL mode — every resolved URL is kept (top_n=len(rows)
        # keeps everything; rank_and_select already no-ops cleanly on an empty list). Still
        # runs through rank_and_select so the manifest row shape (rank/metric_type/
        # metric_value/engagement_score) stays identical to search mode for downstream
        # consumers.
        selected = rank_and_select(rows, len(rows))
        # Honest, business-language gap notes — only computed in direct URL mode, where every
        # video is individually meaningful to the caller (search mode's much larger batches
        # would make a per-row note impractical to read, and a "best effort" search result
        # with gaps isn't surfaced the same way).
        for row in selected:
            row["data_gap_note"] = explain_data_gaps(row)
        excluded_too_old = 0
        excluded_unparseable_date = 0
    elif account_urls or tagged_account_urls:
        rows = []
        cost_entries = []
        search_terms_used = []
        raw_count = 0
        if account_urls:
            fetch_fn = fetch_instagram_by_accounts if platform == "instagram" else fetch_facebook_by_pages
            kwargs = {"content_type": "own"} if platform == "instagram" else {}
            own_rows, own_raw_count, own_cost_entries = fetch_fn(list(account_urls), candidate_pool, approvals_dir=approvals_dir, **kwargs)
            rows.extend(own_rows)
            cost_entries.extend(own_cost_entries)
            search_terms_used.extend(account_urls)
            raw_count += own_raw_count
        if tagged_account_urls:
            # Content OTHER accounts posted that tagged/mentioned this one — e.g. a brand's
            # "Tagged" tab, full of KOL/UGC content the brand's own account never posted.
            tagged_rows, tagged_raw_count, tagged_cost_entries = fetch_instagram_by_accounts(
                list(tagged_account_urls), candidate_pool, content_type="tagged", approvals_dir=approvals_dir,
            )
            rows.extend(tagged_rows)
            cost_entries.extend(tagged_cost_entries)
            search_terms_used.extend(tagged_account_urls)
            raw_count += tagged_raw_count
        discovery_mode = "account"
        # Unlike direct URL mode, account mode IS a discovery step (scan a feed, keep the
        # best N) — date filtering and per_platform_limit ranking both apply, same as search
        # mode, since "historical content isn't useful" applies just as much to an account's
        # old posts as to a stale hashtag hit.
        if cutoff is not None:
            rows, excluded_too_old, excluded_unparseable_date = filter_within_date_window(rows, cutoff)
        else:
            excluded_too_old = excluded_unparseable_date = 0
        selected = rank_and_select(rows, per_platform_limit)
    elif platform == "instagram":
        hashtags = _resolve_hashtags(entry)
        rows, raw_count, cost_entries = search_instagram(hashtags, candidate_pool, approvals_dir=approvals_dir)
        search_terms_used = hashtags
        discovery_mode = "search"
        if cutoff is not None:
            rows, excluded_too_old, excluded_unparseable_date = filter_within_date_window(rows, cutoff)
        else:
            excluded_too_old = excluded_unparseable_date = 0
        selected = rank_and_select(rows, per_platform_limit)
    elif platform == "facebook":
        queries = _resolve_queries(entry)
        date_after = cutoff.strftime("%Y-%m-%d") if cutoff else None
        rows, raw_count, cost_entries = search_facebook(
            queries, candidate_pool, location=entry.get("fb_location"),
            date_after=date_after, approvals_dir=approvals_dir,
        )
        search_terms_used = queries
        discovery_mode = "search"
        if cutoff is not None:
            rows, excluded_too_old, excluded_unparseable_date = filter_within_date_window(rows, cutoff)
        else:
            excluded_too_old = excluded_unparseable_date = 0
        selected = rank_and_select(rows, per_platform_limit)
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    return (
        {
            "brand": brand,
            "platform": platform,
            "discovery_mode": discovery_mode,
            "search_terms_used": search_terms_used,
            "candidates_scanned": raw_count,
            "video_candidates": len(rows),
            "excluded_too_old": excluded_too_old,
            "excluded_unparseable_date": excluded_unparseable_date,
            "results": selected,
        },
        cost_entries,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brands-json", required=True, help="Inline JSON array or path to a .json file")
    parser.add_argument("--platforms", default="instagram,facebook")
    parser.add_argument(
        "--per-platform-limit", type=str, default=str(DEFAULT_PER_PLATFORM_LIMIT),
        help=f"Videos to keep per brand per platform. Either a plain integer applied to every "
             f"platform in this run (default {DEFAULT_PER_PLATFORM_LIMIT}), or a JSON object "
             f'giving each platform its own count, e.g. \'{{"instagram": 30, "facebook": 10}}\'.',
    )
    parser.add_argument("--candidate-pool", type=int, default=DEFAULT_CANDIDATE_POOL)
    parser.add_argument(
        "--target-country", default=None,
        help="Recorded in the manifest for social-video-tag-analyzer's market-match check. "
             "Does NOT filter Apify results — see 'Country/market scoping' in this file's docstring.",
    )
    parser.add_argument(
        "--months-back", type=int, default=DEFAULT_MONTHS_BACK,
        help=f"Scope results to the last N months (default {DEFAULT_MONTHS_BACK}). Pass 0 to disable. "
             "Native on Facebook, best-effort post-hoc filtering on Instagram — see 'Recency scoping'.",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--approvals-dir",
        default=None,
        help="Defaults to <out-dir>/.apify_approvals — where mark_approved() writes cleared request hashes",
    )
    parser.add_argument("--skip-download", action="store_true", help="Rank and write the manifest without fetching video files")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    videos_dir = out_dir / "videos"
    approvals_dir = Path(args.approvals_dir) if args.approvals_dir else out_dir / ".apify_approvals"
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    cutoff = compute_cutoff_date(args.months_back) if args.months_back > 0 else None

    try:
        per_platform_limits = _parse_per_platform_limit(args.per_platform_limit, platforms)
    except ValueError as exc:
        print(f"ERROR: invalid --per-platform-limit: {exc}", file=sys.stderr)
        return 2

    try:
        brands = _load_brands(args.brands_json)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: invalid --brands-json: {exc}", file=sys.stderr)
        return 2

    brand_results = []
    cost_entries: list[dict] = []

    for entry in brands:
        brand = entry["brand"]
        for platform in platforms:
            try:
                result, platform_cost_entries = _process_brand_platform(
                    entry,
                    platform,
                    candidate_pool=args.candidate_pool,
                    per_platform_limit=per_platform_limits[platform],
                    approvals_dir=approvals_dir,
                    cutoff=cutoff,
                )
            except ApifyApprovalRequired as exc:
                print(str(exc))  # surfaces the APIFY_APPROVAL_REQUIRED_BEGIN/END sentinel verbatim
                return 3
            except ApifyNotConfigured as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 4

            cost_entries.extend(platform_cost_entries)

            if not args.skip_download:
                for row in result["results"]:
                    filename = f"{platform}_{_slug(brand)}_{row['rank']:02d}_{row['post_id'] or 'unknown'}.mp4"
                    dest = videos_dir / filename
                    status, error = download_video(row, dest)
                    row["download_status"] = status
                    row["download_error"] = error
                    row["local_file"] = f"videos/{filename}" if status == "ok" else None
            else:
                for row in result["results"]:
                    row["download_status"] = "skipped"
                    row["download_error"] = None
                    row["local_file"] = None

            brand_results.append(result)

    total_usd = sum(c["estimated_usd"] for c in cost_entries)
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target_country": args.target_country,
        "per_platform_limits": per_platform_limits,
        "months_back": args.months_back if cutoff else None,
        "date_cutoff": cutoff.isoformat() if cutoff else None,
        "brands": brand_results,
        "cost_summary": {
            "apify_calls": cost_entries,
            "total_estimated_usd": round(total_usd, 4),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OUTPUT_PATH={manifest_path}")
    print(f"total_estimated_usd={manifest['cost_summary']['total_estimated_usd']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

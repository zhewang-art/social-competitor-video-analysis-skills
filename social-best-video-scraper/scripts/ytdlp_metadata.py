"""Best-effort supplementary metadata via plain `yt-dlp -J <url>` — used only as a FALLBACK
to fill gaps an Apify actor's own output left behind in direct URL mode (see
instagram_fetcher.fetch_instagram_by_urls / facebook_fetcher.fetch_facebook_by_urls), never
as a primary data source and never for search mode (that would add one subprocess call per
candidate in a potentially large batch — direct URL mode is the only place the per-URL cost
is justified).

Verified live 2026-10-17/18 against this skill's own real URLs: yt-dlp's `view_count` is
sometimes populated for Facebook even when NEITHER Facebook Apify actor exposes a views field
at all (Facebook's web page itself shows a play count for some posts; the actor's own scraped
payload structure apparently doesn't carry it through). This is genuinely complementary to
the Apify actors, not redundant with them — the two data sources have different, non-identical
gaps, which is exactly why this module exists.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

YT_DLP_TIMEOUT_SECS = 60


def _as_nonneg_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        n = int(value)
    except (TypeError, ValueError):
        return None
    return None if n < 0 else n


def fetch_ytdlp_metadata(url: str) -> dict[str, Any]:
    """Runs `yt-dlp -J <url>` and extracts the small subset of fields this skill cares about.

    Returns `{}` on ANY failure (private/removed/geo-blocked/unsupported URL, timeout,
    malformed JSON) — this is explicitly a best-effort supplementary source, never a required
    one, so a failure here must never abort the caller's own (Apify-based) result; the caller
    decides what "still missing after this" means.

    Field names (`view_count`/`like_count`/`comment_count`/`repost_count`) are yt-dlp's own,
    shared across platforms — no separate per-platform parsing needed here."""
    try:
        result = subprocess.run(
            ["yt-dlp", "-J", "--no-warnings", url],
            capture_output=True,
            text=True,
            timeout=YT_DLP_TIMEOUT_SECS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}

    if result.returncode != 0 or not result.stdout:
        return {}

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    if not isinstance(info, dict):
        return {}

    return {
        "views": _as_nonneg_int(info.get("view_count")),
        "likes": _as_nonneg_int(info.get("like_count")),
        "comments": _as_nonneg_int(info.get("comment_count")),
        "shares": _as_nonneg_int(info.get("repost_count")),
        "uploader": info.get("uploader") or None,
    }

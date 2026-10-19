"""Download a ranked post's video via yt-dlp.

Tries the post's own page URL first (yt-dlp understands Instagram/Facebook post URLs
directly and handles auth/redirects better than a raw CDN link), falling back to the
actor-provided direct `video_url` when the page URL fails or is unsupported. A failure here
is recorded in the manifest row, never silently dropped — a private/removed/geo-blocked post
is an expected, reportable outcome, not a bug.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

YT_DLP_TIMEOUT_SECS = 120


def download_video(row: dict, dest_path: Path) -> tuple[str, str | None]:
    """Returns (status, error) where status is "ok" or "failed"."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [u for u in (row.get("url"), row.get("video_url")) if u]
    if not candidates:
        return "failed", "no url or video_url on this row"

    last_error: str | None = None
    for url in candidates:
        try:
            result = subprocess.run(
                ["yt-dlp", "--no-playlist", "-o", str(dest_path), url],
                capture_output=True,
                text=True,
                timeout=YT_DLP_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            last_error = f"yt-dlp timed out after {YT_DLP_TIMEOUT_SECS}s on {url}"
            continue
        if result.returncode == 0 and dest_path.exists():
            return "ok", None
        last_error = (result.stderr or result.stdout or "unknown yt-dlp error").strip()[-500:]

    return "failed", last_error

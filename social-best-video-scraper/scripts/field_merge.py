"""Generic "fill the gaps" merge: combine a primary row with a fallback row, keeping the
primary's values and only using the fallback to fill fields the primary left as None.

Used by direct-URL mode (see instagram_fetcher.fetch_instagram_by_urls /
facebook_fetcher.fetch_facebook_by_urls) to combine an Apify actor's row with
ytdlp_metadata.fetch_ytdlp_metadata's supplementary fields — never used in search mode.
"""

from __future__ import annotations

from typing import Any


def fill_missing_fields(primary: dict[str, Any], fallback: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Returns a NEW dict (never mutates `primary`): a copy of `primary` with every field in
    `fields` that is currently `None` replaced by `fallback`'s value for that field, IF
    `fallback` has a non-None value there. A field present and non-None in `primary` is never
    overwritten, even if `fallback` disagrees — the primary source (the Apify actor) is
    considered more authoritative when both have an answer; the fallback only speaks when the
    primary is silent."""
    merged = dict(primary)
    for field in fields:
        if merged.get(field) is None and fallback.get(field) is not None:
            merged[field] = fallback[field]
    return merged

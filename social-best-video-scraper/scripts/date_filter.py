"""Date-window filtering shared across platforms.

Instagram's actor has NO native date parameter at all (verified live 2026-10-16 against its
real input schema — only hashtags/keywordSearch/resultsType/resultsLimit exist), so "last N
months" for Instagram can only be an after-the-fact filter on whatever finite sample the
actor's hashtag/reels search happened to return — not a true exhaustive scan of all
within-window Instagram content for that hashtag. Facebook's actor DOES support a native
`startDate`/`endDate` (see facebook_fetcher.py), which is a real search-time boundary, not a
post-hoc filter — this module's filter is applied there too, defensively, in case the actor's
own date filtering is imperfect, but it should normally be a no-op for Facebook rows.

Both fetchers already produce a `posted_at` field in ISO-8601-ish string form — this module
only consumes that, it has no platform-specific knowledge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_MONTHS_BACK = 3


def compute_cutoff_date(months_back: int, *, now: datetime | None = None) -> datetime:
    """Months are approximated as 30 days each — avoids calendar-month edge cases (e.g. "3
    months before Jan 31") for what is inherently a recency filter, not an exact billing
    period; precision to the day doesn't matter here."""
    now = now or datetime.now(UTC)
    return now - timedelta(days=30 * max(1, months_back))


def _parse_posted_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def filter_within_date_window(
    rows: list[dict[str, Any]], cutoff: datetime
) -> tuple[list[dict[str, Any]], int, int]:
    """Returns (rows within the window, count excluded as too old, count excluded because
    `posted_at` couldn't be parsed at all).

    A row with no parseable date is EXCLUDED, not kept — the business requirement is
    explicitly "historical content is not useful," so a candidate whose recency we can't
    confirm doesn't meet that bar either. The two exclusion counts are kept separate (not
    merged into one "excluded" number) so "the actor gave us garbage data" and "this is
    genuinely old content" stay distinguishable in the manifest — collapsing them would hide
    which failure mode actually happened (Evidence Policy)."""
    kept: list[dict[str, Any]] = []
    excluded_too_old = 0
    excluded_unparseable = 0
    for row in rows:
        parsed = _parse_posted_at(row.get("posted_at", ""))
        if parsed is None:
            excluded_unparseable += 1
            continue
        if parsed < cutoff:
            excluded_too_old += 1
            continue
        kept.append(row)
    return kept, excluded_too_old, excluded_unparseable

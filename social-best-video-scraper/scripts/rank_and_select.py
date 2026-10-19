"""Rank normalized post rows and keep the top N.

Instagram rows always carry a real `views` count from the actor. Facebook rows carry `views`
only when Facebook exposed it on that post — when absent, this module computes an
engagement-weighted score and ranks by that instead. The weights below (likes + 2*comments +
3*shares) are a placeholder the business has not yet calibrated — surface them as such,
never as settled methodology (see INSTRUCTIONS.md "Decision Principles").
"""

from __future__ import annotations

from typing import Any

ENGAGEMENT_WEIGHTS = {"likes": 1, "comments": 2, "shares": 3}


def _engagement_score(row: dict[str, Any]) -> int:
    # `or 0`, not `.get(key, 0)` — a hidden-count field (e.g. Instagram's -1 sentinel,
    # normalized to None upstream) is present but None, not absent, so the dict-default
    # form alone would still multiply None by an int and crash.
    return (
        (row.get("likes") or 0) * ENGAGEMENT_WEIGHTS["likes"]
        + (row.get("comments") or 0) * ENGAGEMENT_WEIGHTS["comments"]
        + (row.get("shares") or 0) * ENGAGEMENT_WEIGHTS["shares"]
    )


def rank_and_select(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Return up to `top_n` rows, each annotated with `rank`, `metric_type`, `metric_value`,
    and `is_estimated`. Never pads past `len(rows)` — a short list stays short."""
    scored: list[tuple[int, bool, dict[str, Any]]] = []
    for row in rows:
        views = row.get("views")
        if views is not None:
            scored.append((views, False, row))
        else:
            scored.append((_engagement_score(row), True, row))

    scored.sort(key=lambda t: t[0], reverse=True)

    results = []
    for i, (metric_value, is_estimated, row) in enumerate(scored[:top_n], start=1):
        results.append(
            {
                **row,
                "rank": i,
                "metric_type": "engagement_score" if is_estimated else "views",
                "metric_value": metric_value,
                "is_estimated": is_estimated,
            }
        )
    return results

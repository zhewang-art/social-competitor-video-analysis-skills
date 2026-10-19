"""Thin Apify actor-call wrapper with the cost preflight guard applied.

Every actor call in this skill goes through `run_actor_guarded` — never call the raw
`apify_client.ApifyClient` directly from a fetcher, or the cost guard is bypassed.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from apify_client import ApifyClient
from apify_guard import ApifyEstimate, check_apify_preflight


class ApifyNotConfigured(RuntimeError):
    """Raised when APIFY_API_KEY is missing. Not an approval gate — the caller cannot
    proceed at all without a token, so this is a distinct, louder failure."""


def _client() -> ApifyClient:
    token = os.getenv("APIFY_API_KEY", "").strip()
    if not token:
        raise ApifyNotConfigured("APIFY_API_KEY is not set. Add it to .env before running.")
    return ApifyClient(token)


def run_actor_guarded(
    actor_id: str,
    run_input: dict[str, Any],
    *,
    approvals_dir: Path | None = None,
    timeout_secs: int = 300,
) -> tuple[list[dict[str, Any]], ApifyEstimate]:
    """Run an Apify actor after the cost preflight check, returning its dataset items and
    the estimate that was used (so the caller can fold it into cost_summary).

    `max_total_charge_usd` is a second, Apify-enforced belt-and-braces cap in case our own
    estimate undercounts — set generously above the estimate (not equal to it) since Apify's
    real per-run platform/compute overhead is not fully captured by our flat per-item price.
    """
    estimate = check_apify_preflight(actor_id, run_input, approvals_dir=approvals_dir)
    client = _client()
    run = client.actor(actor_id).call(
        run_input=run_input,
        run_timeout=timedelta(seconds=timeout_secs),
        max_total_charge_usd=round(estimate.estimated_usd * 3 + 1, 2),
    )
    dataset_id = run.default_dataset_id if run else None
    if not dataset_id:
        return [], estimate
    items = list(client.dataset(dataset_id).iterate_items())
    return items, estimate

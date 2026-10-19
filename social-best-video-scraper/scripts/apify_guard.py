"""Preflight cost guard for Apify actor calls.

Ported from `AnyMindGroup/anyagents`'s `skills_lib.apify_guard`, trimmed to the two actors
this skill uses (see `reference/apify-actors.md`) and simplified: USD only (the source
repo also converts to JPY for a Japan-based ops team; not needed here), and the approval-clear
mechanism uses a marker file under a caller-supplied local directory instead of
`$ANYAGENTS_SESSION_DIR` (this repo has no equivalent session directory convention).

The guard estimates spend *before* an actor run and lets small/known-cost calls through
automatically. An expensive or unknown-actor call raises `ApifyApprovalRequired`, carrying an
`APIFY_APPROVAL_REQUIRED_BEGIN ... END` sentinel block the caller MUST surface verbatim and
stop — never silently retry with a different provider, never silently proceed.

This is a single-call gate, not an accumulating ledger — it does not sum cost across calls in a
run (same reasoning as the source: an accumulating gate that nothing ever resets would
permanently block a thread after the first approval).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENTINEL_BEGIN = "APIFY_APPROVAL_REQUIRED_BEGIN"
SENTINEL_END = "APIFY_APPROVAL_REQUIRED_END"

DEFAULT_AUTO_APPROVE_USD = 10.0
DEFAULT_MIN_RUN_USD = 0.02
DEFAULT_UNKNOWN_ACTOR_UNIT_USD = 0.01

# Per-item USD price. Kept as separate entries (not a shared constant) so a future price
# change to one actor doesn't silently change another. See reference/apify-actors.md for
# sourcing. The "-scraper"/"-posts-scraper" pair (added 2026-10-17) are the "by URL" actors
# used when the caller already has specific video URLs, rather than a hashtag/query to
# search — see instagram_fetcher.fetch_instagram_by_urls / facebook_fetcher.fetch_facebook_by_urls.
ACTOR_UNIT_USD: dict[str, float] = {
    "apify/instagram-hashtag-scraper": 0.0035,
    "scraper_one/facebook-posts-search": 0.0035,
    "apify/instagram-scraper": 0.0023,
    "apify/facebook-posts-scraper": 0.005,
}


@dataclass(frozen=True)
class ApifyEstimate:
    actor_id: str
    quantity: int
    unit_usd: float
    estimated_usd: float
    threshold_usd: float
    request_hash: str
    unknown_actor: bool = False
    reason: str = ""


class ApifyApprovalRequired(RuntimeError):
    """Raised when an Apify call needs explicit approval before running."""

    def __init__(self, estimate: ApifyEstimate, payload: Mapping[str, Any]):
        self.estimate = estimate
        self.payload = dict(payload)
        message = (
            "Apify execution requires approval before running.\n"
            f"{SENTINEL_BEGIN}\n"
            f"{json.dumps(self.payload, ensure_ascii=False, sort_keys=True)}\n"
            f"{SENTINEL_END}"
        )
        super().__init__(message)


def _infer_quantity(run_input: Mapping[str, Any]) -> int:
    """Best-effort item count for cost estimation: resultsLimit or resultsCount, times the
    number of hashtags/direct URLs when the payload searches/fetches several at once.

    `startUrls` (the by-URL Facebook actor) is a special case: its own `resultsLimit` is
    documented as "ignored when scraping direct post URLs" (verified live 2026-10-17 against
    the actor's real input schema), so quantity there is just the URL count, never multiplied
    by `per_call`."""
    start_urls = run_input.get("startUrls")
    if isinstance(start_urls, list) and start_urls:
        return max(1, len(start_urls))

    per_call = run_input.get("resultsLimit") or run_input.get("resultsCount") or 1
    try:
        per_call = int(per_call)
    except (TypeError, ValueError):
        per_call = 1
    hashtags = run_input.get("hashtags")
    direct_urls = run_input.get("directUrls")
    if isinstance(hashtags, list) and hashtags:
        multiplier = len(hashtags)
    elif isinstance(direct_urls, list) and direct_urls:
        multiplier = len(direct_urls)
    else:
        multiplier = 1
    return max(1, per_call * multiplier)


def _request_hash(actor_id: str, run_input: Mapping[str, Any]) -> str:
    blob = json.dumps({"actor": actor_id, "input": run_input}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def estimate_apify_cost(actor_id: str, run_input: Mapping[str, Any] | None = None) -> ApifyEstimate:
    payload = dict(run_input or {})
    normalized_actor = str(actor_id or "").strip()
    threshold = float(os.getenv("APIFY_AUTO_APPROVE_USD", DEFAULT_AUTO_APPROVE_USD))
    quantity = _infer_quantity(payload)
    unit = ACTOR_UNIT_USD.get(normalized_actor)
    unknown_actor = unit is None
    if unit is None:
        unit = float(os.getenv("APIFY_UNKNOWN_ACTOR_UNIT_USD", DEFAULT_UNKNOWN_ACTOR_UNIT_USD))
    min_run_usd = float(os.getenv("APIFY_MIN_RUN_USD", DEFAULT_MIN_RUN_USD))
    estimated_usd = max(min_run_usd, quantity * unit)
    return ApifyEstimate(
        actor_id=normalized_actor,
        quantity=quantity,
        unit_usd=unit,
        estimated_usd=estimated_usd,
        threshold_usd=threshold,
        request_hash=_request_hash(normalized_actor, payload),
        unknown_actor=unknown_actor,
    )


def _approval_reason(estimate: ApifyEstimate) -> str:
    if estimate.unknown_actor:
        return "unknown_actor"
    if estimate.estimated_usd > estimate.threshold_usd:
        return "estimate_over_threshold"
    return ""


def _is_approved_by_marker(request_hash: str, approvals_dir: Path | None) -> bool:
    if approvals_dir is None:
        return False
    return (approvals_dir / request_hash).exists()


def mark_approved(request_hash: str, approvals_dir: Path) -> None:
    """Called by the orchestrating agent AFTER a human has unambiguously approved one
    specific already-displayed request (actor + cost estimate + request hash) in the same
    Slack thread — never by this module itself (that would make the gate self-approving)."""
    approvals_dir.mkdir(parents=True, exist_ok=True)
    (approvals_dir / request_hash).touch()


def check_apify_preflight(
    actor_id: str,
    run_input: Mapping[str, Any] | None = None,
    *,
    approvals_dir: Path | None = None,
) -> ApifyEstimate:
    """Estimate and enforce the approval policy for one Apify call.

    Raises `ApifyApprovalRequired` when the estimate is over threshold (or the actor is
    unpriced) and this exact request hash has not already been marked approved.
    """
    payload = dict(run_input or {})
    estimate = estimate_apify_cost(actor_id, payload)

    if os.getenv("APIFY_PREFLIGHT_DISABLE") == "1":
        return estimate
    if _is_approved_by_marker(estimate.request_hash, approvals_dir):
        return estimate

    reason = _approval_reason(estimate)
    if reason:
        estimate_with_reason = ApifyEstimate(
            **{**estimate.__dict__, "reason": reason},
        )
        payload_out = {
            "type": "apify_approval_required",
            "actor_id": estimate.actor_id,
            "estimated_usd": round(estimate.estimated_usd, 4),
            "threshold_usd": round(estimate.threshold_usd, 2),
            "quantity": estimate.quantity,
            "request_hash": estimate.request_hash,
            "reason": reason,
            "input_preview": {k: v for k, v in payload.items() if k != "proxy"},
        }
        raise ApifyApprovalRequired(estimate_with_reason, payload_out)

    return estimate

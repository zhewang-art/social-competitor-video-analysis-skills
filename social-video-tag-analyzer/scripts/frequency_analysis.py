"""Frequency-count correlation-lite analysis: within the best-performing videos (selected
independently by Views and by Engagement), count how often each tag value / numeric bucket
appears, sorted descending — the user's explicit, simplified redesign (2026-10-16) that
replaced an earlier "group average vs. baseline" proposal they rejected as too complicated.

Selection: per brand, the top `TOP_N_PER_BRAND` videos by Views and, independently, the top
`TOP_N_PER_BRAND` by Engagement (the same `(likes+comments+shares)/views` formula as
row_builder.py) — a video missing the relevant metric (e.g. Facebook videos with no Views) is
excluded from that ranking, not treated as zero. The two per-brand top-N sets are pooled
across all brands into two global video sets, matching the user's own framing ("1个品牌的话
就是10条，如果是5个竞品的话就是50条").

Per factor category (one per tag_definitions.json category, plus the structured sub-fields
of story_structure/content_flags/quantitative_observations), counts every observed value's
frequency within each of the two pooled sets and keeps the top `MAX_VALUES_PER_FACTOR` by
count — capped only to keep the output table readable, not to force a fixed-size result; a
factor with fewer distinct values just contributes fewer rows. A value appearing in only one
video is still reported (small-sample caveats are left to reason_generator.py's Gemini call,
not suppressed here) — never fabricated, never padded.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from build_prompt import load_tag_definitions

TOP_N_PER_BRAND = 10
MAX_VALUES_PER_FACTOR = 3

METRIC_VIEWS = "播放量"
METRIC_ENGAGEMENT = "Engagement"


def _views(video_result: dict) -> float | None:
    v = video_result.get("views")
    return float(v) if v is not None else None


def _engagement(video_result: dict) -> float | None:
    views = video_result.get("views")
    if not views:
        return None
    likes, comments, shares = video_result.get("likes"), video_result.get("comments"), video_result.get("shares")
    if likes is None and comments is None and shares is None:
        # No interaction field was ever populated for this platform/fetch method — this is
        # "unknown," not "zero interactions." Caught live 2026-10-17 analyzing a batch of
        # Facebook videos fetched via plain yt-dlp metadata (views present, likes/comments/
        # shares all None) — without this check every one computed as a fabricated 0.0 and
        # silently "won" the Engagement ranking against videos with real interaction data.
        return None
    return ((likes or 0) + (comments or 0) + (shares or 0)) / views


def _select_top_per_brand(items: list[dict], metric_fn, top_n: int) -> list[dict]:
    by_brand: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for item in items:
        if item.get("analysis") is None:
            continue
        value = metric_fn(item["video_result"])
        if value is None:
            continue
        by_brand[item["manifest_entry"]["brand"]].append((value, item))

    selected: list[dict] = []
    for scored in by_brand.values():
        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected.extend(item for _, item in scored[:top_n])
    return selected


# Bucket boundaries for quantitative_observations' numeric fields — a judgment call (no
# empirical distribution to calibrate against yet), documented here so it's one place to
# retune once real data volume exists. Never silently changed without updating this comment.
_NUMERIC_BUCKETERS = {
    "talent_count": lambda v: "0人" if v <= 0 else ("1人" if v == 1 else ("2人" if v == 2 else "3人以上")),
    "product_first_appearance_sec": lambda v: (
        "0-2秒" if v <= 2 else ("3-5秒" if v <= 5 else ("6-10秒" if v <= 10 else "10秒以上"))
    ),
    "product_screen_time_pct": lambda v: (
        "<20%" if v < 20 else ("20-40%" if v < 40 else ("40-60%" if v < 60 else ("60-80%" if v < 80 else "80%以上")))
    ),
    "product_close_up_count": lambda v: "0次" if v <= 0 else ("1次" if v == 1 else ("2次" if v == 2 else "3次以上")),
    "product_count": lambda v: "1个及以下" if v <= 1 else ("2个" if v == 2 else "3个以上"),
    "price_switch_count": lambda v: "0次(未提及价格)" if v <= 0 else ("1次" if v == 1 else "2次以上"),
}


def _bucket_numeric(field_key: str, value: float) -> str:
    bucketer = _NUMERIC_BUCKETERS.get(field_key)
    return bucketer(value) if bucketer else str(value)


def _extract_factors(analysis: dict, definitions: dict) -> list[tuple[str, str]]:
    """One (factor_label, value) pair per observed tag value in this video's analysis —
    a multi-select field with 2 hits contributes 2 pairs under the same factor_label.
    Generic over tag_definitions.json's selection_type, so adding a new category there
    does not require touching this function unless it introduces a genuinely new
    selection_type shape."""
    pairs: list[tuple[str, str]] = []
    for category in definitions["categories"]:
        key = category["key"]
        label = category["label_cn"]
        selection_type = category["selection_type"]

        if selection_type in ("multi_open", "multi_closed", "multi_closed_extensible"):
            for v in analysis.get(key) or []:
                pairs.append((label, v))

        elif selection_type == "single_closed":
            v = analysis.get(key)
            if v:
                pairs.append((label, v))

        elif selection_type == "multi_closed_with_meta":  # hook_type
            hook = analysis.get(key) or {}
            if hook.get("meta_category"):
                pairs.append(("Hook元类别", hook["meta_category"]))
            for v in hook.get("tags") or []:
                pairs.append(("Hook标签", v))

        elif selection_type == "boolean":  # three_act_structure
            block = analysis.get(key) or {}
            if "present" in block:
                pairs.append((label, "是" if block["present"] else "否"))

        elif selection_type == "boolean_with_subtypes":  # information_gap
            block = analysis.get(key) or {}
            if "present" in block:
                pairs.append((label, "是" if block["present"] else "否"))
                for v in block.get("sub_types") or []:
                    pairs.append((f"{label}子类型", v))

        elif selection_type == "structured_checklist":  # story_structure
            block = analysis.get(key) or {}
            for stage in category.get("stages", []):
                stage_block = block.get(stage["key"]) or {}
                pairs.append((f"{label}:{stage['label_cn']}", "是" if stage_block.get("present") else "否"))

        elif selection_type == "boolean_group":  # content_flags
            block = analysis.get(key) or {}
            for field in category.get("fields", []):
                value = block.get(field["key"])
                if value is not None:
                    pairs.append((field["label_cn"], "是" if value else "否"))

        elif selection_type == "numeric_group":  # quantitative_observations
            block = analysis.get(key) or {}
            for field in category.get("fields", []):
                value = block.get(field["key"])
                if value is not None:
                    pairs.append((field["label_cn"], _bucket_numeric(field["key"], value)))

    return pairs


def _count_factors(items: list[dict], definitions: dict) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        for factor_label, value in _extract_factors(item["analysis"], definitions):
            counts[factor_label][value] += 1
    return counts


def _findings_for_metric(metric_name: str, items: list[dict], definitions: dict) -> list[dict]:
    counts = _count_factors(items, definitions)
    findings: list[dict] = []
    for factor_label, counter in counts.items():
        for value, sample_size in counter.most_common(MAX_VALUES_PER_FACTOR):
            findings.append({
                "metric": metric_name,
                "factor": factor_label,
                "value": value,
                "sample_size": sample_size,
                "pool_size": len(items),
            })
    return findings


def build_findings(results: list[dict[str, Any]]) -> list[dict]:
    """`results` is run.py's list of {"manifest_entry", "video_result", "analysis", "error"}
    dicts. Returns a flat list of finding dicts (metric/factor/value/sample_size/pool_size,
    "reason" not yet filled — see reason_generator.py), Views findings before Engagement
    findings, each group sorted by sample_size descending within it. Empty when nothing in
    `results` has both a usable metric value and a successful analysis — never padded."""
    definitions = load_tag_definitions()

    views_pool = _select_top_per_brand(results, _views, TOP_N_PER_BRAND)
    engagement_pool = _select_top_per_brand(results, _engagement, TOP_N_PER_BRAND)

    findings = _findings_for_metric(METRIC_VIEWS, views_pool, definitions)
    findings += _findings_for_metric(METRIC_ENGAGEMENT, engagement_pool, definitions)

    findings.sort(key=lambda f: (f["metric"] != METRIC_VIEWS, -f["sample_size"]))
    return findings

"""Distinguish a true DATA GAP ("this field should normally be obtainable, but we couldn't get
it for this specific video") from a STRUCTURAL ABSENCE ("this platform never exposes this field
at all, for any video, no matter what tool is used").

This distinction matters because the two call for completely different treatment (explicit
user correction, 2026-10-18): a structural absence is not a "gap" to flag per-video at all —
it is one blanket, stated-once fact about the platform (e.g. "Instagram doesn't expose share
counts"), not a per-row anomaly. Flagging it per-row (as an earlier version of this module did)
implied something had gone wrong on that specific video, when nothing had — EVERY video on
that platform is in the same boat, by design, forever. A true gap, by contrast, is specific to
one video (most others on the same platform DID get the field) and is worth a caller's
attention, since it might mean a retry, a different source, or at minimum something to know
about that one piece of content.

`explain_data_gaps()` therefore only ever reports fields in the SECOND category — never a
known structural absence, which `is_structural_limit()` lets a caller check/footnote once,
globally, instead. Written in plain language about the PLATFORM/CONTENT, never in tool/actor
jargon ("the actor doesn't support X") — the reader does not know or care what Apify or
yt-dlp are; they want to know why a specific number is missing for this video.

Called AFTER field_merge.fill_missing_fields has already tried every available data source for
a row (Apify actor + yt-dlp metadata in direct URL mode) — this module explains gaps that
survive that merge, it never decides whether to attempt another source itself.
"""

from __future__ import annotations

from typing import Any

# Confirmed-always-true structural absences — verified live across multiple independent
# tools (not a single actor's quirk), safe to state with confidence as "this platform never
# exposes this, full stop," not "this video happened to be missing it." See
# reference/apify-actors.md for the verification history behind each of these. A field here
# is skipped entirely by explain_data_gaps() — NEVER reported as a per-row gap.
STRUCTURAL_ABSENCES: dict[tuple[str, str], str] = {
    ("instagram", "shares"): "Instagram 不公开转发数（shares），所有 Instagram 视频此项均为空 — 这是平台本身的限制，不是抓取遗漏。",
}

_FIELD_LABELS = {"views": "播放量", "likes": "点赞数", "comments": "评论数", "shares": "转发数"}


def is_structural_limit(platform: str, field: str) -> bool:
    """True when (platform, field) is a confirmed, always-true structural absence — the
    caller should footnote this once globally (see STRUCTURAL_ABSENCES's text), never
    per-row."""
    return (platform, field) in STRUCTURAL_ABSENCES


def explain_data_gaps(row: dict[str, Any]) -> str:
    """`row` is a normalized video row (post-merge) with platform/views/likes/comments/shares.
    Returns a single semicolon-joined string, one clause per still-missing field that is NOT a
    known structural absence (see module docstring), or "" when nothing is missing or every
    missing field is structural. Never mutates `row`."""
    platform = row.get("platform", "")
    clauses: list[str] = []
    for field in ("views", "likes", "comments", "shares"):
        if row.get(field) is not None:
            continue
        if is_structural_limit(platform, field):
            continue  # not a gap — a stated-once platform fact, see STRUCTURAL_ABSENCES
        label = _FIELD_LABELS[field]
        platform_name = "Instagram" if platform == "instagram" else "Facebook" if platform == "facebook" else platform
        clauses.append(f"{label}：这条{platform_name}内容没有公开这项数据，抓取不到（不是工具漏抓，是内容本身没有暴露）。")
    return "；".join(clauses)

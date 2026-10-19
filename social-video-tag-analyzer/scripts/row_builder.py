"""Format-agnostic row building: turn (manifest entry, video result, Gemini analysis) into
flat rows for Tab 1, tag_definitions.json into flat rows for Tab 2, and
frequency_analysis.py's findings into flat rows for Tab 3.

Shared by both output backends (gsheet_output.py, xlsx_output.py) so the two tabs' content
never drifts apart between the Google Sheet path and the Excel path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "reference"
_TAG_DEFINITIONS_PATH = _REFERENCE_DIR / "tag_definitions.json"

RESULTS_TAB = "Analysis Results"
GLOSSARY_TAB = "标签释义 Tag Glossary"
FINDINGS_TAB = "相似性分析 Findings"

FINDINGS_HEADER = ["指标", "影响因素", "具体标签or数值", "原因分析", "样本量"]

RESULTS_HEADER = [
    "Brand", "Platform", "Rank", "Metric Type", "Metric Value",
    "Views", "Likes", "Comments", "Shares", "Engagement", "URL", "Local File",
    "覆盖人群", "身份", "内容归属", "POV", "Hook 元类别", "Hook 标签", "内容形式", "文案类型",
    "Story: Hook", "Story: Problem", "Story: Solution", "Story: CTA",
    "三幕剧结构", "三幕剧依据", "RTB 证据类型", "信息缺口", "信息缺口子类型",
    # Stage 4b additions (2026-10-16) — content_flags (booleans)
    "开场3秒产品", "开场3秒人脸", "开场文字悬念", "提及价格", "价格对比",
    "提及竞品/其他渠道", "持续使用声称", "CTA外互动引导",
    # Stage 4b additions — quantitative_observations (numerics)
    "出镜人数", "产品首现秒数", "产品画面占比%", "产品特写次数", "产品数量", "价格切换次数",
    # Stage 4b additions — simple categorical tags
    "出镜人物性别", "出镜人物年龄观感", "出镜人物专业度信号", "CTA类型", "JTBD任务维度", "CEP场景切入点",
    "故事线摘要", "卖点", "覆盖人群判断依据", "国家匹配判断", "国家匹配依据",
    "分析状态", "分析失败原因",
]

# Number of blank placeholder cells for a failed/unanalyzed row — must track every column
# between "Local File" and "分析状态" (exclusive) in RESULTS_HEADER above. Views/Likes/
# Comments/Shares/Engagement are NOT in this blank zone — they come from the scraper's
# manifest (video_result), not from Gemini's analysis, so they're populated even when
# Gemini's analysis failed (see the early-return branch below).
_BLANK_TAG_CELLS = len(RESULTS_HEADER) - 14

GLOSSARY_HEADER = ["类别 Category", "标签值 Value", "说明 Definition", "示例 Example"]


def _join(values: list[str] | None) -> str:
    return "; ".join(values) if values else ""


def _metric_cell(value: int | None) -> int | str:
    """A raw performance number as a genuine numeric cell — not `str(value)` — so Views/
    Likes/Comments/Shares/Engagement can be sorted, filtered, and used directly in
    spreadsheet formulas (e.g. for a correlation pass) without a text-to-number conversion
    step first. Blank (`""`), never "0" or "N/A", when the platform doesn't expose the
    field (e.g. Instagram never exposes shares; Facebook rarely exposes views — see
    social-best-video-scraper's INSTRUCTIONS.md) — a 0 would misreport "we observed zero"
    when the truth is "this platform never told us."""
    return value if value is not None else ""


def _engagement_rate_cell(views: int | None, likes: int | None, comments: int | None, shares: int | None) -> float | str:
    """Engagement = (likes + comments + shares) / views — the business's own definition,
    not the weighted `engagement_score` estimate `social-best-video-scraper` computes
    internally as a Facebook views-fallback for ranking (a different, unrelated number).
    Blank when `views` is missing or zero (can't divide, and a view-less video has no
    denominator to make this ratio meaningful) — never a fabricated 0 or a ZeroDivisionError.
    Missing likes/comments/shares count as 0 in the sum (consistent with
    `rank_and_select.py`'s existing engagement-score fallback) ONLY when at least one of the
    three is actually known — if all three are None (no interaction field was ever populated
    for this platform/fetch method at all), that's "unknown," not "zero interactions," and
    must stay blank too. Caught live 2026-10-17 analyzing Facebook videos fetched via plain
    yt-dlp metadata (views present, likes/comments/shares all None) — without this check every
    one would have shown Engagement=0, a fabricated number, not a real one."""
    if not views:
        return ""
    if likes is None and comments is None and shares is None:
        return ""
    total = (likes or 0) + (comments or 0) + (shares or 0)
    return round(total / views, 4)


def _stage_cell(stage: dict | None) -> str:
    if not stage or not stage.get("present"):
        return ""
    return stage.get("description", "") or "✓"


def _bool_cell(flags: dict, key: str) -> str:
    """是/否, matching the existing three_act_structure/information_gap convention — not a
    raw Python bool, so the sheet reads naturally in Chinese. Blank (not "否") when the whole
    content_flags block is absent (Gemini analysis failed), handled by the caller."""
    value = flags.get(key)
    if value is None:
        return ""
    return "是" if value else "否"


def _numeric_cell(observations: dict, key: str) -> int | float | str:
    """Native numeric cell, blank (not 0) when Gemini couldn't estimate — same rationale as
    _metric_cell above: a 0 would misreport "observed zero" when the truth is "not estimated."""
    value = observations.get(key)
    return value if value is not None else ""


def build_result_row(manifest_entry: dict, video_result: dict, analysis: dict | None, error: str | None) -> list[str]:
    """One row per analyzed video. `manifest_entry` is a per-video row from
    social-best-video-scraper's manifest.json (brand/platform/rank/url/local_file/metric_*);
    `analysis` is the Gemini JSON (or None on failure) — kept as two separate args rather
    than merging upstream so a failed analysis still produces a traceable row."""
    status = "ok" if analysis else "failed"
    if not analysis:
        return [
            manifest_entry.get("brand", ""),
            video_result.get("platform", manifest_entry.get("platform", "")),
            str(video_result.get("rank", "")),
            video_result.get("metric_type", ""),
            str(video_result.get("metric_value", "")),
            _metric_cell(video_result.get("views")),
            _metric_cell(video_result.get("likes")),
            _metric_cell(video_result.get("comments")),
            _metric_cell(video_result.get("shares")),
            _engagement_rate_cell(
                video_result.get("views"), video_result.get("likes"),
                video_result.get("comments"), video_result.get("shares"),
            ),
            video_result.get("url", ""),
            video_result.get("local_file", "") or "",
            *([""] * _BLANK_TAG_CELLS),
            status,
            error or "",
        ]

    story = analysis.get("story_structure", {})
    hook = analysis.get("hook_type", {})
    three_act = analysis.get("three_act_structure", {})
    info_gap = analysis.get("information_gap", {})
    # Stage 4b additions — absent (not a dict) only if Gemini's response omitted the whole
    # block entirely; individual missing sub-fields within it render blank via _bool_cell/
    # _numeric_cell, never fabricated as false/0.
    content_flags = analysis.get("content_flags") or {}
    quant_obs = analysis.get("quantitative_observations") or {}
    # Absent (not a dict) whenever no --target-country was given for this run — distinct
    # from Gemini assessing "uncertain", which means a country WAS specified but the
    # evidence wasn't conclusive. Both render as blank-ish but the dict shape differs.
    country_match = analysis.get("country_match") or {}

    return [
        manifest_entry.get("brand", ""),
        video_result.get("platform", manifest_entry.get("platform", "")),
        str(video_result.get("rank", "")),
        video_result.get("metric_type", ""),
        str(video_result.get("metric_value", "")),
        _metric_cell(video_result.get("views")),
        _metric_cell(video_result.get("likes")),
        _metric_cell(video_result.get("comments")),
        _metric_cell(video_result.get("shares")),
        _engagement_rate_cell(
            video_result.get("views"), video_result.get("likes"),
            video_result.get("comments"), video_result.get("shares"),
        ),
        video_result.get("url", ""),
        video_result.get("local_file", "") or "",
        _join(analysis.get("cover_audience")),
        _join(analysis.get("identity")),
        analysis.get("content_ownership", ""),
        analysis.get("pov", ""),
        hook.get("meta_category", ""),
        _join(hook.get("tags")),
        _join(analysis.get("content_format")),
        _join(analysis.get("copy_type")),
        _stage_cell(story.get("hook")),
        _stage_cell(story.get("problem")),
        _stage_cell(story.get("solution")),
        _stage_cell(story.get("cta")),
        "是" if three_act.get("present") else "否",
        three_act.get("evidence", ""),
        _join(analysis.get("rtb_type")),
        "是" if info_gap.get("present") else "否",
        _join(info_gap.get("sub_types")),
        _bool_cell(content_flags, "product_shown_in_first_3s"),
        _bool_cell(content_flags, "face_shown_in_first_3s"),
        _bool_cell(content_flags, "text_overlay_hook_present"),
        _bool_cell(content_flags, "price_mentioned"),
        _bool_cell(content_flags, "price_comparison_present"),
        _bool_cell(content_flags, "competitor_or_other_channel_mention"),
        _bool_cell(content_flags, "continuous_use_claim"),
        _bool_cell(content_flags, "engagement_hook_beyond_cta"),
        _numeric_cell(quant_obs, "talent_count"),
        _numeric_cell(quant_obs, "product_first_appearance_sec"),
        _numeric_cell(quant_obs, "product_screen_time_pct"),
        _numeric_cell(quant_obs, "product_close_up_count"),
        _numeric_cell(quant_obs, "product_count"),
        _numeric_cell(quant_obs, "price_switch_count"),
        _join(analysis.get("talent_gender")),
        _join(analysis.get("talent_age_appearance")),
        _join(analysis.get("talent_expertise_signal")),
        _join(analysis.get("cta_type")),
        _join(analysis.get("job_dimension")),
        _join(analysis.get("cep_occasion_type")),
        analysis.get("story_plot_summary", ""),
        _join(analysis.get("selling_points")),
        analysis.get("cover_audience_confidence_note", ""),
        country_match.get("assessment", ""),
        country_match.get("evidence", ""),
        status,
        error or "",
    ]


def build_glossary_rows() -> list[list[str]]:
    """Flatten tag_definitions.json into glossary rows: Category | Value | Definition |
    Example. Always derived from the same source of truth the prompt is built from — never
    hand-maintained separately, or a tab and the prompt will drift apart.

    "国家匹配判断" and "Engagement" are the two exceptions — "国家匹配判断" is conditional on
    whether the run specified --target-country (see build_prompt.py), and "Engagement" is a
    computed formula column, not a Gemini-tagged category — neither lives in
    tag_definitions.json's always-present core taxonomy. Both documented here by hand instead."""
    definitions = json.loads(_TAG_DEFINITIONS_PATH.read_text(encoding="utf-8"))
    rows: list[list[str]] = [
        GLOSSARY_HEADER,
        [
            "Engagement",
            "",
            "计算公式 = (Likes + Comments + Shares) / Views，不是加权估算分，是业务自己定义的"
            "互动率。Views缺失或为0时留空（无法计算，不是0）——这个指标专门设计用于跨账号/"
            "跨品牌比较，原始views数字会被账号粉丝体量严重影响，但互动率相对公平。",
            "",
        ],
    ]
    for category in definitions["categories"]:
        label = category["label_cn"]
        rows.append([label, "", category["description"], ""])
        for v in category.get("values", []):
            rows.append([label, v["value"], v.get("definition", ""), v.get("example", "")])
        for s in category.get("stages", []):
            rows.append([label, s["label_cn"], s.get("definition", ""), ""])
        for ev in category.get("example_values", []):
            rows.append([label, f"(示例) {ev}", "", ""])
        for f in category.get("fields", []):
            unit = f" 单位:{f['unit']}" if f.get("unit") else ""
            rows.append([label, f"{f['label_cn']}{unit}", f.get("definition", ""), f.get("example", "")])

    rows.append([
        "国家匹配判断",
        "",
        "仅当分析时指定了目标国家（--target-country）才会填写——IG/FB的搜索本身不支持按国家过滤，"
        "这一列是Gemini基于视频的语言/画面/文化线索,独立判断这条视频是否匹配目标市场。空白=本次"
        "分析未指定目标国家，没有做这项判断（不代表不匹配）。",
        "",
    ])
    rows.append(["国家匹配判断", "likely_match", "视频证据支持匹配目标国家", ""])
    rows.append(["国家匹配判断", "uncertain", "证据不足以判断，诚实标注为不确定，不是猜测结果", ""])
    rows.append(["国家匹配判断", "likely_mismatch", "视频证据指向这条内容更可能面向其他市场", ""])
    return rows


def build_notes_rows(results: list[dict[str, Any]]) -> list[list[Any]]:
    """Footnote block appended BELOW the data rows on the same "Analysis Results" tab — not
    a separate column, not a separate tab (explicit user correction 2026-10-18): a structural
    platform fact (e.g. "Instagram doesn't expose shares") applies to EVERY row on that
    platform, so it belongs stated once at the bottom, not repeated as a per-row annotation
    that would wrongly read as "something went wrong with this specific video." A true,
    video-specific data gap (see data_gaps.explain_data_gaps upstream in
    social-best-video-scraper — only produced for fields that are normally obtainable but
    happened to be missing for one video) IS listed here too, one line per affected video,
    for the same "don't clutter the data rows with a column" reason.

    Returns `[]` + 2 blank spacer rows + the Engagement-formula explanations + (only if any
    exist) one line per video with a non-empty `data_gap_note`. `results` is run.py's list of
    {"manifest_entry", "video_result", "analysis", "error"} dicts — same shape
    build_result_row consumes."""
    rows: list[list[Any]] = [[], []]
    rows.append(["IG Engagement 计算方法：(Likes + Comments) / Views — Instagram 不公开转发数（Shares），计算时不包含这一项。"])
    rows.append(["FB Engagement 计算方法：(Likes + Comments + Shares) / Views"])

    gap_lines = [
        (r["video_result"].get("url", ""), r["video_result"]["data_gap_note"])
        for r in results
        if r["video_result"].get("data_gap_note")
    ]
    if gap_lines:
        rows.append([])
        rows.append(["以下内容存在未能抓取到的数据（本应可获取但这条视频抓取失败，不是平台结构性限制）："])
        for url, note in gap_lines:
            rows.append([f"{url} — {note}"])

    return rows


def build_findings_rows(findings: list[dict]) -> list[list[Any]]:
    """Tab 3: frequency_analysis.py + reason_generator.py's output, formatted into the exact
    5-column table the business asked for (指标|影响因素|具体标签or数值|原因分析|样本量),
    already sorted/grouped by metric (Views findings before Engagement findings) upstream in
    `frequency_analysis.build_findings()` — this function only formats, never re-sorts."""
    rows: list[list[Any]] = [FINDINGS_HEADER]
    for f in findings:
        rows.append([f["metric"], f["factor"], f["value"], f.get("reason", ""), f["sample_size"]])
    return rows

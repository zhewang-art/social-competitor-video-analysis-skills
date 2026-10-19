"""Build the Gemini analysis prompt from reference/tag_definitions.json.

Single source of truth: tag_definitions.json drives both this prompt (what Gemini is
asked to tag) and sheet_output.py's glossary tab (what a human sees the tags mean).
Edit the taxonomy there, never hardcode tag values/definitions here or in sheet_output.py.
"""

from __future__ import annotations

import json
from pathlib import Path

_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "reference"
_TAG_DEFINITIONS_PATH = _REFERENCE_DIR / "tag_definitions.json"


def load_tag_definitions() -> dict:
    return json.loads(_TAG_DEFINITIONS_PATH.read_text(encoding="utf-8"))


def _render_category_for_prompt(category: dict) -> str:
    lines = [f"### {category['label_cn']} (`{category['key']}`)", category["description"]]
    values = category.get("values")
    if values:
        lines.append("可选标签值：")
        for v in values:
            meta = f"（上层归类: {v['meta']}）" if v.get("meta") else ""
            lines.append(f"- {v['value']}{meta}：{v['definition']}")
    stages = category.get("stages")
    if stages:
        lines.append("需要逐段检查：")
        for s in stages:
            lines.append(f"- {s['key']} ({s['label_cn']})：{s['definition']}")
    example_values = category.get("example_values")
    if example_values:
        lines.append(f"示例值（非封闭列表，仅供参考）：{', '.join(example_values)}")
    fields = category.get("fields")
    if fields and category["selection_type"] == "boolean_group":
        lines.append("需要逐项给出 true/false 判断：")
        for f in fields:
            lines.append(f"- {f['key']} ({f['label_cn']})：{f['definition']}")
    elif fields and category["selection_type"] == "numeric_group":
        lines.append("需要逐项给出数值估计（没有把握时给最佳估计，不要留空或用'无法判断'）：")
        for f in fields:
            lines.append(f"- {f['key']} ({f['label_cn']}，单位：{f['unit']})：{f['definition']}")
    return "\n".join(lines)


def _render_country_match_section(target_country: str) -> str:
    return f"""# 国家/市场匹配判断（本次分析目标国家：{target_country}）

视频搜索时的候选池不保证只来自目标国家——Instagram/Facebook的搜索API本身不支持按国家过滤
（搜索词本地化只是弱相关信号，不是精确过滤）。所以需要你基于视频本身的语言、口音、画面中出现
的文字/场景/文化线索，独立判断这条视频是否匹配"{target_country}"这个目标市场。

- 只依据视频里能直接观察到的证据判断（人声语言、字幕语言、画面文字、场景文化线索等），不要因为
  搜索词本身是哪国语言就想当然地判定匹配——搜索词和实际命中内容可能不一致。
- 英语内容不自动等于"不匹配"——很多国家的创作者会用英语发布内容，仍然可能面向该国市场；判断要
  看整体文化/场景线索，不要只看语言这一个维度。
- 证据不足以判断时，诚实给 "uncertain"，不要为了给出明确答案而猜测。"""


def build_system_prompt(target_country: str | None = None) -> str:
    definitions = load_tag_definitions()
    category_sections = "\n\n".join(
        _render_category_for_prompt(c) for c in definitions["categories"]
    )
    country_match_section = f"\n\n{_render_country_match_section(target_country)}" if target_country else ""
    country_match_field = (
        ',\n  "country_match": {"assessment": "likely_match｜uncertain｜likely_mismatch", "evidence": "..."}'
        if target_country
        else ""
    )
    return f"""你是一位短视频广告/内容拆解分析师。你会收到一条社媒短视频（Instagram Reel 或 Facebook 视频），
需要同时理解画面、人声、背景音乐/音效，并完成两部分工作：

1. **故事线与卖点的自由文字分析**：这条视频在讲一个什么故事？它的核心卖点是什么？
2. **按下面给定的标签体系打标签**：每个类别的含义、可选值、是否可多选都已给出，严格按说明执行。

# 标签体系

{category_sections}{country_match_section}

# 关键规则

- 多选类别（multi_closed / multi_open / multi_closed_with_meta / multi_closed_extensible）返回字符串数组，
  即使只命中一个值也要用数组包裹；没有命中任何值时返回空数组 `[]`，不要编造。
- `rtb_type` 如果视频证据不属于任何给定值，可以新增一个描述性标签，并加 "NEW:" 前缀，不要强行套用一个不贴切的已有标签。
- `cover_audience` 是开放式的，用具体的人群画像描述（不是"女性"这种泛泛的词），没有把握就给一个最可能的推测并在
  `cover_audience_confidence_note` 里说明依据。
- `story_structure` 对 hook/problem/solution/cta 四段都要给出 `present`(boolean) 和 `description`
  (这段具体怎么表现的，一两句话；如果 present 为 false，description 留空字符串）。
- `three_act_structure` 是独立于 story_structure 的另一个判断，给出 `present`(boolean) 和 `evidence`(简短依据)。
- `information_gap` 的 `present` 为 false 时 `sub_types` 给空数组。
- `content_flags` 的每个子项都返回 boolean（true/false），不要返回字符串 "true"/"是" 之类。
- `quantitative_observations` 的每个子项都返回数字（int 或 float），单位见标签体系说明；确实无法估计时才允许返回
  `null`，但应尽量给出最佳估计而不是直接 null——下游会把这些数字分桶统计，粗略估计比空值更有用。
- `cep_occasion_type` 视频中若没有体现具体场景切入点，返回空数组 `[]`，不要编造。
- 不确定的判断，在对应字段后缀为 `_note` 的位置简短说明依据或不确定性，不要把推测说成确定事实（Evidence Policy）。
- 只输出 JSON，不要有任何 JSON 之外的文字、不要用 markdown 代码块包裹。

# 输出 JSON 结构（严格遵守字段名）

```json
{{
  "story_plot_summary": "这条视频讲了一个什么故事,从开场到结尾怎么推进的,2-4句话",
  "selling_points": ["卖点1", "卖点2"],
  "cover_audience": ["..."],
  "cover_audience_confidence_note": "",
  "identity": ["..."],
  "content_ownership": "品牌内容｜KOL/达人内容｜素人UGC内容",
  "pov": "第一人称｜第三人称｜无人称",
  "hook_type": {{"meta_category": "problem-aware｜aspirational｜social-proof｜curiosity", "tags": ["..."]}},
  "content_format": ["..."],
  "copy_type": ["..."],
  "story_structure": {{
    "hook": {{"present": true, "description": "..."}},
    "problem": {{"present": true, "description": "..."}},
    "solution": {{"present": true, "description": "..."}},
    "cta": {{"present": false, "description": ""}}
  }},
  "three_act_structure": {{"present": true, "evidence": "..."}},
  "rtb_type": ["..."],
  "information_gap": {{"present": true, "sub_types": ["..."]}},
  "content_flags": {{
    "product_shown_in_first_3s": true,
    "face_shown_in_first_3s": true,
    "text_overlay_hook_present": false,
    "price_mentioned": false,
    "price_comparison_present": false,
    "competitor_or_other_channel_mention": false,
    "continuous_use_claim": false,
    "engagement_hook_beyond_cta": false
  }},
  "quantitative_observations": {{
    "talent_count": 1,
    "product_first_appearance_sec": 0,
    "product_screen_time_pct": 40,
    "product_close_up_count": 2,
    "product_count": 1,
    "price_switch_count": 0
  }},
  "talent_gender": ["..."],
  "talent_age_appearance": ["..."],
  "talent_expertise_signal": ["..."],
  "cta_type": ["..."],
  "job_dimension": ["..."],
  "cep_occasion_type": ["..."]{country_match_field}
}}
```
"""

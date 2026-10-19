"""Generate the `原因分析` (one-sentence reason) column for frequency_analysis.py's findings
via batched Gemini text calls — the user explicitly required these be LLM-generated, not
hardcoded templates, since a templated "X is high therefore Y" explanation can't account for
context (target country, product category) the way a model call can.

Findings are chunked into batches of `BATCH_SIZE` (not one call for the ENTIRE list) — a real
run's finding count (tag categories × up to `MAX_VALUES_PER_FACTOR` × 2 metrics) easily exceeds
100 rows, and a single call asking Gemini to emit 100+ one-sentence strings as one JSON array
reliably got truncated mid-string against the real API (verified live 2026-10-16: valid JSON
turned into "Unterminated string" at the response's token cutoff). Batching keeps each call's
expected output comfortably under its token budget and isolates a truncation/parse failure to
just that batch's rows instead of the whole table silently losing every reason.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gemini_video_analysis import _call_with_retry, _is_vertex_client

_MODEL = "gemini-2.5-flash"
BATCH_SIZE = 25
_MAX_OUTPUT_TOKENS = 8192


def _build_prompt(findings: list[dict], target_country: str | None) -> str:
    country_note = f"本次分析的目标市场是{target_country}。" if target_country else ""
    payload = [
        {"metric": f["metric"], "factor": f["factor"], "value": f["value"],
         "sample_size": f["sample_size"], "pool_size": f["pool_size"]}
        for f in findings
    ]
    return f"""你是短视频内容策略分析师。下面是一批统计发现：在表现最好的视频集合里，某个标签/数值出现的
频率有多高。每条发现包含：
- metric：这是"播放量"最高的视频集合里的发现，还是"Engagement"（互动率）最高的视频集合里的发现
- factor：影响因素类别（如"Hook标签"、"产品画面占比"）
- value：具体的标签值或数值区间
- sample_size：在该指标最佳表现的视频集合里，命中这个标签/数值的视频数量
- pool_size：该指标最佳表现视频集合的总视频数（sample_size 的分母）

{country_note}

请为每一条发现写**恰好一句话**的可能原因分析——面向内容团队的人话解释，说明"为什么这个标签/数值可能
带来更高的{{播放量/Engagement}}"，不要复述发现本身的数字。如果 sample_size 相对 pool_size 占比很小
（比如不到3成），要诚实地在这句话里带一句"样本量较小，仅供参考"，不要编造过度确定的因果结论——这是硬性
要求（Evidence Policy：不能把推测说成确定事实）。句子要简短（一句话，不超过40字），因为这是一批发现,
篇幅需要控制。

发现列表（JSON数组，你必须按顺序为每一条输出一句话，输出数组长度必须和下面完全一致，长度={len(payload)}）：
{json.dumps(payload, ensure_ascii=False)}

只输出一个JSON数组，每个元素是一个字符串（一句话原因分析），不要输出任何数组之外的文字，不要用markdown
代码块包裹。
"""


def _extract_json_array(text: str) -> list:
    import re

    stripped = text.strip()
    match = re.search(r"```json\s*([\s\S]*?)\s*```", stripped)
    candidate = match.group(1).strip() if match else stripped
    if not candidate.startswith("["):
        raise ValueError(f"No JSON array found in model response: {text[:300]}...")
    return json.loads(candidate)


def _generate_for_batch(findings: list[dict], target_country: str | None) -> list[str]:
    """Runs one Gemini call for one batch. Raises on failure — the caller decides the
    per-batch fallback, this function never fabricates a reason itself."""
    from google.genai.types import Content, GenerateContentConfig, Part

    from bot.gemini_client import build_client

    prompt = _build_prompt(findings, target_country)
    client = build_client()
    use_vertexai = _is_vertex_client(client)

    def _send(c) -> str:
        response = c.models.generate_content(
            model=_MODEL,
            contents=[Content(role="user", parts=[Part.from_text(text=prompt)])],
            config=GenerateContentConfig(
                temperature=0.3, max_output_tokens=_MAX_OUTPUT_TOKENS, response_mime_type="application/json",
            ),
        )
        return response.text

    try:
        raw_text = _call_with_retry(lambda: _send(client))
    except Exception:
        import os

        api_key = os.getenv("GOOGLE_API_KEY")
        if not (use_vertexai and api_key):
            raise
        from google import genai
        from google.genai.types import HttpOptions

        fallback_client = genai.Client(api_key=api_key, http_options=HttpOptions(api_version="v1beta"))
        raw_text = _call_with_retry(lambda: _send(fallback_client))

    if not raw_text:
        raise RuntimeError("Gemini returned an empty response")
    reasons = _extract_json_array(raw_text)
    if len(reasons) != len(findings):
        raise ValueError(f"expected {len(findings)} reasons, got {len(reasons)}")
    return reasons


def generate_reasons(findings: list[dict], *, target_country: str | None = None) -> list[dict]:
    """Returns a NEW list (immutability — never mutate `findings` in place), same order,
    each entry with a `reason` key added. Findings are processed in `BATCH_SIZE`-sized
    chunks, concurrently. A batch that fails (Gemini error, truncated/malformed JSON, wrong
    array length) gets the same honest fallback string on every row IN THAT BATCH ONLY —
    the numeric columns, and every other batch's real reasons, are unaffected."""
    if not findings:
        return []

    batches = [findings[i:i + BATCH_SIZE] for i in range(0, len(findings), BATCH_SIZE)]

    def _run_one_batch(batch: list[dict]) -> list[str]:
        try:
            return _generate_for_batch(batch, target_country)
        except Exception as exc:
            return [f"(原因分析生成失败，未获得AI解释: {str(exc)[:200]})"] * len(batch)

    with ThreadPoolExecutor(max_workers=max(1, len(batches))) as pool:
        batch_reasons = list(pool.map(_run_one_batch, batches))

    all_reasons = [r for batch in batch_reasons for r in batch]
    return [{**f, "reason": r} for f, r in zip(findings, all_reasons, strict=True)]

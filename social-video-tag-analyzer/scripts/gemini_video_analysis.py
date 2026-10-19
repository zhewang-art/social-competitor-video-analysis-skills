"""Call Gemini with a video file + the tag-analysis prompt, returning parsed JSON.

Auth reuses `bot.gemini_client.build_client()` (ADC → GOOGLE_API_KEY fallback, the repo's
one shared Gemini auth path). Only the video-specific content-building (MIME detection,
inline-bytes vs. upload-and-poll, JSON response parsing) lives here — mirrors
`avatar-qc/scripts/analyze_video.py`'s `_call_gemini`/`_detect_mime_type`, the only other
place in this repo that sends a video file to Gemini.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bot.gemini_client import build_client, is_retryable

_MODEL = "gemini-2.5-flash"
_RETRY_DELAYS = [2, 4, 8, 16]


def _detect_mime_type(file_path: Path) -> str:
    with file_path.open("rb") as f:
        header = f.read(32)
    if header[4:8] == b"ftyp":
        return "video/mp4"
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "video/x-msvideo"
    ext_map = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    return ext_map.get(file_path.suffix.lower(), "video/mp4")


def _is_vertex_client(client) -> bool:
    # google-genai's Client exposes this internally; fall back to attribute probing
    # rather than importing a private symbol.
    return bool(getattr(getattr(client, "_api_client", None), "vertexai", False))


def _build_video_part(client, video_path: Path, use_vertexai: bool):
    from google.genai.types import Part

    mime_type = _detect_mime_type(video_path)
    if use_vertexai:
        return Part.from_bytes(data=video_path.read_bytes(), mime_type=mime_type)

    uploaded = client.files.upload(file=video_path)
    while uploaded.state.name == "PROCESSING":
        time.sleep(5)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state.name == "FAILED":
        raise RuntimeError(f"Gemini file processing failed: {uploaded.name}")
    return Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)


def _call_with_retry(fn):
    last_exc: BaseException | None = None
    for _attempt, delay in enumerate([*_RETRY_DELAYS, None], start=1):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    match = re.search(r"```json\s*([\s\S]*?)\s*```", stripped)
    candidate = match.group(1).strip() if match else stripped
    if not candidate.startswith("{"):
        raise ValueError(f"No JSON object found in model response: {text[:300]}...")
    return json.loads(candidate)


def analyze_video(video_path: Path, system_prompt: str) -> dict:
    """Send one video + the tag-analysis prompt to Gemini, return the parsed JSON dict.

    Raises on: missing file, Gemini call failure after retries, or a response that
    isn't valid JSON — the caller (run.py) is responsible for turning that into a
    per-video "failed" manifest row rather than aborting the whole batch.
    """
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    import os

    from google.genai.types import Content, GenerateContentConfig, Part

    client = build_client()
    use_vertexai = _is_vertex_client(client)

    def _send(c, vertexai: bool) -> str:
        video_part = _build_video_part(c, video_path, vertexai)
        config = GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=8192,
            response_mime_type="application/json",
        )
        response = c.models.generate_content(
            model=_MODEL,
            contents=[Content(role="user", parts=[video_part, Part.from_text(text=system_prompt)])],
            config=config,
        )
        return response.text

    try:
        raw_text = _call_with_retry(lambda: _send(client, use_vertexai))
    except Exception:
        # Mirrors avatar-qc's `_analyze_gemini`: ADC can successfully build a Vertex AI
        # client while the account still lacks `aiplatform.endpoints.predict` on the
        # resolved project (a permission gap, not a transient error) — verified live
        # 2026-10-16 against this project's own ADC. Fall back to GOOGLE_API_KEY once,
        # only when we were on the Vertex path and a key is actually configured.
        api_key = os.getenv("GOOGLE_API_KEY")
        if not (use_vertexai and api_key):
            raise
        from google import genai
        from google.genai.types import HttpOptions

        fallback_client = genai.Client(api_key=api_key, http_options=HttpOptions(api_version="v1beta"))
        raw_text = _call_with_retry(lambda: _send(fallback_client, False))

    if not raw_text:
        raise RuntimeError("Gemini returned an empty response")
    return _extract_json(raw_text)

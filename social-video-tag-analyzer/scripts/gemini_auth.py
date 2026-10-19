"""Self-contained Gemini auth — Vertex ADC preferred, GOOGLE_API_KEY fallback.

This is a deliberate COPY (not an import) of `bot/gemini_client.py`'s `build_client()` /
`is_retryable()`, trimmed to just what this skill uses. Copied rather than imported because
this skill must work standalone when distributed outside the anyai-live repo (as a shared
skill pack, see that pack's README) — importing `bot.gemini_client` via a `sys.path` reach-up
to the repo root (as earlier revisions of `gemini_video_analysis.py`/`reason_generator.py` did)
breaks the moment `bot/` isn't present alongside the skill, which is exactly the case once this
skill is copied standalone into someone else's `~/.codex/skills/`. Caught live 2026-10-19 when
a user tested the packaged skill outside this repo and Codex correctly flagged the broken
import before running anything.

If `bot/gemini_client.py`'s own auth logic changes, this copy does NOT update automatically —
re-sync by hand if the two drift apart in a way that matters for video/text Gemini calls.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_PROJECT = "anymind-pms"
_DEFAULT_REGION = "global"


def build_client():
    """Return a `google.genai` Client. Prefers Vertex AI via ADC (no API key); falls back to
    GOOGLE_API_KEY when ADC is unavailable. Raises RuntimeError if neither path is usable."""
    from google import genai
    from google.genai.types import HttpOptions

    api_key = os.getenv("GOOGLE_API_KEY")
    try:
        import google.auth

        credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        gcp_project = os.getenv("GCP_PROJECT_ID", project or _DEFAULT_PROJECT)
        gcp_region = os.getenv("GCP_REGION", _DEFAULT_REGION)
        client = genai.Client(
            vertexai=True, project=gcp_project, location=gcp_region,
            credentials=credentials, http_options=HttpOptions(api_version="v1"),
        )
        logger.info("Gemini via Vertex AI (project=%s, region=%s)", gcp_project, gcp_region)
        return client
    except Exception as exc:
        if not api_key:
            raise RuntimeError(
                f"Vertex AI ADC auth failed ({exc}) and no GOOGLE_API_KEY set. "
                "Run `gcloud auth application-default login` or set GOOGLE_API_KEY."
            ) from exc
        logger.warning("Vertex ADC failed (%s); falling back to GOOGLE_API_KEY", exc)
        return genai.Client(api_key=api_key, http_options=HttpOptions(api_version="v1beta"))


def is_retryable(exc: BaseException) -> bool:
    """True for transient errors worth retrying: Gemini 5xx / 429 / transport errors."""
    try:
        from google.genai import errors

        if isinstance(exc, errors.ServerError):
            return True
        if isinstance(exc, errors.ClientError) and getattr(exc, "code", None) == 429:
            return True
    except Exception:
        pass
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except Exception:
        pass
    return False

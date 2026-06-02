"""Gemini wrapper: receipt image + description -> ExtractedBill.

Single public function: ``extract_bill(image_b64, description)``.

Design choices:

* One Gemini call per request. Image and description go together so the
  model can cross-reference (e.g. "the pasta" -> the Penne Arrabiata line).
* ``response_mime_type="application/json"`` forces JSON output; we still
  parse defensively (strip code fences, locate the first ``{``) because
  the free-tier model occasionally adds prose.
* One retry on parse failure with an explicit "JSON only" reminder.
* Never let the model do arithmetic — the prompt forbids it and the
  splitter recomputes everything from line items.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.prompts import (
    EXTRACTOR_PROMPT_VERSION,
    EXTRACTOR_SYSTEM_PROMPT,
    RETRY_PROMPT,
    build_user_prompt,
)
from app.schemas import ExtractedBill
from app.settings import get_settings

logger = logging.getLogger("fairsplit.extractor")


class ExtractionError(RuntimeError):
    """Raised when Gemini fails or its output cannot be coerced to ExtractedBill."""


# --- lazy SDK init ------------------------------------------------------
# google.generativeai is imported lazily so the rest of the app
# (schemas, splitter, tests) stays importable without the SDK installed,
# and so unit tests don't need an API key.
_genai = None
_model = None


def _get_model():
    global _genai, _model
    if _model is not None:
        return _model

    settings = get_settings()
    if not settings.gemini_api_key:
        raise ExtractionError(
            "GEMINI_API_KEY not set. Add it to .env locally or Render env vars in prod."
        )

    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.gemini_api_key)
    _genai = genai
    _model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=EXTRACTOR_SYSTEM_PROMPT,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.1,  # near-zero: we want extraction, not creativity
        },
    )
    logger.info(
        "Gemini model initialized: %s (prompt %s)",
        settings.gemini_model,
        EXTRACTOR_PROMPT_VERSION,
    )
    return _model


# --- JSON cleanup helpers ----------------------------------------------
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _clean_json_text(text: str) -> str:
    """Strip markdown fences and isolate the JSON object if extra prose exists."""
    cleaned = _FENCE_RE.sub("", text).strip()
    # If there's leading/trailing prose, grab the outermost {...} block.
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return cleaned


def _parse_extracted(raw: str) -> ExtractedBill:
    """Parse a JSON string into ``ExtractedBill``; raises on failure."""
    cleaned = _clean_json_text(raw)
    try:
        payload: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Model returned non-JSON: {e.msg}") from e
    try:
        return ExtractedBill.model_validate(payload)
    except ValidationError as e:
        raise ExtractionError(f"Extracted JSON failed schema validation: {e}") from e


# --- public API ---------------------------------------------------------
def extract_bill(image_b64: str, description: str) -> ExtractedBill:
    """Call Gemini once (twice on JSON failure) and return a validated ExtractedBill.

    Parameters
    ----------
    image_b64:
        Base64-encoded image bytes, no data-URI prefix.
    description:
        Free-text "who had what" string from the user.

    Raises
    ------
    ExtractionError
        On invalid base64, model failure, or unparseable output.
    """
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except Exception as e:
        raise ExtractionError(f"receipt_base64 is not valid base64: {e}") from e

    model = _get_model()
    user_prompt = build_user_prompt(description)

    image_part = {"mime_type": _sniff_mime(image_bytes), "data": image_bytes}

    # --- attempt 1 ------------------------------------------------------
    try:
        resp = model.generate_content([user_prompt, image_part])
        text = (resp.text or "").strip()
    except Exception as e:  # pragma: no cover — network/SDK error
        raise ExtractionError(f"Gemini call failed: {e}") from e

    try:
        return _parse_extracted(text)
    except ExtractionError as first_err:
        logger.warning("First extraction parse failed (%s) — retrying", first_err)

    # --- attempt 2 (retry) ---------------------------------------------
    try:
        resp = model.generate_content([user_prompt, image_part, RETRY_PROMPT])
        text = (resp.text or "").strip()
    except Exception as e:  # pragma: no cover
        raise ExtractionError(f"Gemini retry call failed: {e}") from e

    return _parse_extracted(text)


# --- tiny mime sniffer -------------------------------------------------
def _sniff_mime(data: bytes) -> str:
    """Identify image type from magic bytes; default to JPEG."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"

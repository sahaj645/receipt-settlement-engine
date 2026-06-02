"""FastAPI application entrypoint.

Routes:
    GET  /healthz   liveness probe (Render uses this)
    GET  /          serves the single-page frontend
    POST /split     receipt + description -> per-person split

CORS is wide open by design — this is a public demo API with no auth.

A global exception handler converts unexpected errors into a 200 JSON
response shaped like a regular split result (empty per_person, a single
flag) so the frontend can render an error without special-casing 5xx.
The brief calls this out as a deliberate design choice.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.extractor import ExtractionError, extract_bill
from app.reconciler import reconcile
from app.schemas import (
    Reconciliation,
    SplitRequest,
    SplitResponse,
)
from app.settings import get_settings
from app.splitter import compute_split

logger = logging.getLogger("fairsplit")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Receipt photo + 'who had what' description -> reconciled per-person split.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- static frontend ----------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


# =====================================================================
# Routes
# =====================================================================
@app.get("/", include_in_schema=False, response_model=None)
def serve_index() -> Any:
    """Serve the single-file frontend if present, otherwise a JSON stub."""
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return JSONResponse(
        {"app": settings.app_name, "version": settings.app_version, "ui": "not built yet"}
    )


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    """Liveness probe. Returns 200 as long as the process is up."""
    return {"ok": True}


@app.post("/split", response_model=SplitResponse, response_model_by_alias=True)
def split_bill(req: SplitRequest) -> SplitResponse:
    """Main endpoint: extract -> split -> reconcile -> respond.

    The LLM only extracts structured data. All arithmetic happens in
    ``splitter.compute_split`` (pure Python, Decimal-precise).
    """
    logger.info(
        "POST /split | image_bytes=%d | desc_len=%d",
        len(req.receipt_base64),
        len(req.description),
    )

    extracted = extract_bill(req.receipt_base64, req.description)
    logger.info(
        "extracted: items=%d people=%s payer=%s",
        len(extracted.items),
        extracted.people,
        extracted.payer,
    )

    result = compute_split(extracted)

    extra_flags, extra_assumptions = reconcile(extracted, result, req.description)
    result.flags = list(dict.fromkeys(result.flags + extra_flags))
    result.assumptions = list(dict.fromkeys(result.assumptions + extra_assumptions))

    return result.to_response()


# =====================================================================
# Global error handling
# =====================================================================
def _error_response(message: str, *, status_code: int = 200) -> JSONResponse:
    """Return a SplitResponse-shaped error so the UI can render it directly."""
    payload = SplitResponse(
        per_person=[],
        grand_total=0,
        reconciliation=Reconciliation(sum_of_person_totals=0, matches_bill=False),
        paid_by=None,
        settle_up=[],
        assumptions=[],
        flags=[f"error: {message}"],
    ).model_dump(by_alias=True)
    return JSONResponse(payload, status_code=status_code)


@app.exception_handler(ExtractionError)
async def _handle_extraction_error(_: Request, exc: ExtractionError) -> JSONResponse:
    """Bad image, bad base64, model failure — surface to UI without 5xx noise."""
    logger.warning("ExtractionError: %s", exc)
    return _error_response(str(exc), status_code=200)


@app.exception_handler(Exception)
async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log full trace, return a friendly flag to the client.

    We return 200 (not 500) so the existing frontend renderer can show
    the message in the flags panel without special-casing HTTP status.
    """
    logger.exception("Unhandled exception in /split: %s", exc)
    return _error_response(f"unhandled: {type(exc).__name__}: {exc}", status_code=200)

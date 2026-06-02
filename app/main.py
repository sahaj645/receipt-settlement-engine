"""FastAPI application entrypoint.

Routes are deliberately small:
    GET  /healthz   liveness probe (Render uses this)
    GET  /          serves the single-page frontend
    POST /split     receipt + description -> per-person split (wired later)

CORS is wide open by design — this is a public demo API with no auth.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.settings import get_settings

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

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.routers import search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="LinkedIn Helper", version="0.1.0")

# Paths - detect if running on Vercel or locally
BASE_DIR = Path(__file__).resolve().parent.parent.parent
IS_VERCEL = os.environ.get("VERCEL", "") == "1"

if IS_VERCEL:
    # On Vercel, static files are served automatically from public/
    # Only mount API routes
    pass
else:
    # Local dev: serve static files and index.html via FastAPI
    FRONTEND_DIR = BASE_DIR / "frontend"
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")

app.include_router(search.router, prefix="/api")

if not IS_VERCEL:

    @app.get("/")
    async def index(request: Request):
        index_path = BASE_DIR / "frontend" / "index.html"
        return HTMLResponse(index_path.read_text())

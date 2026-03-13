from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Paths - detect if running on Vercel or locally
BASE_DIR = Path(__file__).resolve().parent.parent.parent
IS_VERCEL = os.environ.get("VERCEL", "") == "1"

app = FastAPI(
    title="LinkedIn Helper",
    version="0.1.0",
    docs_url=None if IS_VERCEL else "/docs",
    redoc_url=None,
    openapi_url=None if IS_VERCEL else "/openapi.json",
)

# --- CORS ---
allowed_origins = [
    "https://linkedin-helper-wine.vercel.app",
]
if not IS_VERCEL:
    allowed_origins += ["http://localhost:8000", "http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --- Security Headers ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# --- Rate Limiting (in-memory, resets per cold start on Vercel) ---
from collections import defaultdict
from time import time

_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_SEARCH = 5  # max searches per minute per IP


def _check_rate_limit(ip: str) -> bool:
    """Return True if rate limit exceeded."""
    now = time()
    # Clean old entries
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[ip]) >= RATE_LIMIT_MAX_SEARCH:
        return True
    _rate_limits[ip].append(now)
    return False


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit search endpoints
    if request.url.path in ("/api/search", "/api/search/run") and request.method == "POST":
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
        ip = ip.split(",")[0].strip()
        if _check_rate_limit(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many searches. Please wait a minute."},
            )
    return await call_next(request)


# --- Static files (local dev only) ---
if IS_VERCEL:
    pass
else:
    FRONTEND_DIR = BASE_DIR / "frontend"
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")

app.include_router(search.router, prefix="/api")

if not IS_VERCEL:

    @app.get("/")
    async def index(request: Request):
        index_path = BASE_DIR / "frontend" / "index.html"
        return HTMLResponse(index_path.read_text())

    @app.get("/s/{query_id}")
    async def shared_result(request: Request, query_id: str):
        index_path = BASE_DIR / "frontend" / "index.html"
        return HTMLResponse(index_path.read_text())

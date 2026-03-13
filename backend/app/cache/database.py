from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    natural_query TEXT NOT NULL,
    criteria_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result_count INTEGER DEFAULT 0,
    provider_stats_json TEXT,
    error_message TEXT,
    dimension_1_name TEXT,
    dimension_2_name TEXT,
    dimension_3_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    linkedin_url_normalized TEXT UNIQUE,
    full_name TEXT NOT NULL,
    linkedin_url TEXT,
    current_title TEXT,
    current_company TEXT,
    location TEXT,
    headline TEXT,
    summary TEXT,
    experience_json TEXT,
    education_json TEXT,
    skills_json TEXT,
    source_provider TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS query_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT NOT NULL REFERENCES queries(id),
    profile_id INTEGER NOT NULL REFERENCES profiles(id),
    rank INTEGER NOT NULL,
    match_score INTEGER NOT NULL,
    dimension_1_score INTEGER,
    dimension_2_score INTEGER,
    dimension_3_score INTEGER,
    match_reasons_json TEXT,
    concerns_json TEXT,
    summary TEXT,
    UNIQUE(query_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_queries_created ON queries(created_at);
CREATE INDEX IF NOT EXISTS idx_qr_query ON query_results(query_id);
CREATE INDEX IF NOT EXISTS idx_qr_profile ON query_results(profile_id);
"""


def _normalize_linkedin_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    return f"https://www.linkedin.com{path}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            is_vercel = os.environ.get("VERCEL", "") == "1"
            self.db_path = "/tmp/cache.db" if is_vercel else settings.cache_db_path
        else:
            self.db_path = db_path
        self._initialized = False

    async def _open(self) -> aiosqlite.Connection:
        """Open a new connection and ensure schema exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        if not self._initialized:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()
            self._initialized = True
        return conn

    # --- Query operations ---

    async def save_query(
        self,
        query_id: str,
        natural_query: str,
        criteria_json: str | None,
        dimensions: list[str],
        created_at: str | None = None,
    ) -> None:
        dims = dimensions + [""] * (3 - len(dimensions))
        conn = await self._open()
        try:
            await conn.execute(
                """INSERT OR REPLACE INTO queries
                   (id, natural_query, criteria_json, dimension_1_name, dimension_2_name, dimension_3_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (query_id, natural_query, criteria_json, dims[0], dims[1], dims[2], created_at or _now_iso()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def update_query_status(
        self, query_id: str, status: str, result_count: int = 0, error: str | None = None
    ) -> None:
        conn = await self._open()
        try:
            await conn.execute(
                "UPDATE queries SET status=?, result_count=?, error_message=? WHERE id=?",
                (status, result_count, error, query_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_query(self, query_id: str) -> dict | None:
        conn = await self._open()
        try:
            cursor = await conn.execute("SELECT * FROM queries WHERE id=?", (query_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def list_queries(self, limit: int = 20) -> list[dict]:
        conn = await self._open()
        try:
            cursor = await conn.execute(
                "SELECT * FROM queries WHERE status='completed' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in await cursor.fetchall()]
        finally:
            await conn.close()

    # --- Profile operations ---

    async def _upsert_profile(self, conn: aiosqlite.Connection, profile) -> int:
        """Insert or update a profile within an existing connection. Returns the profile's DB id."""
        now = _now_iso()

        if profile.linkedin_url:
            norm_url = _normalize_linkedin_url(profile.linkedin_url)
        else:
            norm_url = f"no-url:{profile.full_name.lower()}|{(profile.current_company or '').lower()}"

        cursor = await conn.execute(
            "SELECT id, hit_count FROM profiles WHERE linkedin_url_normalized=?", (norm_url,)
        )
        existing = await cursor.fetchone()

        if existing:
            await conn.execute(
                "UPDATE profiles SET last_seen_at=?, hit_count=? WHERE id=?",
                (now, existing["hit_count"] + 1, existing["id"]),
            )
            return existing["id"]

        cursor = await conn.execute(
            """INSERT INTO profiles
               (linkedin_url_normalized, full_name, linkedin_url, current_title, current_company,
                location, headline, summary, experience_json, education_json, skills_json,
                source_provider, first_seen_at, last_seen_at, hit_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                norm_url,
                profile.full_name,
                profile.linkedin_url,
                profile.current_title,
                profile.current_company,
                profile.location,
                profile.headline,
                profile.summary,
                json.dumps([e.model_dump() for e in profile.experience]) if profile.experience else None,
                json.dumps([e.model_dump() for e in profile.education]) if profile.education else None,
                json.dumps(profile.skills) if profile.skills else None,
                profile.source_provider,
                now,
                now,
            ),
        )
        return cursor.lastrowid

    # --- Query-Result link operations ---

    async def save_full_results(self, query_id: str, evaluated: list, dimensions: list[str]) -> None:
        """Batch save: upsert all profiles, then save all query_results."""
        conn = await self._open()
        try:
            for ep in evaluated:
                profile_id = await self._upsert_profile(conn, ep.profile)

                sub_scores = ep.evaluation.sub_scores or []
                d1 = sub_scores[0].score if len(sub_scores) > 0 else None
                d2 = sub_scores[1].score if len(sub_scores) > 1 else None
                d3 = sub_scores[2].score if len(sub_scores) > 2 else None

                await conn.execute(
                    """INSERT OR REPLACE INTO query_results
                       (query_id, profile_id, rank, match_score,
                        dimension_1_score, dimension_2_score, dimension_3_score,
                        match_reasons_json, concerns_json, summary)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        query_id,
                        profile_id,
                        ep.rank,
                        ep.evaluation.match_score,
                        d1,
                        d2,
                        d3,
                        json.dumps(ep.evaluation.match_reasons),
                        json.dumps(ep.evaluation.concerns),
                        ep.evaluation.summary,
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def get_query_results(self, query_id: str) -> list[dict]:
        """Load all results for a query, joined with profile data."""
        conn = await self._open()
        try:
            # Get query for dimension names
            qcursor = await conn.execute("SELECT * FROM queries WHERE id=?", (query_id,))
            query = await qcursor.fetchone()
            if not query:
                return []
            dims = [query["dimension_1_name"], query["dimension_2_name"], query["dimension_3_name"]]

            cursor = await conn.execute(
                """SELECT qr.*, p.*,
                          qr.id AS qr_id, p.id AS profile_db_id,
                          qr.summary AS eval_summary, p.summary AS profile_summary
                   FROM query_results qr
                   JOIN profiles p ON qr.profile_id = p.id
                   WHERE qr.query_id = ?
                   ORDER BY qr.rank""",
                (query_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        results = []
        for row in rows:
            row = dict(row)
            sub_scores = []
            for i, dim_name in enumerate(dims):
                score_val = row.get(f"dimension_{i + 1}_score")
                if dim_name and score_val is not None:
                    sub_scores.append({"name": dim_name, "score": score_val})

            results.append({
                "profile": {
                    "full_name": row["full_name"],
                    "linkedin_url": row["linkedin_url"],
                    "current_title": row["current_title"],
                    "current_company": row["current_company"],
                    "location": row["location"],
                    "headline": row["headline"],
                    "summary": row["profile_summary"],
                    "experience": json.loads(row["experience_json"]) if row["experience_json"] else [],
                    "education": json.loads(row["education_json"]) if row["education_json"] else [],
                    "skills": json.loads(row["skills_json"]) if row["skills_json"] else [],
                    "source_provider": row["source_provider"] or "",
                },
                "evaluation": {
                    "match_score": row["match_score"],
                    "sub_scores": sub_scores,
                    "match_reasons": json.loads(row["match_reasons_json"]) if row["match_reasons_json"] else [],
                    "concerns": json.loads(row["concerns_json"]) if row["concerns_json"] else [],
                    "summary": row["eval_summary"] or "",
                },
                "rank": row["rank"],
                "hit_count": row["hit_count"],
            })

        return results

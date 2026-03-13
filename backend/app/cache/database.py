from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)

# Each CREATE TABLE is executed separately (asyncpg doesn't support executescript)
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS queries (
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
        created_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS profiles (
        id SERIAL PRIMARY KEY,
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
        first_seen_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL,
        hit_count INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS query_results (
        id SERIAL PRIMARY KEY,
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_queries_created ON queries(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_qr_query ON query_results(query_id)",
    "CREATE INDEX IF NOT EXISTS idx_qr_profile ON query_results(profile_id)",
]


def _normalize_linkedin_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    return f"https://www.linkedin.com{path}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, database_url: str | None = None) -> None:
        # Use provided URL, or fall back to POSTGRES_URL env var
        self._database_url = database_url or os.environ.get("POSTGRES_URL", "")
        # Vercel Postgres uses postgres:// but asyncpg needs postgresql://
        if self._database_url.startswith("postgres://"):
            self._database_url = self._database_url.replace("postgres://", "postgresql://", 1)
        self._initialized = False
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create a connection pool and ensure schema exists."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=1,
                max_size=5,
            )
        if not self._initialized:
            async with self._pool.acquire() as conn:
                for stmt in SCHEMA_STATEMENTS:
                    await conn.execute(stmt)
            self._initialized = True
        return self._pool

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
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO queries
                   (id, natural_query, criteria_json, dimension_1_name, dimension_2_name, dimension_3_name, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (id) DO UPDATE SET
                       natural_query = EXCLUDED.natural_query,
                       criteria_json = EXCLUDED.criteria_json,
                       dimension_1_name = EXCLUDED.dimension_1_name,
                       dimension_2_name = EXCLUDED.dimension_2_name,
                       dimension_3_name = EXCLUDED.dimension_3_name""",
                query_id, natural_query, criteria_json, dims[0], dims[1], dims[2],
                datetime.fromisoformat(created_at) if created_at else _now(),
            )

    async def update_query_status(
        self, query_id: str, status: str, result_count: int = 0, error: str | None = None
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE queries SET status=$1, result_count=$2, error_message=$3 WHERE id=$4",
                status, result_count, error, query_id,
            )

    async def get_query(self, query_id: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM queries WHERE id=$1", query_id)
            return dict(row) if row else None

    async def list_queries(self, limit: int = 20) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM queries WHERE status='completed' ORDER BY created_at DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

    # --- Profile operations ---

    async def _upsert_profile(self, conn: asyncpg.Connection, profile) -> int:
        """Insert or update a profile within an existing connection. Returns the profile's DB id."""
        now = _now()

        if profile.linkedin_url:
            norm_url = _normalize_linkedin_url(profile.linkedin_url)
        else:
            norm_url = f"no-url:{profile.full_name.lower()}|{(profile.current_company or '').lower()}"

        existing = await conn.fetchrow(
            "SELECT id, hit_count FROM profiles WHERE linkedin_url_normalized=$1", norm_url
        )

        if existing:
            await conn.execute(
                "UPDATE profiles SET last_seen_at=$1, hit_count=$2 WHERE id=$3",
                now, existing["hit_count"] + 1, existing["id"],
            )
            return existing["id"]

        row = await conn.fetchrow(
            """INSERT INTO profiles
               (linkedin_url_normalized, full_name, linkedin_url, current_title, current_company,
                location, headline, summary, experience_json, education_json, skills_json,
                source_provider, first_seen_at, last_seen_at, hit_count)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 1)
               RETURNING id""",
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
        )
        return row["id"]

    # --- Query-Result link operations ---

    async def save_full_results(self, query_id: str, evaluated: list, dimensions: list[str]) -> None:
        """Batch save: upsert all profiles, then save all query_results."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for ep in evaluated:
                    profile_id = await self._upsert_profile(conn, ep.profile)

                    sub_scores = ep.evaluation.sub_scores or []
                    d1 = sub_scores[0].score if len(sub_scores) > 0 else None
                    d2 = sub_scores[1].score if len(sub_scores) > 1 else None
                    d3 = sub_scores[2].score if len(sub_scores) > 2 else None

                    await conn.execute(
                        """INSERT INTO query_results
                           (query_id, profile_id, rank, match_score,
                            dimension_1_score, dimension_2_score, dimension_3_score,
                            match_reasons_json, concerns_json, summary)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                           ON CONFLICT (query_id, profile_id) DO UPDATE SET
                               rank = EXCLUDED.rank,
                               match_score = EXCLUDED.match_score,
                               dimension_1_score = EXCLUDED.dimension_1_score,
                               dimension_2_score = EXCLUDED.dimension_2_score,
                               dimension_3_score = EXCLUDED.dimension_3_score,
                               match_reasons_json = EXCLUDED.match_reasons_json,
                               concerns_json = EXCLUDED.concerns_json,
                               summary = EXCLUDED.summary""",
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
                    )

    async def get_query_results(self, query_id: str) -> list[dict]:
        """Load all results for a query, joined with profile data."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Get query for dimension names
            query = await conn.fetchrow("SELECT * FROM queries WHERE id=$1", query_id)
            if not query:
                return []
            dims = [query["dimension_1_name"], query["dimension_2_name"], query["dimension_3_name"]]

            rows = await conn.fetch(
                """SELECT qr.*, p.*,
                          qr.id AS qr_id, p.id AS profile_db_id,
                          qr.summary AS eval_summary, p.summary AS profile_summary
                   FROM query_results qr
                   JOIN profiles p ON qr.profile_id = p.id
                   WHERE qr.query_id = $1
                   ORDER BY qr.rank""",
                query_id,
            )

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

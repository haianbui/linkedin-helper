from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlparse

from app.models.evaluation import EvaluatedProfile
from app.models.profile import ProfileResult
from app.models.search import SearchCriteria, SearchSession, SearchStatus
from app.providers.registry import ProviderRegistry
from app.services.profile_evaluator import ProfileEvaluator
from app.services.query_decomposer import QueryDecomposer

logger = logging.getLogger(__name__)


@dataclass
class SSEEvent:
    event: str
    data: str


def _normalize_linkedin_url(url: str) -> str:
    """Normalize a LinkedIn URL for deduplication."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    return f"https://www.linkedin.com{path}"


def _deduplicate(profiles: list[ProfileResult]) -> list[ProfileResult]:
    """Remove duplicate profiles based on LinkedIn URL or name+company."""
    seen: dict[str, ProfileResult] = {}
    for p in profiles:
        if p.linkedin_url:
            key = _normalize_linkedin_url(p.linkedin_url)
        else:
            key = f"{p.full_name.lower()}|{(p.current_company or '').lower()}"
        if key not in seen:
            seen[key] = p
    return list(seen.values())


class SearchOrchestrator:
    def __init__(
        self,
        decomposer: QueryDecomposer,
        evaluator: ProfileEvaluator,
        registry: ProviderRegistry,
    ) -> None:
        self.decomposer = decomposer
        self.evaluator = evaluator
        self.registry = registry

    async def execute_search(
        self,
        session: SearchSession,
    ) -> AsyncIterator[SSEEvent]:
        try:
            # Phase 1: Decompose query
            session.status = SearchStatus.DECOMPOSING
            yield SSEEvent(event="status", data="Analyzing your search query with AI...")

            criteria = await self.decomposer.decompose(session.natural_query)
            session.criteria = criteria

            yield SSEEvent(event="criteria", data=criteria.model_dump_json())
            yield SSEEvent(
                event="status",
                data=f"Parsed: {len(criteria.job_titles)} titles, "
                f"{len(criteria.locations)} locations, "
                f"{len(criteria.search_strategies)} search strategies",
            )

            # Phase 2: Search across providers
            session.status = SearchStatus.SEARCHING
            yield SSEEvent(event="status", data="Searching across data providers...")

            providers = await self.registry.get_available_search_providers()
            if not providers:
                yield SSEEvent(event="error", data="No search providers are configured. Add API keys to .env")
                session.status = SearchStatus.FAILED
                session.error_message = "No providers configured"
                return

            yield SSEEvent(
                event="status",
                data=f"Using {len(providers)} provider(s): {', '.join(p.name for p in providers)}",
            )

            raw_profiles: list[ProfileResult] = []
            for provider in providers:
                try:
                    async for profile in provider.search(criteria, max_results=50):
                        raw_profiles.append(profile)
                    session.provider_stats[provider.name] = len(
                        [p for p in raw_profiles if p.source_provider == provider.name]
                    )
                    yield SSEEvent(
                        event="status",
                        data=f"{provider.name}: found {session.provider_stats[provider.name]} profiles",
                    )
                except Exception as e:
                    logger.error("Provider %s failed: %s", provider.name, e)
                    yield SSEEvent(event="status", data=f"{provider.name}: error - {e}")

            if not raw_profiles:
                yield SSEEvent(event="error", data="No profiles found. Try a broader search query.")
                session.status = SearchStatus.FAILED
                session.error_message = "No results"
                return

            # Phase 3: Deduplicate
            unique_profiles = _deduplicate(raw_profiles)
            yield SSEEvent(
                event="status",
                data=f"Found {len(raw_profiles)} raw results → {len(unique_profiles)} unique profiles",
            )

            # Phase 4: Evaluate and rank with Claude
            session.status = SearchStatus.EVALUATING
            yield SSEEvent(
                event="status",
                data=f"AI is evaluating {len(unique_profiles)} profiles against your criteria...",
            )

            evaluated = await self.evaluator.evaluate_batch(
                profiles=unique_profiles,
                criteria=criteria,
                original_query=session.natural_query,
            )

            # Phase 5: Stream results
            for ep in evaluated:
                yield SSEEvent(event="result", data=ep.model_dump_json())

            session.status = SearchStatus.COMPLETED
            session.result_count = len(evaluated)

            yield SSEEvent(
                event="complete",
                data=json.dumps(
                    {
                        "total": len(evaluated),
                        "session_id": session.id,
                        "provider_stats": session.provider_stats,
                    }
                ),
            )

        except Exception as e:
            logger.exception("Search failed")
            session.status = SearchStatus.FAILED
            session.error_message = str(e)
            yield SSEEvent(event="error", data=f"Search failed: {e}")

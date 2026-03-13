from __future__ import annotations

import anthropic

from app.cache.database import Database
from app.config import settings
from app.providers.registry import ProviderRegistry
from app.providers.serp import SerpProvider
from app.services.profile_evaluator import ProfileEvaluator
from app.services.query_decomposer import QueryDecomposer
from app.services.search_orchestrator import SearchOrchestrator

# Shared instances
_claude_client: anthropic.AsyncAnthropic | None = None
_registry: ProviderRegistry | None = None
_orchestrator: SearchOrchestrator | None = None
_database: Database | None = None


def get_claude_client() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _claude_client


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.register(SerpProvider())
    return _registry


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database(database_url=settings.postgres_url or None)
    return _database


def get_orchestrator() -> SearchOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        client = get_claude_client()
        _orchestrator = SearchOrchestrator(
            decomposer=QueryDecomposer(client),
            evaluator=ProfileEvaluator(client),
            registry=get_registry(),
            database=get_database(),
        )
    return _orchestrator

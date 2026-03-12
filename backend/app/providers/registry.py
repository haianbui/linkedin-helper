from __future__ import annotations

import logging

from app.providers.base import SearchProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Manages available search providers."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        self._providers[provider.name] = provider
        logger.info("Registered provider: %s", provider.name)

    async def get_available_search_providers(self) -> list[SearchProvider]:
        """Return providers that are configured and healthy."""
        available = []
        for provider in self._providers.values():
            if provider.supports_search and await provider.health_check():
                available.append(provider)
        return available

    async def get_available_enrichment_providers(self) -> list[SearchProvider]:
        """Return providers that support enrichment and are healthy."""
        available = []
        for provider in self._providers.values():
            if provider.supports_enrichment and await provider.health_check():
                available.append(provider)
        return available

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.models.profile import ProfileResult
from app.models.search import SearchCriteria


class SearchProvider(ABC):
    """Interface for providers that can search for people."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def supports_search(self) -> bool: ...

    @property
    @abstractmethod
    def supports_enrichment(self) -> bool: ...

    @abstractmethod
    async def search(
        self,
        criteria: SearchCriteria,
        max_results: int = 100,
    ) -> AsyncIterator[ProfileResult]:
        """Yield profile results as they are found."""
        ...

    async def enrich(self, profile: ProfileResult) -> ProfileResult:
        """Enrich a profile with additional data. Default: no-op."""
        return profile

    async def health_check(self) -> bool:
        """Check if this provider is configured and reachable."""
        return True

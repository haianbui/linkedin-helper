from __future__ import annotations

import logging
import re
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models.profile import ProfileResult
from app.models.search import SearchCriteria
from app.providers.base import SearchProvider

logger = logging.getLogger(__name__)

SERP_API_URL = "https://serpapi.com/search"


def _extract_linkedin_url(url: str) -> str | None:
    """Extract and normalize a linkedin.com/in/ URL."""
    parsed = urlparse(url)
    if not parsed.hostname or "linkedin.com" not in parsed.hostname:
        return None
    # Match /in/username pattern
    match = re.match(r"(/in/[^/?#]+)", parsed.path)
    if not match:
        return None
    return f"https://www.linkedin.com{match.group(1)}"


def _parse_name_from_title(title: str) -> str:
    """Extract name from a Google result title like 'John Doe - Product Manager - Company | LinkedIn'."""
    # Remove "| LinkedIn" suffix
    name = re.sub(r"\s*[|\-–]\s*LinkedIn\s*$", "", title, flags=re.IGNORECASE)
    # Take the first segment (before first dash/pipe)
    name = re.split(r"\s*[|\-–]\s*", name)[0].strip()
    return name


def _parse_headline_from_title(title: str) -> str | None:
    """Extract headline from Google result title."""
    parts = re.split(r"\s*[|\-–]\s*", title)
    if len(parts) >= 2:
        # Skip name (first) and "LinkedIn" (last)
        middle = [p.strip() for p in parts[1:] if "linkedin" not in p.lower()]
        if middle:
            return " - ".join(middle)
    return None


class SerpProvider(SearchProvider):
    @property
    def name(self) -> str:
        return "serpapi"

    @property
    def supports_search(self) -> bool:
        return True

    @property
    def supports_enrichment(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return bool(settings.serp_api_key)

    async def search(
        self,
        criteria: SearchCriteria,
        max_results: int = 100,
    ) -> AsyncIterator[ProfileResult]:
        if not settings.serp_api_key:
            logger.warning("SerpAPI key not configured, skipping")
            return

        seen_urls: set[str] = set()
        results_yielded = 0

        for strategy in criteria.search_strategies:
            if results_yielded >= max_results:
                break

            dork = strategy.google_dork
            if not dork:
                continue

            logger.info("SerpAPI search: %s", dork)

            async with httpx.AsyncClient(timeout=30) as client:
                # Fetch up to 3 pages per strategy
                for page in range(3):
                    if results_yielded >= max_results:
                        break

                    params = {
                        "engine": "google",
                        "q": dork,
                        "api_key": settings.serp_api_key,
                        "num": 10,
                        "start": page * 10,
                    }

                    try:
                        resp = await client.get(SERP_API_URL, params=params)
                        resp.raise_for_status()
                        data = resp.json()
                    except Exception as e:
                        logger.error("SerpAPI request failed: %s", e)
                        break

                    organic = data.get("organic_results", [])
                    if not organic:
                        break

                    for result in organic:
                        if results_yielded >= max_results:
                            break

                        url = result.get("link", "")
                        linkedin_url = _extract_linkedin_url(url)
                        if not linkedin_url or linkedin_url in seen_urls:
                            continue

                        seen_urls.add(linkedin_url)
                        title = result.get("title", "")
                        snippet = result.get("snippet", "")

                        name = _parse_name_from_title(title)
                        if not name:
                            continue

                        headline = _parse_headline_from_title(title)

                        # Try to extract location from snippet
                        location = None
                        for loc in criteria.locations:
                            if loc.lower() in snippet.lower():
                                location = loc
                                break

                        profile = ProfileResult(
                            full_name=name,
                            linkedin_url=linkedin_url,
                            headline=headline,
                            summary=snippet,
                            location=location,
                            source_provider=self.name,
                        )

                        results_yielded += 1
                        yield profile

        logger.info("SerpAPI yielded %d profiles", results_yielded)

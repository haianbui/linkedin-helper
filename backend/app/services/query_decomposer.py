from __future__ import annotations

import json
import logging

import anthropic

from app.config import settings
from app.models.search import SearchCriteria

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a LinkedIn people search query parser. Given a natural language search query, \
extract structured search criteria and return ONLY valid JSON (no markdown fences).

Return a JSON object with these fields:
{
  "job_titles": ["list of relevant job titles, include common variations"],
  "locations": ["countries, cities, or regions"],
  "industries": ["relevant industries"],
  "keywords": ["important keywords for profile matching"],
  "company_attributes": {"industry": "...", "keywords": ["..."]},
  "experience_requirements": ["specific experience criteria in plain English"],
  "education_requirements": ["education criteria if any"],
  "seniority_levels": ["entry", "mid", "senior", "executive"],
  "boolean_logic": "explanation of how criteria combine (AND/OR)",
  "search_strategies": [
    {
      "description": "what this sub-search targets",
      "google_dork": "site:linkedin.com/in query string"
    }
  ],
  "semantic_intent": "one-paragraph description of the ideal candidate",
  "scoring_dimensions": ["Dimension 1", "Dimension 2", "Dimension 3"]
}

Guidelines:
- For job_titles, include common variations (e.g. "Product Manager" → also "Senior Product Manager", "Head of Product", "PM")
- For locations, include both country and major cities
- Generate 2-4 google_dork search strategies that together cover the query from different angles
- Google dork format: site:linkedin.com/in "exact phrase" keyword1 OR keyword2
- Keep boolean_logic clear: explain which criteria are AND vs OR
- semantic_intent should capture the full nuance of what the user is looking for
- scoring_dimensions: Choose exactly 3 scoring dimensions most relevant to THIS specific query. \
These are the 3 most important axes for evaluating candidates. Keep names short (2-3 words). \
Examples: "Role Fit", "Location Match", "AI Experience", "Seniority Level", "Company Prestige", \
"Technical Depth", "Domain Expertise", "Leadership Experience", "Industry Relevance"
"""


class QueryDecomposer:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def decompose(self, query: str) -> SearchCriteria:
        logger.info("Decomposing query: %s", query)

        response = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            raw_text = "\n".join(lines)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse Claude response as JSON: %s", raw_text[:200])
            raise ValueError("Failed to parse search criteria from LLM response")

        criteria = SearchCriteria.model_validate(data)

        # Ensure exactly 3 scoring dimensions
        if len(criteria.scoring_dimensions) != 3:
            criteria.scoring_dimensions = ["Overall Fit", "Experience Match", "Criteria Alignment"]

        logger.info(
            "Decomposed into %d titles, %d locations, %d strategies, dimensions: %s",
            len(criteria.job_titles),
            len(criteria.locations),
            len(criteria.search_strategies),
            criteria.scoring_dimensions,
        )
        return criteria

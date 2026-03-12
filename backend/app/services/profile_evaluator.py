from __future__ import annotations

import json
import logging

import anthropic

from app.config import settings
from app.models.evaluation import EvaluatedProfile, MatchExplanation
from app.models.profile import ProfileResult
from app.models.search import SearchCriteria

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are evaluating LinkedIn profiles against a user's search criteria.

For each profile, provide a JSON object with:
- match_score: 0-100 (how well they match the search intent)
- match_reasons: list of specific reasons they match
- concerns: list of reasons they might not be a perfect match
- summary: 1-2 sentence explanation

Return a JSON array with one object per profile, in the same order as provided.
Return ONLY valid JSON (no markdown fences).

Score guidelines:
- 90-100: Near-perfect match on all criteria
- 70-89: Strong match, meets most criteria
- 50-69: Partial match, meets some key criteria
- 30-49: Weak match, tangential relevance
- 0-29: Poor match, does not fit the criteria
"""


def _format_profile(p: ProfileResult) -> str:
    parts = [f"Name: {p.full_name}"]
    if p.headline:
        parts.append(f"Headline: {p.headline}")
    if p.current_title:
        parts.append(f"Current: {p.current_title} at {p.current_company or 'N/A'}")
    if p.location:
        parts.append(f"Location: {p.location}")
    if p.summary:
        parts.append(f"Summary: {p.summary[:300]}")
    for exp in p.experience[:5]:
        line = f"  - {exp.title} at {exp.company}"
        if exp.description:
            line += f" ({exp.description[:100]})"
        parts.append(line)
    if p.skills:
        parts.append(f"Skills: {', '.join(p.skills[:15])}")
    return "\n".join(parts)


class ProfileEvaluator:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def evaluate_batch(
        self,
        profiles: list[ProfileResult],
        criteria: SearchCriteria,
        original_query: str,
        batch_size: int = 10,
    ) -> list[EvaluatedProfile]:
        if not profiles:
            return []

        import asyncio

        # Run all batches concurrently for speed
        batches = [
            profiles[i : i + batch_size]
            for i in range(0, len(profiles), batch_size)
        ]
        tasks = [
            self._evaluate_chunk(batch, criteria, original_query)
            for batch in batches
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[EvaluatedProfile] = []
        for batch, batch_result in zip(batches, batch_results):
            if isinstance(batch_result, Exception):
                logger.error("Evaluation batch failed: %s", batch_result)
                for p in batch:
                    results.append(
                        EvaluatedProfile(
                            profile=p,
                            evaluation=MatchExplanation(match_score=0, summary="Evaluation failed"),
                        )
                    )
            else:
                results.extend(batch_result)

        # Sort by score descending and reassign ranks
        results.sort(key=lambda x: x.evaluation.match_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    async def _evaluate_chunk(
        self,
        profiles: list[ProfileResult],
        criteria: SearchCriteria,
        original_query: str,
    ) -> list[EvaluatedProfile]:
        profiles_text = "\n\n---\n\n".join(
            f"[Profile {i + 1}]\n{_format_profile(p)}" for i, p in enumerate(profiles)
        )

        user_message = (
            f"Original search query: {original_query}\n\n"
            f"Search criteria:\n{criteria.model_dump_json(indent=2)}\n\n"
            f"Profiles to evaluate ({len(profiles)} total):\n\n{profiles_text}"
        )

        response = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            raw_text = "\n".join(lines)

        try:
            evaluations = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse evaluation response: %s", raw_text[:200])
            # Return profiles with zero scores
            return [
                EvaluatedProfile(
                    profile=p,
                    evaluation=MatchExplanation(
                        match_score=0,
                        summary="Could not evaluate this profile",
                    ),
                )
                for p in profiles
            ]

        results = []
        for profile, eval_data in zip(profiles, evaluations):
            try:
                explanation = MatchExplanation.model_validate(eval_data)
            except Exception:
                explanation = MatchExplanation(match_score=0, summary="Evaluation parse error")
            results.append(EvaluatedProfile(profile=profile, evaluation=explanation))

        return results

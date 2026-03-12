from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field


class ProfileExperience(BaseModel):
    title: str
    company: str
    company_linkedin_url: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    is_current: bool = False


class ProfileEducation(BaseModel):
    school: str
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    end_year: int | None = None


class ProfileResult(BaseModel):
    """Unified profile format across all providers."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    linkedin_url: str | None = None
    profile_photo_url: str | None = None

    current_title: str | None = None
    current_company: str | None = None

    location: str | None = None
    city: str | None = None
    country: str | None = None

    headline: str | None = None
    summary: str | None = None
    experience: list[ProfileExperience] = Field(default_factory=list)
    education: list[ProfileEducation] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    industry: str | None = None

    source_provider: str = ""
    is_enriched: bool = False

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from .profile_records import ApplicantRecords


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(Model):
    records: ApplicantRecords = Field(default_factory=ApplicantRecords)
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    country: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    skill_years: dict[str, float] = Field(default_factory=dict)
    work_history: str = ""
    education: str = ""
    availability: str = ""
    salary_expectation: str = ""
    willing_to_relocate: bool | None = None
    relocation_countries: list[str] = Field(default_factory=list)
    authorization: dict[str, bool] = Field(default_factory=dict)
    sponsorship: dict[str, bool] = Field(default_factory=dict)
    saved_answers: dict[str, str] = Field(default_factory=dict)
    demographic_preference: Literal["skip", "decline"] = "skip"

    @field_validator("skill_years")
    @classmethod
    def valid_years(cls, value):
        if any(v < 0 or v > 70 for v in value.values()):
            raise ValueError("Experience must be between 0 and 70 years")
        return value


class Search(Model):
    id: str = Field(default_factory=uid)
    name: str = "My search"
    enabled: bool = True
    titles: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    workplaces: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    included_companies: list[str] = Field(default_factory=list)
    min_salary: float | None = Field(default=None, ge=0)
    max_salary: float | None = Field(default=None, ge=0)
    salary_currency: str = "USD"
    salary_period: Literal["year", "month", "hour"] = "year"
    posted_within_days: int = Field(default=7, ge=1, le=365)
    easy_apply_only: bool = False
    require_authorization: bool = False
    sponsorship_required: bool = False
    missing_information: Literal["review", "reject"] = "review"
    min_score: int = Field(default=55, ge=0, le=100)


class Settings(Model):
    mode: Literal["review", "automatic"] = "review"
    paused: bool = True
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    max_jobs_per_run: int = Field(default=25, ge=1, le=100)
    daily_limit: int = Field(default=20, ge=1, le=500)
    cv_min_score: int = Field(default=20, ge=0, le=100)
    timezone: str = "Asia/Karachi"
    operating_start: str = "00:00"
    operating_end: str = "00:00"
    trusted_employers: dict[str, str] = Field(default_factory=dict)
    model_enabled: bool = False
    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = ""
    allow_cloud_model: bool = False
    allow_registration: bool = False
    registration_hosts: list[str] = Field(default_factory=list)
    sso_hosts: dict[str, list[str]] = Field(default_factory=dict)
    technology_aliases: dict[str, str] = Field(default_factory=dict)
    max_search_pages: int = Field(default=20, ge=1, le=100)
    retry_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: int = Field(default=5, ge=1, le=3600)
    portal_cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    prevent_sleep: bool = False

    @field_validator("registration_hosts")
    @classmethod
    def valid_registration_hosts(cls, value):
        return [cls.validate_host(h) for h in value]

    @field_validator("sso_hosts")
    @classmethod
    def valid_sso_hosts(cls, value):
        return {cls.validate_host(h): [cls.validate_host(a) for a in allowed] for h, allowed in value.items()}

    @staticmethod
    def validate_host(value):
        from .security import public_url, hostname
        if not isinstance(value, str) or ":" in value or "/" in value or "@" in value or "?" in value or "#" in value or not public_url("https://" + value, resolve=False):
            raise ValueError("Enter an exact public hostname without scheme, port, or path")
        return hostname("https://" + value)

    @field_validator("technology_aliases")
    @classmethod
    def valid_aliases(cls, value):
        if len(value) > 100 or any(not k.strip() or not v.strip() or len(k) > 80 or len(v) > 80 for k, v in value.items()):
            raise ValueError("Use at most 100 nonempty technology aliases under 80 characters")
        return {k.strip().casefold(): v.strip().casefold() for k, v in value.items()}

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo
        ZoneInfo(value)
        return value

    @field_validator("operating_start", "operating_end")
    @classmethod
    def valid_time(cls, value):
        datetime.strptime(value, "%H:%M")
        return value


class Config(Model):
    profile: Profile = Field(default_factory=Profile)
    searches: list[Search] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    revision: int = 0


class Resume(Model):
    id: str = Field(default_factory=uid)
    label: str
    filename: str = ""
    text: str = ""
    path: str = ""
    skills: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)
    confirmed: bool = False
    identity_facts: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    conflict_acknowledged: bool = False
    conflict_fingerprint: str = ""
    sha256: str = ""
    created_at: str = Field(default_factory=now)


class Job(Model):
    id: str = Field(default_factory=uid)
    linkedin_id: str
    title: str
    company: str
    description: str = ""
    url: str = ""
    application_url: str = ""
    company_website: str = ""
    company_linkedin_url: str = ""
    country: str = ""
    city: str = ""
    location: str = ""
    workplace: str = ""
    seniority: str = ""
    employment_type: str = ""
    posted_days: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""
    sponsorship_available: bool | None = None
    easy_apply: bool = False
    verified_poster: bool = False
    poster_name: str = ""
    poster_url: str = ""
    poster_company: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    adapter_name: str = ""
    adapter_version: str = ""
    retry_at: str = ""
    last_error_class: str = ""
    requisition: str = ""
    search_id: str = ""
    status: str = "discovered"
    score: int = 0
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    resume_id: str = ""
    resume_hash: str = ""
    answers: dict[str, str] = Field(default_factory=dict)
    answer_sources: dict[str, str] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    field_details: list[dict] = Field(default_factory=list)
    error: str = ""
    attempts: int = 0
    submission_started: bool = False
    submission_at: str = ""
    revision: int = 0
    approved_revision: int = -1
    possible_duplicate: str = ""
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class MatchResult(Model):
    eligible: bool
    score: int
    reasons: list[str]
    needs_review: bool = False


class ResumeChoice(Model):
    resume: Resume
    score: int
    reasons: list[str]


class Answer(Model):
    value: str
    source: str


class ApplyResult(Model):
    state: Literal["submitted", "needs_input", "failed", "submission_uncertain", "blocked"]
    reason: str = ""
    questions: list[str] = Field(default_factory=list)
    field_details: list[dict] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    retryable: bool = False
    portal_issue: bool = False


class Verification(Model):
    state: Literal["eligible", "blocked", "unresolved"]
    evidence: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=list)

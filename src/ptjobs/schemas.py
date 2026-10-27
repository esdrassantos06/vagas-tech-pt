"""Pydantic schemas for API input and output."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ptjobs.models import INT32_MAX
from ptjobs.normalize import canonical_tag
from ptjobs.notify import validate_discord_webhook

MAX_ITEMS = 20


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL bytes are not allowed")
    return value


FilterTerm = Annotated[
    str, StringConstraints(min_length=1, max_length=50), AfterValidator(_reject_nul)
]
TagTerm = Annotated[FilterTerm, AfterValidator(canonical_tag)]
Target = Annotated[str, StringConstraints(max_length=500), AfterValidator(_reject_nul)]
Token = Annotated[str, StringConstraints(max_length=128), AfterValidator(_reject_nul)]
WorkMode = Literal["onsite", "hybrid", "remote", "unknown"]
Source = Literal["linkedin", "itjobs", "net_empregos"]


class ErrorDetail(BaseModel):
    """Error envelope payload."""

    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    """Standard error response wrapper."""

    error: ErrorDetail


class JobSummary(BaseModel):
    """A job as listed, deduplicated across sources."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    url: str
    title: str
    company: str
    city: str | None
    work_mode: str
    salary_min: int | None
    salary_max: int | None
    tags: list[str]
    published_at: datetime | None
    first_seen_at: datetime


class JobAlsoOn(BaseModel):
    """A sibling posting of the same job on another source."""

    model_config = ConfigDict(from_attributes=True)

    source: str
    url: str


class JobDetail(JobSummary):
    """A job with its description and sibling postings on other sources."""

    description: str | None
    last_seen_at: datetime
    also_on: list[JobAlsoOn] = []


class JobPage(BaseModel):
    """A page of job summaries."""

    items: list[JobSummary]
    total: int
    page: int
    size: int


class SourceStatus(BaseModel):
    """The most recent scrape run for a source."""

    model_config = ConfigDict(from_attributes=True)

    source: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    jobs_found: int
    jobs_new: int
    error: str | None


class SubscriptionFilters(BaseModel):
    """Alert filters; empty lists and null mean no constraint on that dimension."""

    keywords: list[FilterTerm] = Field(default_factory=list, max_length=MAX_ITEMS)
    cities: list[FilterTerm] = Field(default_factory=list, max_length=MAX_ITEMS)
    work_modes: list[WorkMode] = Field(default_factory=list, max_length=4)
    tags: list[TagTerm] = Field(default_factory=list, max_length=MAX_ITEMS)
    min_salary: int | None = Field(default=None, ge=0, le=INT32_MAX)

    @field_validator("keywords", "cities", "tags")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class SubscriptionCreate(BaseModel):
    """Request body to create a subscription."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"channel": "telegram", "filters": {"tags": ["python"]}}]
        }
    )

    channel: Literal["email", "discord", "telegram"]
    target: Target | None = None
    filters: SubscriptionFilters = Field(default_factory=SubscriptionFilters)

    @model_validator(mode="after")
    def _validate_target(self) -> SubscriptionCreate:
        if self.channel == "email":
            try:
                validate_email(self.target or "", check_deliverability=False)
            except EmailNotValidError as exc:
                raise ValueError("invalid email address") from exc
        elif self.channel == "discord" and (
            not self.target or not validate_discord_webhook(self.target)
        ):
            raise ValueError("invalid Discord webhook URL")
        return self


class SubscriptionUpdate(BaseModel):
    """Request body to update a subscription's filters.

    The target cannot be changed; create a new subscription to use a different
    destination, so delivery always follows a fresh opt-in.
    """

    filters: SubscriptionFilters | None = None


class UnsubscribeRequest(BaseModel):
    """Request body to unsubscribe using a manage token."""

    token: Token


class RecoverRequest(BaseModel):
    """Request body to email a new manage token to an email alert."""

    email: Target = Field(examples=["dev@example.com"])


class SubscriptionCreated(BaseModel):
    """Response to a successful subscription creation."""

    id: UUID
    manage_token: str
    deep_link: str | None = None


class SubscriptionOut(BaseModel):
    """A subscription as returned to its owner (no secret hashes)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    target: str | None
    filters: dict[str, object]
    verified_at: datetime | None
    created_at: datetime

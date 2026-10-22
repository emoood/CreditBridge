from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MatchRecommendation = Literal["POTENTIAL_MATCH", "NO_DEFENSIBLE_MATCH", "NEEDS_MORE_EVIDENCE"]
ReviewVerdict = Literal["SUPPORTED_FOR_HUMAN_REVIEW", "CHALLENGED", "INSUFFICIENT_EVIDENCE"]


class CourseExtraction(BaseModel):
    code: str
    title: str = ""
    title_ar: str = ""
    credits: Decimal | None = None
    grade: str | None = None
    term: str = ""
    page: int = 1
    evidence_ref: str = ""  # excerpt id
    uncertain_fields: list[str] = Field(default_factory=list)


class Excerpt(BaseModel):
    id: str
    document_id: str
    page: int
    text: str
    excerpt_hash: str


# Agent output contracts. Deliberately NO approval field anywhere (strict: unknown keys rejected).
class SupportedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_excerpt_id: str
    target_excerpt_id: str
    explanation: str


class MatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation: MatchRecommendation
    target_course_id: str | None = None
    supported_outcomes: list[SupportedOutcome] = Field(default_factory=list)
    missing_or_weaker_outcomes: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class Objection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    excerpt_ids: list[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: ReviewVerdict
    objections: list[Objection] = Field(default_factory=list)

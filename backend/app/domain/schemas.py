from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# POTENTIAL_GENERAL_ELECTIVE is only a flag for the admissions officer: no specific course fits, the AI does not
# judge elective eligibility. The human decides.
MatchRecommendation = Literal["POTENTIAL_MATCH", "POTENTIAL_GENERAL_ELECTIVE", "NO_DEFENSIBLE_MATCH", "NEEDS_MORE_EVIDENCE"]
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


def _reject_approval(x: Any) -> None:
    if isinstance(x, dict):
        for k, v in x.items():
            if "approv" in str(k).lower():
                raise ValueError("agents cannot approve credit")
            _reject_approval(v)
    elif isinstance(x, list):
        for v in x:
            _reject_approval(v)


class AgentOutput(BaseModel):
    """Agent output contract. Harmless extra keys are ignored (small models add some), but any approval-like key is
    rejected outright, and no approval field exists on any agent output."""
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _no_approval_keys(cls, data: Any) -> Any:
        _reject_approval(data)
        return data


class SupportedOutcome(AgentOutput):
    source_excerpt_id: str
    target_excerpt_id: str
    explanation: str


class MatchOutput(AgentOutput):
    recommendation: MatchRecommendation
    target_course_id: str | None = None
    supported_outcomes: list[SupportedOutcome] = Field(default_factory=list)
    missing_or_weaker_outcomes: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    evidence_strength: Literal["STRONG", "PARTIAL", "WEAK"] = "WEAK"  # how much of the target the evidence covers


class Objection(AgentOutput):
    text: str
    excerpt_ids: list[str] = Field(default_factory=list)


class ReviewOutput(AgentOutput):
    verdict: ReviewVerdict
    objections: list[Objection] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # minor remarks that are not objections

"""Pydantic schema definitions for ScholarLens student profiles and matching engine.

Supports explainable, deterministic matching grounded in official scholarship criteria.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EducationLevel(str, Enum):
    """Standard education levels recognized in Indian scholarship schemes."""

    CLASS_10 = "class_10"
    CLASS_12 = "class_12"
    DIPLOMA = "diploma"
    UNDERGRADUATE = "undergraduate"
    POSTGRADUATE = "postgraduate"
    DOCTORATE = "doctorate"
    OTHER = "other"


class SocialCategory(str, Enum):
    """Social categories recognized in Indian affirmative action and scholarship schemes."""

    GENERAL = "general"
    OBC = "obc"
    SC = "sc"
    ST = "st"
    EWS = "ews"
    MINORITY = "minority"
    OTHER = "other"


class Gender(str, Enum):
    """Gender categories relevant to women/girl-specific scholarship schemes."""

    FEMALE = "female"
    MALE = "male"
    OTHER = "other"


class CriterionStatus(str, Enum):
    """Status of an individual eligibility criterion evaluation."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class CriterionType(str, Enum):
    """Type of eligibility constraint being evaluated."""

    INCOME = "income"
    EDUCATION_LEVEL = "education_level"
    COURSE = "course"
    ACADEMIC_MARKS = "academic_marks"
    CATEGORY = "category"
    GENDER = "gender"
    STATE = "state"
    DISABILITY = "disability"
    GENERAL = "general"


class MatchOutcome(str, Enum):
    """Overall eligibility match outcome for a scholarship scheme."""

    ELIGIBLE = "eligible"
    POTENTIALLY_ELIGIBLE = "potentially_eligible"
    NOT_ELIGIBLE = "not_eligible"


class StudentProfile(BaseModel):
    """Student profile input for scholarship matching.

    Sensitive attributes are completely optional and only evaluated
    when explicitly required by a scholarship's official criteria.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "education_level": "undergraduate",
                "course": "B.Tech Computer Science",
                "state_of_residence": "Maharashtra",
                "annual_family_income": 450000.0,
                "academic_percentage": 85.5,
                "category": "general",
                "gender": "female",
                "is_differently_abled": False,
            }
        },
    )

    education_level: Optional[EducationLevel] = Field(
        default=None,
        description="Current or target education level (e.g. undergraduate, class_12).",
    )
    course: Optional[str] = Field(
        default=None,
        description="Course, degree, or stream (e.g. B.Tech, MBBS, B.Sc).",
        max_length=200,
    )
    state_of_residence: Optional[str] = Field(
        default=None,
        description="State or Union Territory of domicile/residence in India.",
        max_length=100,
    )
    annual_family_income: Optional[float] = Field(
        default=None,
        ge=0,
        description="Gross annual parental/family income in INR.",
    )
    academic_percentage: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Academic score or percentile percentage in previous qualifying exam (0-100).",
    )
    category: Optional[SocialCategory] = Field(
        default=None,
        description="Social or reservation category (e.g. general, obc, sc, st, ews).",
    )
    gender: Optional[Gender] = Field(
        default=None,
        description="Gender of the student (e.g. female, male, other).",
    )
    is_differently_abled: Optional[bool] = Field(
        default=None,
        description="Whether the student is a person with benchmark disability (PwD).",
    )

    @field_validator("course", "state_of_residence", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class CriterionEvaluation(BaseModel):
    """Detailed evaluation result for an individual eligibility criterion."""

    criterion_type: CriterionType = Field(..., description="Type of constraint evaluated.")
    raw_criterion: str = Field(..., description="Original criterion statement from source.")
    status: CriterionStatus = Field(..., description="Evaluation outcome: PASS, FAIL, or UNKNOWN.")
    reason: str = Field(..., description="Human-readable explanation of why criterion passed, failed, or is unknown.")
    evidence_quote: Optional[str] = Field(
        default=None,
        description="Verbatim portal quote supporting this criterion, if available.",
    )


class ScholarshipMatchResult(BaseModel):
    """Explainable matching result for a single scholarship scheme."""

    scholarship_id: Optional[int] = Field(default=None, description="Database record ID.")
    scheme_name: str = Field(..., description="Official scheme name.")
    government_authority: str = Field(..., description="Governing authority.")
    verification_status: str = Field(..., description="Official verification status.")
    is_provisional: bool = Field(
        default=False,
        description="True if scholarship is unverified, meaning eligibility is provisional and unconfirmed.",
    )
    match_outcome: MatchOutcome = Field(..., description="ELIGIBLE, POTENTIALLY_ELIGIBLE, or NOT_ELIGIBLE.")
    passed_criteria: List[CriterionEvaluation] = Field(default_factory=list)
    failed_criteria: List[CriterionEvaluation] = Field(default_factory=list)
    unknown_criteria: List[CriterionEvaluation] = Field(default_factory=list)
    explanation: str = Field(..., description="Concise summary explanation of matching outcome.")
    official_source_url: str = Field(..., description="Official portal URL.")
    scholarship_amount: Optional[str] = Field(default=None, description="Amount if available.")
    application_deadline: Optional[str] = Field(default=None, description="Deadline if established.")
    evidence_references: Dict[str, str] = Field(
        default_factory=dict,
        description="Field-level verbatim citations backing this scholarship.",
    )
    application_status: Optional[str] = Field(
        default=None,
        description="Application cycle status (e.g. open, closed, expired, unknown).",
    )
    scheme_year: Optional[str] = Field(
        default=None,
        description="Academic year or cycle for the scheme.",
    )


class MatchingResponse(BaseModel):
    """Response containing explainable matching results across evaluated scholarships."""

    total_evaluated: int = Field(..., description="Total scholarships evaluated.")
    eligible_count: int = Field(..., description="Confirmed eligible scholarships.")
    potentially_eligible_count: int = Field(..., description="Potentially eligible scholarships (missing info).")
    not_eligible_count: int = Field(..., description="Scholarships where at least one mandatory criterion failed.")
    unverified_count: int = Field(..., description="Scholarships with unverified status (provisional).")
    results: List[ScholarshipMatchResult] = Field(default_factory=list)

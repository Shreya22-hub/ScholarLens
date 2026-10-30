"""ScholarLens Pydantic Schemas Package."""

from backend.app.schemas.matching import (
    CriterionEvaluation,
    CriterionStatus,
    CriterionType,
    EducationLevel,
    Gender,
    MatchingResponse,
    MatchOutcome,
    ScholarshipMatchResult,
    SocialCategory,
    StudentProfile,
)
from backend.app.schemas.scholarship import (
    IngestionRunRecord,
    ScholarshipRecord,
    ScholarshipSource,
    SourceType,
    VerificationStatus,
)

__all__ = [
    "CriterionEvaluation",
    "CriterionStatus",
    "CriterionType",
    "EducationLevel",
    "Gender",
    "IngestionRunRecord",
    "MatchingResponse",
    "MatchOutcome",
    "ScholarshipMatchResult",
    "ScholarshipRecord",
    "ScholarshipSource",
    "SocialCategory",
    "SourceType",
    "StudentProfile",
    "VerificationStatus",
]

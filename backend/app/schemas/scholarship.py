"""Pydantic schema definitions for ScholarLens scholarship records, sources, and ingestion.

All scholarship data must be strictly grounded in official government portal citations.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class VerificationStatus(str, Enum):
    """Enumeration of possible verification states for scholarship data."""

    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    FLAGGED = "flagged"
    EXPIRED = "expired"


class ApplicationStatus(str, Enum):
    """Enumeration of possible application cycle states."""

    OPEN = "open"
    DEADLINE_PUBLISHED_STATUS_UNKNOWN = "deadline_published_status_unknown"
    CLOSED = "closed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """Type of source document."""

    HTML = "html"
    PDF = "pdf"


class ScholarshipRecord(BaseModel):
    """Pydantic model representing an Indian government scholarship scheme record.

    Ensures data integrity and strict grounding in official government sources.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "scheme_name": "Central Sector Scheme of Scholarship for College and University Students",
                "government_authority": "Department of Higher Education, Ministry of Education, Government of India",
                "description": "Financial assistance to meritorious students from low-income families to meet day-to-day expenses while pursuing higher studies.",
                "eligibility_criteria": [
                    "Students above 80th percentile of successful candidates in relevant stream in Class XII",
                    "Pursuing regular degree course in recognized institution",
                    "Gross parental/family income up to Rs. 4.5 lakh per annum"
                ],
                "scholarship_amount": "Rs. 12,000 per annum at graduation level for first 3 years; Rs. 20,000 per annum at post-graduation level",
                "application_deadline": "31st October 2026 (indicative, subject to official NSP portal circular)",
                "required_documents": [
                    "Class 12th Marks Sheet",
                    "Income Certificate issued by competent authority",
                    "Aadhaar Card or Aadhaar Enrolment Slip",
                    "Bank Account details seeded with Aadhaar",
                    "College/Institute Verification Certificate"
                ],
                "official_source_url": "https://scholarships.gov.in/",
                "supporting_quotations": [
                    "The scholarships are awarded on the basis of results of Senior Secondary Examination (Class XII)."
                ],
                "last_verified_timestamp": "2026-10-30T12:00:00Z",
                "verification_status": "unverified"
            }
        }
    )

    id: Optional[int] = Field(default=None, description="Database record ID if stored")
    scheme_name: str = Field(
        ...,
        description="Official name of the scholarship scheme as published by the governing authority.",
        min_length=3,
        max_length=500,
    )
    government_authority: str = Field(
        ...,
        description="Government ministry, department, board, or nodal agency offering the scheme.",
        min_length=2,
        max_length=300,
    )
    description: str = Field(
        ...,
        description="Official description of the scholarship scheme, objectives, and scope.",
        min_length=10,
    )
    eligibility_criteria: List[str] = Field(
        default_factory=list,
        description="List of specific eligibility criteria (e.g. academic marks, family income cap, category, course level).",
    )
    scholarship_amount: Optional[str] = Field(
        ...,
        description="Financial assistance amount, allowance, fee reimbursement, or stipend provided under the scheme (or None if unavailable/ambiguous).",
    )
    application_deadline: Optional[str] = Field(
        default=None,
        description="Normalized ISO date (YYYY-MM-DD) if clearly established, or None if ambiguous/pending.",
    )
    application_deadline_raw: Optional[str] = Field(
        default=None,
        description="Original unparsed deadline text extracted from official source as evidence.",
    )
    required_documents: List[str] = Field(
        default_factory=list,
        description="List of mandatory documents, certificates, and proofs required for submission.",
    )
    official_source_url: HttpUrl = Field(
        ...,
        description="Direct URL to the official government portal, circular, or scheme guideline page.",
    )
    supporting_quotations: List[str] = Field(
        default_factory=list,
        description="Verbatim text snippets and quotations directly extracted from the official source to ground claims.",
    )
    field_evidence: Dict[str, str] = Field(
        default_factory=dict,
        description="Field-level verbatim citations extracted from source document.",
    )
    last_verified_timestamp: Optional[datetime] = Field(
        default=None,
        alias="last_verified",
        description="UTC timestamp indicating when this record was last verified against the official source.",
    )
    verification_status: VerificationStatus = Field(
        default=VerificationStatus.UNVERIFIED,
        description="Verification state of this record (unverified, pending, verified, flagged, expired).",
    )
    verification_notes: Optional[str] = Field(
        default=None,
        description="Transparent explanation of verification status or why fields remain unverified.",
    )
    scheme_year: Optional[str] = Field(
        default=None,
        description="Academic year or cycle for the scheme (e.g. 2026-27).",
    )
    application_status: Optional[str] = Field(
        default=ApplicationStatus.UNKNOWN.value,
        description="Current application status: open, deadline_published_status_unknown, closed, expired, or unknown.",
    )
    guideline_url: Optional[str] = Field(
        default=None,
        description="Direct URL to official guidelines or notification document if followed.",
    )

    @field_validator("scheme_name", "government_authority", "description")
    @classmethod
    def check_non_empty_strings(cls, v: str) -> str:
        """Validate that text fields are not just empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("Field must contain non-whitespace text.")
        return v.strip()

    @field_validator("scholarship_amount")
    @classmethod
    def check_non_empty_amount(cls, v: Optional[str]) -> Optional[str]:
        """Validate that scholarship_amount is not whitespace if provided."""
        if v is None:
            return None
        if not v or not v.strip():
            raise ValueError("Field must contain non-whitespace text.")
        return v.strip()

    @field_validator("eligibility_criteria", "required_documents", "supporting_quotations")
    @classmethod
    def clean_string_lists(cls, v: List[str]) -> List[str]:
        """Strip whitespace and filter out blank list entries."""
        if not v:
            return []
        cleaned = [item.strip() for item in v if item and item.strip()]
        return cleaned


class ScholarshipSource(BaseModel):
    """Configuration and status model for official scholarship portal sources."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    id: str = Field(..., description="Unique slug or identifier for the portal source")
    name: str = Field(..., description="Human-readable title of the government portal")
    authority: str = Field(..., description="Operating ministry or council")
    url: HttpUrl = Field(..., description="Base or scheme-listing URL")
    source_type: SourceType = Field(
        default=SourceType.HTML,
        description="Document type of the source: HTML web page or PDF document",
    )
    category: Optional[str] = Field(default="Central Government Portal", description="Portal category")
    is_active: bool = Field(
        default=True,
        alias="enabled",
        description="Whether this source is currently enabled for ingestion",
    )
    verified: bool = Field(default=False, description="Whether this source URL has been actively verified")
    verification_status: VerificationStatus = Field(default=VerificationStatus.UNVERIFIED)
    last_verified_timestamp: Optional[datetime] = Field(default=None)
    last_checked_timestamp: Optional[datetime] = Field(
        default=None,
        alias="last_checked",
        description="Timestamp of the most recent fetch attempt",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Error message from the last ingestion or fetch attempt, if any",
    )
    notes: Optional[str] = Field(default=None, description="Operational notes or guidelines")


class IngestionRunRecord(BaseModel):
    """Model recording an individual ingestion or fetch execution."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = None
    source_id: str
    url: str
    fetched_at: datetime
    status_code: Optional[int] = None
    success: bool
    content_type: Optional[str] = None
    records_extracted: int = 0
    error_message: Optional[str] = None

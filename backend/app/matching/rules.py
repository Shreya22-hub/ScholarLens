"""Deterministic eligibility rules and constraint extractors for ScholarLens.

Inspects official scholarship criteria text and evaluates against student profile attributes.
Produces explainable PASS, FAIL, or UNKNOWN outcomes with clear justifications.
"""

from enum import Enum
import re
from typing import List, Optional, Tuple

from backend.app.schemas.matching import (
    CriterionEvaluation,
    CriterionStatus,
    CriterionType,
    EducationLevel,
    Gender,
    SocialCategory,
    StudentProfile,
)


def extract_income_ceiling(text: str) -> Optional[float]:
    """Extract maximum family income ceiling in INR from criterion text.

    Supports formats like:
    - Rs. 8 lakh per annum -> 800,000.0
    - Rs. 4.5 lakh -> 450,000.0
    - Rs. 3,50,000 -> 350,000.0
    - <= 800000 -> 800,000.0
    """
    text_lower = text.lower()
    
    # Must be related to income or parental earnings
    if not any(k in text_lower for k in ["income", "parental", "family income", "gross family"]):
        return None

    # Pattern 1: Rs. X [lakh|crore]
    pat_lakh = re.search(
        r"(?:rs\.?|₹|inr)?\s*([\d\.]+)\s*(lakh|lakhs|lac|lacs|crore|crores)\b",
        text_lower,
    )
    if pat_lakh:
        try:
            val = float(pat_lakh.group(1))
            unit = pat_lakh.group(2)
            multiplier = 10000000.0 if "crore" in unit else 100000.0
            return val * multiplier
        except (ValueError, TypeError):
            pass

    # Pattern 2: Absolute figure e.g. Rs. 3,50,000 or Rs. 800000
    pat_num = re.search(
        r"(?:rs\.?|₹|inr)?\s*([\d,]{4,10})\b(?:\s*(?:per\s*annum|p\.?a\.?|per\s*year|yearly))?",
        text_lower,
    )
    if pat_num:
        clean_num = pat_num.group(1).replace(",", "")
        try:
            val = float(clean_num)
            # Sanity check: annual income ceilings are typically >= 50,000
            if val >= 50000.0:
                return val
        except (ValueError, TypeError):
            pass

    return None


def extract_education_level(text: str) -> Optional[EducationLevel]:
    """Detect required current education level from criterion or scheme text."""
    t = text.lower()

    if any(k in t for k in ["ph.d", "phd", "doctorate", "doctoral", "research scholar"]):
        return EducationLevel.DOCTORATE
    if any(k in t for k in ["post-graduat", "postgraduat", "master", "m.tech", "m.sc", "m.a", "mba"]):
        return EducationLevel.POSTGRADUATE
    if any(
        k in t
        for k in [
            "degree programme",
            "degree course",
            "undergraduat",
            "bachelor",
            "b.tech",
            "b.e",
            "b.sc",
            "b.a",
            "b.com",
            "mbbs",
            "graduation level",
            "1st year degree",
            "first year degree",
            "college and university students",
        ]
    ):
        return EducationLevel.UNDERGRADUATE
    if any(k in t for k in ["diploma", "polytechnic"]):
        return EducationLevel.DIPLOMA

    # Check if Class 12 / 10 is mentioned as a previous qualifying exam/marks rather than current study
    is_prior_exam = bool(
        re.search(
            r"(?:marks|percentile|result|passed|score|scored|secured|above)\s*(?:in|of)?\s*(?:class\s*(?:10|12|x|xii))",
            t,
        )
        or re.search(r"(?:class\s*(?:10|12|x|xii))\s*(?:marks|percentile|result|passed)", t)
    )

    if not is_prior_exam:
        if any(
            k in t
            for k in [
                "class 12",
                "class xii",
                "senior secondary",
                "higher secondary",
                "intermediate",
                "+2",
                "plus two",
            ]
        ):
            return EducationLevel.CLASS_12
        if any(k in t for k in ["class 10", "class x", "matriculation", "secondary school", "class ix", "class 9"]):
            return EducationLevel.CLASS_10

    return None


def extract_academic_percentage(text: str) -> Optional[float]:
    """Extract minimum academic percentage or percentile from text."""
    t = text.lower()
    
    # Must have marks/percent keywords
    if not any(k in t for k in ["%", "percent", "marks", "percentile"]):
        return None

    # Pattern: minimum 60% or above 80th percentile
    m = re.search(
        r"(?:minimum|at\s*least|above|>=|>)\s*(\d{1,2}(?:\.\d+)?)(?:st|nd|rd|th)?\s*(?:%|percent|percentile|marks)",
        t,
    )
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    m2 = re.search(r"(\d{1,2}(?:\.\d+)?)(?:st|nd|rd|th)?\s*(?:%|percent|percentile)\s*(?:or\s*above|minimum|marks)", t)
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            pass

    return None


def extract_target_gender(text: str) -> Optional[Gender]:
    """Detect if the scheme or criterion is gender-specific (e.g. girl students)."""
    t = text.lower()
    if any(k in t for k in ["girl student", "girl students", "women", "female students", "daughters", "single girl child"]):
        return Gender.FEMALE
    return None


def extract_target_category(text: str) -> Optional[SocialCategory]:
    """Detect targeted reservation or social category."""
    t = text.lower()
    # Check word boundaries for short acronyms
    if re.search(r"\b(?:st|scheduled\s*tribe|scheduled\s*tribes|tribal)\b", t):
        return SocialCategory.ST
    if re.search(r"\b(?:sc|scheduled\s*caste|scheduled\s*castes)\b", t):
        return SocialCategory.SC
    if re.search(r"\b(?:obc|other\s*backward)\b", t):
        return SocialCategory.OBC
    if re.search(r"\b(?:ews|economically\s*weaker)\b", t):
        return SocialCategory.EWS
    if re.search(r"\b(?:minority|minorities|muslim|christian|sikh|buddhist|jain|parsi)\b", t):
        return SocialCategory.MINORITY
    return None


def extract_disability_requirement(text: str) -> bool:
    """Detect if criterion requires person with benchmark disability."""
    t = text.lower()
    return any(
        k in t
        for k in [
            "differently abled",
            "disabilit",
            "pwd",
            "handicap",
            "divyang",
            "benchmark disability",
        ]
    )


def evaluate_criterion(
    criterion_text: str,
    profile: StudentProfile,
    evidence_quote: Optional[str] = None,
    is_scheme_name: bool = False,
) -> List[CriterionEvaluation]:
    """Evaluate a single criterion text string against a student profile.

    A single sentence might contain multiple constraints (e.g. education level + income).
    Returns a list of CriterionEvaluation for each detected constraint.
    """
    evaluations: List[CriterionEvaluation] = []
    text_lower = criterion_text.lower()

    # 1. Income Ceiling Constraint
    ceiling = extract_income_ceiling(criterion_text)
    if ceiling is not None:
        if profile.annual_family_income is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.INCOME,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=f"Criterion requires family income <= ₹{ceiling:,.0f}, but annual family income was not provided in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.annual_family_income <= ceiling:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.INCOME,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Family income ₹{profile.annual_family_income:,.0f} satisfies ceiling of ₹{ceiling:,.0f}.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.INCOME,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Family income ₹{profile.annual_family_income:,.0f} exceeds maximum ceiling of ₹{ceiling:,.0f}.",
                    evidence_quote=evidence_quote,
                )
            )

    # 2. Education Level Constraint
    req_level = extract_education_level(criterion_text)
    if req_level is not None:
        if profile.education_level is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=f"Criterion requires education level '{req_level.value}', but education level was not provided in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.education_level == req_level:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Education level '{profile.education_level.value}' matches required level '{req_level.value}'.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Education level '{profile.education_level.value}' does not match required level '{req_level.value}'.",
                    evidence_quote=evidence_quote,
                )
            )

    # 3. Academic Marks / Percentage Constraint
    min_marks = extract_academic_percentage(criterion_text)
    if min_marks is not None:
        if profile.academic_percentage is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=f"Criterion requires minimum academic marks of {min_marks}%, but percentage was not provided in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.academic_percentage >= min_marks:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Academic percentage {profile.academic_percentage}% satisfies minimum requirement of {min_marks}%.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Academic percentage {profile.academic_percentage}% is below required minimum of {min_marks}%.",
                    evidence_quote=evidence_quote,
                )
            )

    # 4. Gender Constraint
    target_gender = extract_target_gender(criterion_text)
    if target_gender is not None:
        if profile.gender is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENDER,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason="Scheme is specifically reserved for girl/women students, but gender was not specified in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.gender == target_gender:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENDER,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Student gender '{profile.gender.value}' meets requirement for women/girl students.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENDER,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Scheme is specifically reserved for {target_gender.value} students; student profile indicates {profile.gender.value}.",
                    evidence_quote=evidence_quote,
                )
            )

    # 5. Social Category Constraint
    target_cat = extract_target_category(criterion_text)
    if target_cat is not None:
        if profile.category is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.CATEGORY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=f"Scheme requires category '{target_cat.value.upper()}', but category was not provided in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.category == target_cat:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.CATEGORY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Student category '{profile.category.value.upper()}' matches required category '{target_cat.value.upper()}'.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.CATEGORY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Student category '{profile.category.value.upper()}' does not match required category '{target_cat.value.upper()}'.",
                    evidence_quote=evidence_quote,
                )
            )

    # 6. Disability Constraint
    if extract_disability_requirement(criterion_text):
        if profile.is_differently_abled is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.DISABILITY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason="Scheme is reserved for differently-abled students (PwD), but disability status was not specified.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.is_differently_abled:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.DISABILITY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason="Student profile confirms benchmark disability status.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.DISABILITY,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason="Scheme is exclusively for differently-abled students (PwD); student profile indicates not differently-abled.",
                    evidence_quote=evidence_quote,
                )
            )

    # 7. Fallback for General / Unparsed substantive criteria
    # If no specific constraint was detected, record it as a general condition (for criteria, not scheme names).
    if not is_scheme_name and not evaluations and len(criterion_text.strip()) > 10:
        # Check if criterion requires Indian domicile or citizen
        if any(k in text_lower for k in ["indian citizen", "citizen of india"]):
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENERAL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason="Criterion requires Indian citizenship; confirmed applicable to domestic portal applicants.",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENERAL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason="General condition specified by scheme; requires manual verification against official guidelines.",
                    evidence_quote=evidence_quote,
                )
            )

    return evaluations

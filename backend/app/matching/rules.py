"""Deterministic eligibility rules and constraint extractors for ScholarLens.

Inspects official scholarship criteria text and evaluates against student profile attributes.
Produces explainable PASS, FAIL, or UNKNOWN outcomes with clear justifications.
"""

from dataclasses import dataclass
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


def extract_education_levels(text: str) -> List[EducationLevel]:
    """Detect required current education level(s) from criterion or scheme text.

    Supports compound criteria (e.g. 'Degree/Diploma') by returning multiple accepted levels.
    """
    t = text.lower()

    # 1. Compound level checks
    # Check degree/diploma combinations (e.g. "Degree/Diploma level", "Degree or Diploma")
    if re.search(r"\b(?:degree\s*(?:/|\bor\b|\band\b)\s*diploma|diploma\s*(?:/|\bor\b|\band\b)\s*degree)\b", t):
        return [EducationLevel.UNDERGRADUATE, EducationLevel.DIPLOMA]

    # Check diploma/ug/pg combinations
    if re.search(r"\bdiploma\s*/\s*(?:degree|ug)\s*/\s*pg\b", t):
        return [EducationLevel.DIPLOMA, EducationLevel.UNDERGRADUATE, EducationLevel.POSTGRADUATE]

    # Check ug/pg combinations (e.g. "UG/PG level", "undergraduate or postgraduate")
    is_qualifying_marks = bool(
        re.search(
            r"(?:marks|percentile|result|passed|score|secured|minimum|above)\s*(?:in|of)?\s*(?:pg/ug|ug/pg|degree)",
            t,
        )
    )
    if not is_qualifying_marks and re.search(
        r"\b(?:ug\s*(?:/|\bor\b)\s*pg|undergraduate\s*(?:/|\bor\b)\s*postgraduate)\b",
        t,
    ):
        return [EducationLevel.UNDERGRADUATE, EducationLevel.POSTGRADUATE]

    # 2. Single level checks

    if any(k in t for k in ["ph.d", "phd", "doctorate", "doctoral", "research scholar"]):
        return [EducationLevel.DOCTORATE]
    if any(k in t for k in ["post-graduat", "postgraduat", "master", "m.tech", "m.sc", "m.a", "mba"]):
        return [EducationLevel.POSTGRADUATE]
    if any(
        k in t
        for k in [
            "degree programme",
            "degree course",
            "degree level",
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
    ) or (re.search(r"\bdegree\b", t) and not is_qualifying_marks and not any(k in t for k in ["diploma"])):
        return [EducationLevel.UNDERGRADUATE]
    if any(k in t for k in ["diploma", "polytechnic"]):
        return [EducationLevel.DIPLOMA]

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
            return [EducationLevel.CLASS_12]
        if any(k in t for k in ["class 10", "class x", "matriculation", "secondary school", "class ix", "class 9"]):
            return [EducationLevel.CLASS_10]

    return []


def extract_education_level(text: str) -> Optional[EducationLevel]:
    """Detect required current education level from criterion or scheme text."""
    levels = extract_education_levels(text)
    return levels[0] if levels else None


@dataclass
class MarksRelaxation:
    """Encapsulates base academic cutoff and relaxed cutoff for affirmative action groups."""

    base_percentage: float
    relaxed_percentage: float
    groups_text: str
    raw_clause: str


def parse_academic_marks_relaxation(text: str) -> Optional[MarksRelaxation]:
    """Extract base cutoff percentage and alternative/relaxed cutoff for reserved groups.

    Recognizes formats like:
    - 'Minimum 60% marks in PG/UG (55% for SC/ST/OBC/Women/PwD)'
    - 'At least 60% marks (with a relaxation of 5% for SC/ST/Women)'
    - '60% aggregate marks; 55% for SC/ST/OBC candidates'
    - '60% marks, with 5% relaxation for SC/ST/PwD'
    """
    target_kws = [
        "sc",
        "st",
        "obc",
        "pwd",
        "women",
        "female",
        "differently abled",
        "handicap",
        "divyang",
        "reserved",
        "minority",
        "cat",
    ]

    # Pattern 1: Parenthesized cutoff percentage, e.g. (55% for SC/ST/OBC/Women/PwD)
    m1 = re.search(
        r"\(\s*(\d{1,2}(?:\.\d+)?)\s*%\s*(?:marks\s*)?(?:for|to|in\s*case\s*of)\s*([^\)]+)\)",
        text,
        re.IGNORECASE,
    )
    if m1:
        groups = m1.group(2).strip()
        if any(k in groups.lower() for k in target_kws):
            relaxed_val = float(m1.group(1))
            clause = m1.group(0)
            text_outside = text.replace(clause, " ")
            base_m = re.search(
                r"(?:minimum|at\s*least|above|>=|>)?\s*(\d{1,2}(?:\.\d+)?)\s*(?:%|percent|percentile|marks)",
                text_outside,
                re.IGNORECASE,
            )
            if base_m:
                base_val = float(base_m.group(1))
                return MarksRelaxation(
                    base_percentage=base_val,
                    relaxed_percentage=relaxed_val,
                    groups_text=groups,
                    raw_clause=clause,
                )

    # Pattern 2: Parenthesized relaxation amount/delta, e.g. (with a relaxation of 5% for SC/ST)
    m2 = re.search(
        r"\(\s*(?:with\s*)?(?:a\s*)?relaxation\s*of\s*(\d{1,2}(?:\.\d+)?)\s*%\s*(?:for|to|in\s*case\s*of)\s*([^\)]+)\)",
        text,
        re.IGNORECASE,
    )
    if not m2:
        m2 = re.search(
            r"\(\s*(\d{1,2}(?:\.\d+)?)\s*%\s*relaxation\s*(?:for|to|in\s*case\s*of)\s*([^\)]+)\)",
            text,
            re.IGNORECASE,
        )
    if m2:
        groups = m2.group(2).strip()
        if any(k in groups.lower() for k in target_kws):
            rel_delta = float(m2.group(1))
            clause = m2.group(0)
            text_outside = text.replace(clause, " ")
            base_m = re.search(
                r"(?:minimum|at\s*least|above|>=|>)?\s*(\d{1,2}(?:\.\d+)?)\s*(?:%|percent|percentile|marks)",
                text_outside,
                re.IGNORECASE,
            )
            if base_m:
                base_val = float(base_m.group(1))
                return MarksRelaxation(
                    base_percentage=base_val,
                    relaxed_percentage=max(0.0, base_val - rel_delta),
                    groups_text=groups,
                    raw_clause=clause,
                )

    # Pattern 3: Semicolon / comma-separated cutoff percentage, e.g. '; 55% for SC/ST/OBC'
    m3 = re.search(
        r"(?:;|,)\s*(\d{1,2}(?:\.\d+)?)\s*%\s*(?:marks\s*)?(?:for|to|in\s*case\s*of)\s*([A-Za-z0-9_/\s-]+)",
        text,
        re.IGNORECASE,
    )
    if m3:
        groups = m3.group(2).strip()
        if any(k in groups.lower() for k in target_kws):
            relaxed_val = float(m3.group(1))
            clause = m3.group(0)
            text_outside = text.replace(clause, " ")
            base_m = re.search(
                r"(?:minimum|at\s*least|above|>=|>)?\s*(\d{1,2}(?:\.\d+)?)\s*(?:%|percent|percentile|marks)",
                text_outside,
                re.IGNORECASE,
            )
            if base_m:
                base_val = float(base_m.group(1))
                return MarksRelaxation(
                    base_percentage=base_val,
                    relaxed_percentage=relaxed_val,
                    groups_text=groups,
                    raw_clause=clause,
                )

    # Pattern 4: Semicolon / comma-separated relaxation delta, e.g. ', with 5% relaxation for SC/ST'
    m4 = re.search(
        r"(?:;|,)\s*(?:with\s*)?(?:a\s*)?relaxation\s*of\s*(\d{1,2}(?:\.\d+)?)\s*%\s*(?:for|to|in\s*case\s*of)\s*([A-Za-z0-9_/\s-]+)",
        text,
        re.IGNORECASE,
    )
    if not m4:
        m4 = re.search(
            r"(?:;|,)\s*(?:with\s*)?(\d{1,2}(?:\.\d+)?)\s*%\s*relaxation\s*(?:for|to|in\s*case\s*of)\s*([A-Za-z0-9_/\s-]+)",
            text,
            re.IGNORECASE,
        )
    if m4:
        groups = m4.group(2).strip()
        if any(k in groups.lower() for k in target_kws):
            rel_delta = float(m4.group(1))
            clause = m4.group(0)
            text_outside = text.replace(clause, " ")
            base_m = re.search(
                r"(?:minimum|at\s*least|above|>=|>)?\s*(\d{1,2}(?:\.\d+)?)\s*(?:%|percent|percentile|marks)",
                text_outside,
                re.IGNORECASE,
            )
            if base_m:
                base_val = float(base_m.group(1))
                return MarksRelaxation(
                    base_percentage=base_val,
                    relaxed_percentage=max(0.0, base_val - rel_delta),
                    groups_text=groups,
                    raw_clause=clause,
                )

    return None


def strip_marks_relaxation_clause(text: str) -> str:
    """Remove marks relaxation clauses from criterion text.

    Ensures that category, gender, or disability keywords appearing within marks
    relaxation clauses are never extracted as mandatory restrictive criteria.
    """
    rel = parse_academic_marks_relaxation(text)
    if rel:
        return text.replace(rel.raw_clause, " ")

    clean = re.sub(
        r"\(\s*(?:\d{1,2}(?:\.\d+)?\s*%|(?:with\s*)?(?:a\s*)?relaxation\s*of\s*\d{1,2}(?:\.\d+)?\s*%|\d{1,2}(?:\.\d+)?\s*%\s*relaxation)[^\)]*\)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"(?:;|,)\s*(?:\d{1,2}(?:\.\d+)?\s*%\s*(?:marks\s*)?for|(?:with\s*)?(?:a\s*)?relaxation\s*of\s*\d{1,2}(?:\.\d+)?\s*%\s*for)[^;\.\n]*",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    return clean


def evaluate_relaxation_membership(
    profile: StudentProfile,
    groups_text: str,
) -> Tuple[bool, bool]:
    """Check whether a student qualifies for marks relaxation based on their profile.

    Returns:
        (is_covered, is_indeterminate):
        - is_covered=True if student profile positively proves membership in a relaxed group.
        - is_covered=False, is_indeterminate=False if all relevant dimensions are specified
          and the student is definitely NOT in any relaxed group.
        - is_covered=False, is_indeterminate=True if one or more relevant dimensions
          are missing (None) and the student might qualify if those attributes were provided.
    """
    gt = groups_text.lower()

    mentions_sc = bool(re.search(r"\b(?:sc|scheduled\s*caste|scheduled\s*castes)\b", gt))
    mentions_st = bool(re.search(r"\b(?:st|scheduled\s*tribe|scheduled\s*tribes|tribal)\b", gt))
    mentions_obc = bool(re.search(r"\b(?:obc|other\s*backward)\b", gt))
    mentions_ews = bool(re.search(r"\b(?:ews|economically\s*weaker)\b", gt))
    mentions_minority = bool(re.search(r"\b(?:minority|minorities)\b", gt))
    mentions_reserved = bool(re.search(r"\breserved\b", gt))
    mentions_cat = mentions_sc or mentions_st or mentions_obc or mentions_ews or mentions_minority or mentions_reserved

    mentions_female = bool(re.search(r"\b(?:women|woman|female|girl|girls)\b", gt))
    mentions_pwd = bool(re.search(r"\b(?:pwd|disabilit|differently\s*abled|handicap|divyang)\b", gt))

    # 1. Check for confirmed positive qualification
    if mentions_cat and profile.category:
        if mentions_sc and profile.category == SocialCategory.SC:
            return True, False
        if mentions_st and profile.category == SocialCategory.ST:
            return True, False
        if mentions_obc and profile.category == SocialCategory.OBC:
            return True, False
        if mentions_ews and profile.category == SocialCategory.EWS:
            return True, False
        if mentions_minority and profile.category == SocialCategory.MINORITY:
            return True, False
        if mentions_reserved and profile.category in (
            SocialCategory.SC,
            SocialCategory.ST,
            SocialCategory.OBC,
            SocialCategory.EWS,
            SocialCategory.MINORITY,
        ):
            return True, False

    if mentions_female and profile.gender == Gender.FEMALE:
        return True, False

    if mentions_pwd and profile.is_differently_abled is True:
        return True, False

    # 2. Check for missing/indeterminate information on any mentioned dimension
    cat_indet = mentions_cat and (profile.category is None)
    gender_indet = mentions_female and (profile.gender is None)
    pwd_indet = mentions_pwd and (profile.is_differently_abled is None)

    if cat_indet or gender_indet or pwd_indet:
        return False, True

    # 3. All relevant dimensions specified and none matched
    return False, False


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

    clean_criterion_text = strip_marks_relaxation_clause(criterion_text)

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

    # 2. Education Level Constraint (supports compound levels like Degree/Diploma)
    req_levels = extract_education_levels(criterion_text)
    if req_levels:
        if profile.education_level is None:
            if len(req_levels) == 1:
                reason = f"Criterion requires education level '{req_levels[0].value}', but education level was not provided in profile."
            else:
                levels_str = " or ".join(f"'{lvl.value}'" for lvl in req_levels)
                reason = f"Criterion requires education level {levels_str}, but education level was not provided in profile."
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=reason,
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.education_level in req_levels:
            if len(req_levels) == 1:
                reason = f"Education level '{profile.education_level.value}' matches required level '{req_levels[0].value}'."
            else:
                levels_str = " or ".join(f"'{lvl.value}'" for lvl in req_levels)
                reason = f"Education level '{profile.education_level.value}' matches accepted levels ({levels_str})."
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=reason,
                    evidence_quote=evidence_quote,
                )
            )
        else:
            if len(req_levels) == 1:
                reason = f"Education level '{profile.education_level.value}' does not match required level '{req_levels[0].value}'."
            else:
                levels_str = " or ".join(f"'{lvl.value}'" for lvl in req_levels)
                reason = f"Education level '{profile.education_level.value}' does not match any accepted levels ({levels_str})."
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.EDUCATION_LEVEL,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=reason,
                    evidence_quote=evidence_quote,
                )
            )

    # 3. Academic Marks / Percentage Constraint (with affirmative-action relaxation handling)
    marks_relaxation = parse_academic_marks_relaxation(criterion_text)
    if marks_relaxation is not None:
        base_cutoff = marks_relaxation.base_percentage
        relaxed_cutoff = marks_relaxation.relaxed_percentage
        groups = marks_relaxation.groups_text

        if profile.academic_percentage is None:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.UNKNOWN,
                    reason=f"Criterion requires minimum academic marks of {base_cutoff:g}% ({relaxed_cutoff:g}% for {groups}), but percentage was not provided in profile.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.academic_percentage >= base_cutoff:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.PASS,
                    reason=f"Academic percentage {profile.academic_percentage:g}% satisfies general minimum cutoff of {base_cutoff:g}%.",
                    evidence_quote=evidence_quote,
                )
            )
        elif profile.academic_percentage < relaxed_cutoff:
            evaluations.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.ACADEMIC_MARKS,
                    raw_criterion=criterion_text,
                    status=CriterionStatus.FAIL,
                    reason=f"Academic percentage {profile.academic_percentage:g}% is below the minimum relaxed cutoff of {relaxed_cutoff:g}% (applicable to {groups}).",
                    evidence_quote=evidence_quote,
                )
            )
        else:
            # Within relaxation gap [relaxed_cutoff, base_cutoff)
            is_covered, is_indeterminate = evaluate_relaxation_membership(profile, groups)
            if is_covered:
                evaluations.append(
                    CriterionEvaluation(
                        criterion_type=CriterionType.ACADEMIC_MARKS,
                        raw_criterion=criterion_text,
                        status=CriterionStatus.PASS,
                        reason=f"Academic percentage {profile.academic_percentage:g}% satisfies relaxed minimum cutoff of {relaxed_cutoff:g}% for {groups}.",
                        evidence_quote=evidence_quote,
                    )
                )
            elif is_indeterminate:
                evaluations.append(
                    CriterionEvaluation(
                        criterion_type=CriterionType.ACADEMIC_MARKS,
                        raw_criterion=criterion_text,
                        status=CriterionStatus.UNKNOWN,
                        reason=f"Academic percentage {profile.academic_percentage:g}% is in the relaxation window ({relaxed_cutoff:g}% - {base_cutoff:g}%); eligibility requires confirming category/gender/disability status not provided in profile.",
                        evidence_quote=evidence_quote,
                    )
                )
            else:
                evaluations.append(
                    CriterionEvaluation(
                        criterion_type=CriterionType.ACADEMIC_MARKS,
                        raw_criterion=criterion_text,
                        status=CriterionStatus.FAIL,
                        reason=f"Academic percentage {profile.academic_percentage:g}% is below the required general cutoff of {base_cutoff:g}% (relaxation to {relaxed_cutoff:g}% applies only to {groups}).",
                        evidence_quote=evidence_quote,
                    )
                )
    else:
        min_marks = extract_academic_percentage(clean_criterion_text)
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

    # 4. Gender Constraint (evaluated on stripped text to prevent marks relaxation groups from being treated as mandatory)
    target_gender = extract_target_gender(clean_criterion_text)
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

    # 5. Social Category Constraint (evaluated on stripped text to prevent marks relaxation groups from being treated as mandatory)
    target_cat = extract_target_category(clean_criterion_text)
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

    # 6. Disability Constraint (evaluated on stripped text to prevent marks relaxation groups from being treated as mandatory)
    if extract_disability_requirement(clean_criterion_text):
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

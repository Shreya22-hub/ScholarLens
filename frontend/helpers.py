"""Frontend helper functions for ScholarLens.

Handles input validation, payload transformation, result categorization,
display formatting, and API client operations with fallback.
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple
import requests

__all__ = [
    "LEVEL_MAP",
    "GENDER_MAP",
    "CATEGORY_MAP",
    "validate_and_build_profile",
    "categorize_match_results",
    "format_amount_display",
    "format_deadline_display",
    "format_application_status_display",
    "humanize_criterion_type",
    "get_record_category_label",
    "get_record_status_label",
    "get_record_verification_label",
    "filter_match_results",
    "evaluate_student_matching",
]


# Mapping constants from UI dropdown labels to Pydantic schema enums
LEVEL_MAP: Dict[str, str] = {
    "Undergraduate (Degree / B.Tech / MBBS / B.Sc)": "undergraduate",
    "Class 12 / Higher Secondary (+2)": "class_12",
    "Class 10 / Secondary School": "class_10",
    "Polytechnic Diploma": "diploma",
    "Postgraduate (Master's / M.Tech / MBA)": "postgraduate",
    "Doctorate / Ph.D": "doctorate",
}

GENDER_MAP: Dict[str, str] = {
    "Female (Girl Student)": "female",
    "Male": "male",
    "Other": "other",
}

CATEGORY_MAP: Dict[str, str] = {
    "General": "general",
    "OBC (Other Backward Classes)": "obc",
    "SC (Scheduled Caste)": "sc",
    "ST (Scheduled Tribe)": "st",
    "EWS (Economically Weaker Section)": "ews",
    "Minority Community": "minority",
}


def validate_and_build_profile(
    ed_choice: str,
    course_input: Optional[str] = None,
    income_num: float = 0.0,
    marks_num: float = 0.0,
    gender_choice: str = "Not Specified",
    cat_choice: str = "Not Specified",
    state_input: Optional[str] = None,
    is_pwd: bool = False,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Validate student inputs and construct a schema-compliant StudentProfile payload.

    Returns:
        (is_valid, error_message, payload_dict)
    """
    if marks_num < 0.0 or marks_num > 100.0:
        return False, "Academic marks percentage must be between 0.0 and 100.0%.", {}

    if income_num < 0.0:
        return False, "Annual family income cannot be negative.", {}

    payload: Dict[str, Any] = {}

    if ed_choice in LEVEL_MAP:
        payload["education_level"] = LEVEL_MAP[ed_choice]

    if course_input and course_input.strip():
        payload["course"] = course_input.strip()

    if income_num > 0.0:
        payload["annual_family_income"] = float(income_num)

    if marks_num > 0.0:
        payload["academic_percentage"] = float(marks_num)

    if gender_choice in GENDER_MAP:
        payload["gender"] = GENDER_MAP[gender_choice]

    if cat_choice in CATEGORY_MAP:
        payload["category"] = CATEGORY_MAP[cat_choice]

    if state_input and state_input.strip():
        payload["state_of_residence"] = state_input.strip()

    if is_pwd:
        payload["is_differently_abled"] = True

    return True, None, payload


def categorize_match_results(results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group matching results into distinct, explainable categories for UI display.

    Design:
    - confirmed_eligible: Contains ALL confirmed, verified scholarships meeting all mandatory criteria.
      An unverified scholarship is strictly excluded from confirmed_eligible.
    - open_confirmed_eligible: Subset of confirmed_eligible with confirmed 'open' application cycle.
    - historical_closed_eligible: Subset of confirmed_eligible with closed or expired cycle.
    - unknown_cycle_eligible: Subset of confirmed_eligible with unknown or unconfirmed cycle dates.
    - potentially_eligible: Contains verified scholarships where no mandatory criteria failed, but certain
      criteria remain pending additional profile details or manual circular review.
    - not_eligible: Contains verified scholarships where at least one mandatory condition clearly failed.
    - unverified_provisional: Dedicated category collecting all records that remain UNVERIFIED
      against primary source circulars, isolated from confirmed and verified outcome tabs.

    Note on counts:
    - open_confirmed_eligible + historical_closed_eligible + unknown_cycle_eligible + potentially_eligible + not_eligible + unverified_provisional
      strictly partition the total evaluated scholarships (sum == total_evaluated).
    - Every scholarship appears in exactly one primary UI subtab.
    """
    confirmed_eligible: List[Dict[str, Any]] = []
    open_confirmed_eligible: List[Dict[str, Any]] = []
    historical_closed_eligible: List[Dict[str, Any]] = []
    unknown_cycle_eligible: List[Dict[str, Any]] = []
    potentially_eligible: List[Dict[str, Any]] = []
    not_eligible: List[Dict[str, Any]] = []
    unverified_provisional: List[Dict[str, Any]] = []

    for r in results:
        outcome = r.get("match_outcome")
        is_provisional = r.get("is_provisional", False)
        ver_status = r.get("verification_status", "unverified")
        app_status = (r.get("application_status") or "unknown").strip().lower()

        if is_provisional or ver_status != "verified":
            unverified_provisional.append(r)
        elif outcome == "eligible":
            confirmed_eligible.append(r)
            if app_status == "open":
                open_confirmed_eligible.append(r)
            elif app_status in ("closed", "expired"):
                historical_closed_eligible.append(r)
            else:
                unknown_cycle_eligible.append(r)
        elif outcome == "potentially_eligible":
            potentially_eligible.append(r)
        elif outcome == "not_eligible":
            not_eligible.append(r)

    return {
        "confirmed_eligible": confirmed_eligible,
        "open_confirmed_eligible": open_confirmed_eligible,
        "historical_closed_eligible": historical_closed_eligible,
        "unknown_cycle_eligible": unknown_cycle_eligible,
        "potentially_eligible": potentially_eligible,
        "not_eligible": not_eligible,
        "unverified_provisional": unverified_provisional,
    }


def format_deadline_display(
    deadline_iso: Optional[str],
    deadline_raw: Optional[str] = None,
    verification_status: str = "verified",
) -> Tuple[str, Optional[str]]:
    """Format scholarship deadline for honest display.

    Returns:
        (display_text, optional_caption)
    """
    if not deadline_iso and not deadline_raw:
        return "To be announced / Not established", None

    raw_candidate = (deadline_raw or "").strip()
    iso_candidate = (deadline_iso or "").strip()

    # Only treat as true ISO if it matches YYYY-MM-DD
    is_true_iso = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", iso_candidate))

    if is_true_iso:
        if verification_status != "verified":
            caption = f'Source text: "{raw_candidate}" (Unverified)' if raw_candidate else "Unverified against primary circular"
            return f"{iso_candidate} (ISO - Unverified)", caption
        else:
            caption = f'Source text: "{raw_candidate}"' if raw_candidate and raw_candidate != iso_candidate else None
            return f"{iso_candidate} (ISO)", caption

    # If deadline_iso is an unparsed string (e.g. from Phase 2 unstructured text)
    unparsed_text = iso_candidate or raw_candidate
    return f'"{unparsed_text}" (Date unverified)', "Unstructured source sentence; date not established in ISO format."


def format_amount_display(
    amount_str: Optional[str],
    verification_status: str = "verified",
    verification_notes: Optional[str] = None,
) -> str:
    """Format scholarship amount for honest, transparent display.

    Visibly flags suspicious, malformed, or unverified amounts.
    """
    if not amount_str or not amount_str.strip():
        return "Not verified / Pending official circular"

    amount_clean = amount_str.strip()

    # Check if amount was flagged in verification notes or failed sanity check
    notes_lower = (verification_notes or "").lower()
    if "amount" in notes_lower and any(k in notes_lower for k in ["sanity", "unverified", "malformed", "token fee"]):
        return f"{amount_clean} (Flagged: Failed validation sanity check)"

    # Check if raw amount is a malformed token amount (< 500)
    digits = re.sub(r"\D", "", amount_clean)
    if digits and int(digits) < 500:
        return f"{amount_clean} (Unverified / Suspect token amount)"

    if verification_status != "verified":
        return f"{amount_clean} (Unverified)"

    return amount_clean


def format_application_status_display(
    application_status: Optional[str],
    scheme_year: Optional[str] = None,
) -> Tuple[str, str]:
    """Format application cycle status with clear distinction from verification status.

    Returns:
        (badge_text, caption_or_help)
    """
    status = (application_status or "unknown").strip().lower()
    year_suffix = f" ({scheme_year})" if scheme_year else ""

    if status == "open":
        return f"Open for applications{year_suffix}", "Applications currently accepted as established by official circular."
    elif status == "deadline_published_status_unknown":
        return f"Deadline published (status unconfirmed){year_suffix}", "A future deadline is published, but official opening dates or acceptance status are unconfirmed."
    elif status == "closed":
        return f"Closed / past cycle{year_suffix}", "This application cycle is closed. Documented historically from official records."
    elif status == "expired":
        return f"Expired / deadline passed{year_suffix}", "The official application submission deadline has passed."
    else:
        return f"Status unknown / unconfirmed{year_suffix}", "Application cycle dates are not published or unconfirmed in official circular."


def humanize_criterion_type(crit_type: Optional[str]) -> str:
    """Convert raw backend criterion type enums into student-friendly, human-readable labels."""
    if not crit_type:
        return "General Criterion"

    clean = crit_type.strip().lower()
    mapping = {
        "education_level": "Education Level",
        "academic_marks": "Academic Marks Requirement",
        "income": "Family Income Ceiling",
        "annual_family_income": "Family Income Ceiling",
        "gender": "Gender Restriction",
        "category": "Social Category",
        "disability": "Disability Benchmark",
        "state": "State / Domicile",
        "course": "Degree / Discipline",
        "general": "General Requirement",
    }
    return mapping.get(clean, clean.replace("_", " ").title())


def get_record_category_label(r: Dict[str, Any]) -> str:
    """Return the primary category label for a scholarship match record."""
    outcome = r.get("match_outcome")
    is_provisional = r.get("is_provisional", False)
    ver_status = r.get("verification_status", "unverified")
    app_status = (r.get("application_status") or "unknown").strip().lower()

    if is_provisional or ver_status != "verified":
        return "Provisional / Unverified"
    elif outcome == "eligible":
        if app_status == "open":
            return "Confirmed Open"
        else:
            return "Historical / Unconfirmed"
    elif outcome == "potentially_eligible":
        return "Potentially Eligible"
    elif outcome == "not_eligible":
        return "Not Eligible"
    return "Other"


def get_record_status_label(r: Dict[str, Any]) -> str:
    """Return the standardized application status label for filtering."""
    app_status = (r.get("application_status") or "unknown").strip().lower()
    if app_status == "open":
        return "Open"
    elif app_status in ("closed", "expired"):
        return "Closed / Past Cycle"
    else:
        return "Unknown / Unconfirmed"


def get_record_verification_label(r: Dict[str, Any]) -> str:
    """Return the standardized verification label for filtering."""
    is_provisional = r.get("is_provisional", False)
    ver_status = r.get("verification_status", "unverified")
    if ver_status == "verified" and not is_provisional:
        return "Verified"
    else:
        return "Unverified / Provisional"


def filter_match_results(
    records: List[Dict[str, Any]],
    query: str = "",
    authority: str = "All",
    status: str = "All",
    verification: str = "All",
    category: str = "All",
) -> List[Dict[str, Any]]:
    """Filter scholarship match results in-memory by search query and quick filters.

    Supports:
    - Multi-token case-insensitive search across scheme name, authority, description, explanation,
      category/degree details, and criteria.
    - Governing authority match (or 'All' / 'All Authorities').
    - Application status match ('Open', 'Closed / Past Cycle', 'Unknown / Unconfirmed', or 'All').
    - Verification match ('Verified', 'Unverified / Provisional', or 'All').
    - Eligibility category match ('Confirmed Open', 'Historical / Unconfirmed', 'Potentially Eligible',
      'Not Eligible', 'Provisional / Unverified', or 'All').
    """
    if not records:
        return []

    tokens = [t.lower() for t in query.strip().split() if t.strip()]

    filtered: List[Dict[str, Any]] = []
    for r in records:
        # 1. Eligibility Category filter
        if category and category not in ("All", "All Categories"):
            cat_label = get_record_category_label(r)
            clean_cat = category.strip().lower()
            clean_label = cat_label.strip().lower()
            if clean_cat != clean_label and clean_cat.replace(" ", "") != clean_label.replace(" ", ""):
                continue

        # 2. Authority filter
        if authority and authority not in ("All", "All Authorities"):
            r_auth = (r.get("government_authority") or "").strip().lower()
            if r_auth != authority.strip().lower():
                continue

        # 3. Application status filter
        if status and status not in ("All", "All Statuses"):
            app_status = (r.get("application_status") or "unknown").strip().lower()
            clean_status = status.strip().lower()
            if clean_status in ("open", "confirmed open"):
                if app_status != "open":
                    continue
            elif clean_status in ("closed", "closed / past cycle", "closed/past cycle", "historical / closed", "historical/closed", "expired"):
                if app_status not in ("closed", "expired"):
                    continue
            elif clean_status in ("unknown", "unknown / unconfirmed", "unknown/unconfirmed", "status unconfirmed", "unconfirmed"):
                if app_status in ("open", "closed", "expired"):
                    continue

        # 4. Verification filter
        if verification and verification not in ("All", "All Verification", "All Verification Statuses"):
            is_prov = r.get("is_provisional", False)
            ver_status = r.get("verification_status", "unverified")
            clean_ver = verification.strip().lower()
            if clean_ver in ("verified", "verified circular"):
                if is_prov or ver_status != "verified":
                    continue
            elif clean_ver in ("unverified", "provisional", "unverified / provisional", "unverified/provisional"):
                if not is_prov and ver_status == "verified":
                    continue

        # 5. Search query tokens (AND search)
        if tokens:
            name_text = str(r.get("scheme_name") or "").lower()
            auth_text = str(r.get("government_authority") or "").lower()
            desc_text = str(r.get("description") or "").lower()
            expl_text = str(r.get("explanation") or "").lower()
            course_text = str(r.get("course") or "").lower()
            edu_text = str(r.get("education_level") or "").lower()
            cat_text = str(r.get("category") or "").lower()
            year_text = str(r.get("scheme_year") or "").lower()

            crit_texts = []
            for key in ("passed_criteria", "failed_criteria", "unknown_criteria"):
                crits = r.get(key) or []
                for c in crits:
                    if isinstance(c, dict):
                        crit_texts.append(str(c.get("criterion_type") or "").lower())
                        crit_texts.append(str(c.get("description") or "").lower())
                        crit_texts.append(str(c.get("reason") or "").lower())
                    elif isinstance(c, str):
                        crit_texts.append(c.lower())

            haystack = f"{name_text} {auth_text} {desc_text} {expl_text} {course_text} {edu_text} {cat_text} {year_text} {' '.join(crit_texts)}"
            if not all(tok in haystack for tok in tokens):
                continue

        filtered.append(r)

    return filtered


def evaluate_student_matching(
    profile_data: Dict[str, Any],
    backend_url: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Submit student profile to /api/matching/evaluate with direct service fallback.

    Returns:
        (success, error_message, response_data)
    """
    backend_url = (backend_url or os.getenv("BACKEND_API_URL", "http://127.0.0.1:8000")).rstrip("/")
    try:
        res = requests.post(f"{backend_url}/api/matching/evaluate", json=profile_data, timeout=10.0)
        if res.status_code == 200:
            data = res.json()
            data["is_fallback"] = False
            return True, None, data
        elif res.status_code == 422:
            detail = res.json().get("detail", "Invalid profile parameters.")
            return False, f"API Validation Error: {detail}", None
        else:
            return False, f"Matching API returned HTTP {res.status_code}.", None
    except requests.RequestException:
        # Backend server is offline; attempt direct service execution fallback
        try:
            from backend.app.db.database import get_db
            from backend.app.matching.service import MatchingService
            from backend.app.schemas.matching import StudentProfile

            profile = StudentProfile(**profile_data)
            service = MatchingService(db=get_db())
            resp = service.evaluate_profile(profile)
            data = resp.model_dump(mode="json")
            data["is_fallback"] = True
            return True, None, data
        except Exception as e:
            return False, f"Failed to execute local matching engine fallback: {str(e)}", None

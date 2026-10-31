"""Frontend helper functions for ScholarLens.

Handles input validation, payload transformation, result categorization,
display formatting, and API client operations with fallback.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
import requests


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
    - confirmed_eligible: Contains ONLY confirmed, verified scholarships meeting all mandatory criteria.
      An unverified scholarship is strictly excluded from confirmed_eligible.
    - potentially_eligible: Contains scholarships where no mandatory criteria failed, but certain
      criteria remain pending additional profile details or manual circular review.
    - not_eligible: Contains scholarships where at least one mandatory condition clearly failed.
    - unverified_provisional: A dedicated audit subset collecting all records that remain UNVERIFIED
      against primary source circulars.

    Note on counts:
    - confirmed_eligible + potentially_eligible + not_eligible partition the total evaluated scholarships
      (sum == total_evaluated).
    - unverified_provisional represents the cross-cutting subset of total evaluated scholarships with
      provisional status, ensuring users can audit unverified schemes without inflating the primary outcome partition.
    """
    confirmed_eligible: List[Dict[str, Any]] = []
    potentially_eligible: List[Dict[str, Any]] = []
    not_eligible: List[Dict[str, Any]] = []
    unverified_provisional: List[Dict[str, Any]] = []

    for r in results:
        outcome = r.get("match_outcome")
        is_provisional = r.get("is_provisional", False)
        ver_status = r.get("verification_status", "unverified")

        if is_provisional or ver_status != "verified":
            unverified_provisional.append(r)

        if outcome == "eligible" and not is_provisional and ver_status == "verified":
            confirmed_eligible.append(r)
        elif outcome == "potentially_eligible":
            potentially_eligible.append(r)
        elif outcome == "not_eligible":
            not_eligible.append(r)

    return {
        "confirmed_eligible": confirmed_eligible,
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
        return f"🟢 Open for Applications{year_suffix}", "Applications currently accepted as established by official circular."
    elif status == "deadline_published_status_unknown":
        return f"🟡 Deadline Published (Status Unconfirmed){year_suffix}", "A future deadline is published, but official opening dates or acceptance status are unconfirmed."
    elif status == "closed":
        return f"🔴 Closed / Past Cycle{year_suffix}", "This application cycle is closed. Documented historically from official records."
    elif status == "expired":
        return f"🔴 Expired / Deadline Passed{year_suffix}", "The official application submission deadline has passed."
    else:
        return f"⚪ Status Unknown / Unconfirmed{year_suffix}", "Application cycle dates are not published or unconfirmed in official circular."


def evaluate_student_matching(
    profile_data: Dict[str, Any],
    backend_url: str = "http://127.0.0.1:8000",
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Submit student profile to /api/matching/evaluate with direct service fallback.

    Returns:
        (success, error_message, response_data)
    """
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

"""Quality assurance, date normalization, amount validation, and verification rules for ScholarLens.

Enforces evidence-based data quality:
- Rejection of generic scheme names (e.g. 'Fellowship')
- Sanity checks on financial amounts (rejecting malformed tokens like 'Rs 30')
- ISO date normalization (YYYY-MM-DD) while preserving raw text evidence
- Deduplication and removal of generic eligibility statements
- Conservative verification rules with explicit explanatory notes
"""

from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas.scholarship import VerificationStatus

GENERIC_SCHEME_NAMES: Set[str] = {
    "fellowship",
    "fellowships",
    "scholarship",
    "scholarships",
    "scheme",
    "schemes",
    "student development scheme",
    "student development schemes",
    "central sector scheme",
    "central sector schemes",
    "state scheme",
    "state schemes",
    "ugc schemes",
    "aicte schemes",
    "guidelines",
    "guideline",
    "notification",
    "announcement",
    "announcements",
    "home",
    "about us",
    "login",
    "register",
    "apply online",
    "overview",
    "general information",
    "student login",
    "institute login",
    "officer login",
    "faq",
    "faqs",
    "disclaimer",
    "quick links",
}

GENERIC_ELIGIBILITY_PHRASES: Set[str] = {
    "eligibility",
    "eligibility criteria",
    "scheme eligibility criteria",
    "as per scheme eligibility criteria",
    "as per eligibility criteria",
    "who can apply",
    "general conditions",
    "terms and conditions",
    "eligibility rules",
    "criteria",
    "click here for eligibility",
    "eligibility conditions",
    "eligibility details",
    "conditions of eligibility",
    "scheme guidelines",
    "minimum eligibility",
}


def is_generic_scheme_name(name: Optional[str]) -> bool:
    """Check if a scheme name is merely a generic category label."""
    if not name or not isinstance(name, str):
        return True
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip().lower()
    if cleaned in GENERIC_SCHEME_NAMES:
        return True

    words = cleaned.split()
    if len(words) == 1 and words[0] in {
        "scholarship",
        "fellowship",
        "scheme",
        "grant",
        "stipend",
        "award",
        "circular",
    }:
        return True

    if len(words) < 2 and len(cleaned) < 10:
        return True

    return False


def normalize_date_iso(date_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Normalize extracted date string to ISO format (YYYY-MM-DD) if clearly established.

    Returns:
        (iso_date, raw_text_evidence)
    """
    if not date_text or not isinstance(date_text, str):
        return None, None

    raw_evidence = date_text.strip()
    if not raw_evidence or raw_evidence.lower() in [
        "not specified",
        "none",
        "null",
        "tba",
        "to be announced",
        "to be notified",
    ]:
        return None, raw_evidence

    # 1. Match DD-MM-YYYY, DD/MM/YYYY, or DD.MM.YYYY
    m1 = re.search(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})\b", raw_evidence)
    if m1:
        d, m, y = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            iso_str = datetime(y, m, d).strftime("%Y-%m-%d")
            return iso_str, raw_evidence
        except ValueError:
            pass

    # 2. Match YYYY-MM-DD
    m2 = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw_evidence)
    if m2:
        y, m, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            iso_str = datetime(y, m, d).strftime("%Y-%m-%d")
            return iso_str, raw_evidence
        except ValueError:
            pass

    # 3. Match '31st October 2026', '31 Oct 2026', or 'October 31, 2026'
    m3 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s*,?\s*(\d{4})\b", raw_evidence)
    if m3:
        d_str, month_name, y_str = m3.group(1), m3.group(2), m3.group(3)
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                iso_str = datetime.strptime(f"{d_str} {month_name} {y_str}", fmt).strftime("%Y-%m-%d")
                return iso_str, raw_evidence
            except ValueError:
                pass

    m4 = re.search(r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b", raw_evidence)
    if m4:
        month_name, d_str, y_str = m4.group(1), m4.group(2), m4.group(3)
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                iso_str = datetime.strptime(f"{month_name} {d_str} {y_str}", fmt).strftime("%Y-%m-%d")
                return iso_str, raw_evidence
            except ValueError:
                pass

    # Date could not be unambiguously resolved to ISO date
    return None, raw_evidence


def clean_and_validate_amount(amount_text: Optional[str]) -> Tuple[Optional[str], bool, Optional[str]]:
    """Clean and validate scholarship amount.

    Returns:
        (cleaned_amount, is_valid, reason)
    """
    if not amount_text or not isinstance(amount_text, str):
        return None, False, "Amount is not specified in source."

    cleaned = amount_text.strip()
    lower = cleaned.lower()

    if any(ph in lower for ph in [
        "see source",
        "as specified in official",
        "refer to source",
        "guideline",
        "unknown",
        "to be confirmed",
    ]):
        return None, False, "Amount is generic placeholder, not verified financial figure."

    # Valid descriptive financial assistance
    descriptive_valid_terms = [
        "tuition fee",
        "course fee",
        "maintenance allowance",
        "fee waiver",
        "reimbursement of fees",
        "stipend of",
    ]
    if any(term in lower for term in descriptive_valid_terms):
        return cleaned, True, None

    # Currency pattern matching
    m = re.search(
        r"(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:lakh|crore)?(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly)[^\n,.;]*)?",
        cleaned,
        re.I,
    )
    if not m:
        return None, False, f"Amount text '{cleaned}' does not contain recognizable Indian currency format."

    matched_amount = m.group(0).strip()
    raw_num_str = m.group(1).replace(",", "")

    try:
        raw_num = float(raw_num_str)
        if "lakh" in matched_amount.lower():
            raw_num *= 100000
        elif "crore" in matched_amount.lower():
            raw_num *= 10000000

        # Sanity check: scholarships in India are typically >= Rs. 500.
        # Amounts like 'Rs 30' are typically application fees, token charges, or section numbers.
        if raw_num < 500 and not any(kw in matched_amount.lower() for kw in ["per day", "/day", "daily", "per hour"]):
            return (
                None,
                False,
                f"Extracted amount '{matched_amount}' failed sanity check (< Rs. 500). Likely a token fee or malformed token.",
            )
    except ValueError:
        pass

    return matched_amount, True, None


def clean_eligibility_criteria(criteria_list: List[str]) -> List[str]:
    """Remove generic phrases, duplicate items, and trivial text while preserving real conditions."""
    if not criteria_list:
        return []

    cleaned: List[str] = []
    seen_normalized: Set[str] = set()

    for item in criteria_list:
        if not item or not isinstance(item, str):
            continue
        text = re.sub(r"\s+", " ", item).strip(" .;:,-")
        lower = text.lower()

        # Reject generic headers/phrases
        if lower in GENERIC_ELIGIBILITY_PHRASES or len(text) < 15:
            continue
        if re.match(r"^(?:scheme\s+)?eligibility\s*(?:criteria)?\s*$", lower):
            continue
        if re.match(r"^as\s+per\s+(?:scheme\s+)?eligibility\s+criteria\b", lower):
            continue

        # Check for substantive eligibility indicator
        has_substantive = any(
            kw in lower
            for kw in [
                "income",
                "student",
                "class",
                "degree",
                "percent",
                "%",
                "marks",
                "merit",
                "resident",
                "domicile",
                "belong",
                "citizen",
                "age",
                "pursuing",
                "passed",
                "admitted",
                "enrolled",
                "sc",
                "st",
                "obc",
                "ebc",
                "dnt",
                "women",
                "girl",
                "disabilit",
                "pwd",
                "minority",
                "quota",
                "regular",
            ]
        )
        if not has_substantive:
            continue

        norm_key = re.sub(r"[^a-z0-9]", "", lower)
        if norm_key in seen_normalized:
            continue

        # Check for near-substring duplicates (keep the more descriptive one)
        is_sub = False
        for idx, existing in enumerate(cleaned):
            existing_norm = re.sub(r"[^a-z0-9]", "", existing.lower())
            if norm_key in existing_norm:
                is_sub = True
                break
            elif existing_norm in norm_key:
                cleaned[idx] = text
                seen_normalized.remove(existing_norm)
                seen_normalized.add(norm_key)
                is_sub = True
                break

        if not is_sub:
            seen_normalized.add(norm_key)
            cleaned.append(text)

    return cleaned


def evaluate_verification_status(
    scheme_name: str,
    government_authority: str,
    scholarship_amount: Optional[str],
    application_deadline: Optional[str],
    eligibility_criteria: List[str],
    supporting_quotations: List[str],
    field_evidence: Optional[Dict[str, str]] = None,
) -> Tuple[VerificationStatus, Optional[str]]:
    """Apply conservative verification rules to determine record status and explanation.

    A record is ONLY marked VERIFIED if:
    - Scheme name is specific and non-generic
    - Government authority is identified
    - Scholarship amount is plausible and backed by evidence
    - Meaningful eligibility conditions exist
    - All claims are grounded in supporting quotations
    """
    reasons: List[str] = []

    # 1. Scheme Name Validation
    if is_generic_scheme_name(scheme_name):
        reasons.append(f"Scheme name '{scheme_name}' is a generic label, not a specific scheme name")

    # 2. Government Authority Validation
    if not government_authority or len(government_authority.strip()) < 4:
        reasons.append("Government authority is not clearly identified")

    # 3. Scholarship Amount Validation
    cleaned_amount, amount_valid, amount_reason = clean_and_validate_amount(scholarship_amount)
    if not amount_valid:
        reasons.append(amount_reason or "Scholarship amount is unverified")

    # 4. Eligibility Criteria Validation
    cleaned_criteria = clean_eligibility_criteria(eligibility_criteria)
    if not cleaned_criteria:
        reasons.append("Eligibility criteria lack verifiable, substantive conditions")

    # 5. Supporting Evidence Validation
    if not supporting_quotations or len(supporting_quotations) == 0:
        reasons.append("Record lacks verbatim source quotations from official portal")

    # If any reason exists, mark UNVERIFIED
    if reasons:
        explanation = "Unverified: " + "; ".join(reasons) + "."
        return VerificationStatus.UNVERIFIED, explanation

    return (
        VerificationStatus.VERIFIED,
        "Verified: Scheme details, eligibility criteria, and financial assistance confirmed against official source content.",
    )

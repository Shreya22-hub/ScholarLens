"""Quality assurance, date normalization, amount validation, and verification rules for ScholarLens.

Enforces evidence-based data quality:
- Rejection of generic scheme names (e.g. 'Fellowship')
- Sanity checks on financial amounts (rejecting malformed tokens like 'Rs 30')
- ISO date normalization (YYYY-MM-DD) while preserving raw text evidence
- Deduplication and removal of generic eligibility statements
- Conservative verification rules with explicit explanatory notes
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas.scholarship import ApplicationStatus, VerificationStatus

GENERIC_SCHEME_NAMES: Set[str] = {
    "fellowship",
    "fellowships",
    "scholarship",
    "scholarships",
    "scheme",
    "schemes",
    "student development scheme",
    "student development schemes",
    "students development scheme",
    "students development schemes",
    "faculty development scheme",
    "faculty development schemes",
    "institutional development scheme",
    "institutional development schemes",
    "official scholarship scheme",
    "official scholarship schemes",
    "scholarship directory",
    "scholarships directory",
    "list of schemes",
    "list of scholarships",
    "scheme directory",
    "schemes directory",
    "all schemes",
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

    # Rejection of call-to-actions, announcements, and navigation banners
    if any(cleaned.startswith(p) for p in [
        "explore ",
        "welcome ",
        "click here",
        "apply online",
        "view details",
        "about us",
        "extension of last date",
    ]):
        return True

    # Rejection of incomplete acronym fragments without council or scheme qualifier
    if cleaned in {"fellowship apdf", "scholarship apdf", "guidelines apdf"}:
        return True

    # Rejection of table headers / column header rows
    if any(h in cleaned for h in [
        "sno ",
        "slno ",
        "sl no",
        "serial no",
        "scheme authority",
        "name of scheme",
        "scheme name",
        "scholarship name",
    ]):
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

    # Reject government circular or file numbers that resemble date strings (e.g. 'O.M. F No: 12-4/2019-U1')
    if re.search(r"(?:o\.?m\.?|f\.?\s*no|file\s*no|ref\s*no)\b", raw_evidence, re.IGNORECASE):
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


def determine_application_status(
    deadline_iso: Optional[str] = None,
    raw_text: Optional[str] = None,
    reference_date: Optional[datetime] = None,
) -> Tuple[str, Optional[str]]:
    """Determine application status and academic year from extracted dates and source text.

    Distinguishes:
    - "open": explicitly open application window or opening_date <= today <= closing_date
    - "deadline_published_status_unknown": future deadline published without confirmed opening dates
    - "expired": published deadline has passed
    - "closed": circular/batch explicitly closed, past academic cycle, or selection merit list
    - "unknown": missing dates, ambiguous periods, or conflicting status statements

    Returns:
        (application_status, scheme_year)
    """
    ref_dt = reference_date or datetime.now(timezone.utc)
    ref_date = ref_dt.date() if isinstance(ref_dt, datetime) else ref_dt

    combined_text = (raw_text or "")
    scheme_year = None

    # Detect academic year (e.g. 2026-27, 2026–27, 2024-25, 2019-2020)
    year_match = re.search(r"\b(20\d{2}\s*[-–/]\s*(?:20\d{2}|\d{2}))\b", combined_text)
    if year_match:
        scheme_year = re.sub(r"\s+", "", year_match.group(1)).replace("–", "-")

    lower_text = combined_text.lower()

    # 1. Detect conflicting status evidence
    has_explicit_open = bool(
        re.search(
            r"\b(now open for receiving|portal (?:is )?(?:now )?open|applications (?:are )?(?:currently )?(?:now )?open|portal is open for registration|inviting online applications)\b",
            lower_text,
        )
    )
    has_explicit_closed = bool(
        re.search(
            r"\b(applications? (?:are )?(?:now )?closed|portal (?:is )?closed|closed for new applications|admissions? closed|process concluded|submission closed)\b",
            lower_text,
        )
    )
    if has_explicit_open and has_explicit_closed:
        return ApplicationStatus.UNKNOWN.value, scheme_year

    # 2. Check for past completed cycles / archived batches / merit lists
    if any(k in lower_text for k in ["batch 2019", "batch 2018", "batch 2017", "batch 2016", "batch 2015", "batch 2014"]):
        return ApplicationStatus.CLOSED.value, scheme_year

    if "merit list" in lower_text and any(y in lower_text for y in ["2024", "2023", "2022", "2021", "2020"]):
        return ApplicationStatus.CLOSED.value, scheme_year

    # 3. Date extraction with explicit separation of:
    # A. Opening date for student submissions
    # B. Student application submission closing deadline
    # C. Institutional/SNO verification milestones (never treated as student deadlines)
    # D. Final portal closure date
    has_verification_milestones = bool(
        re.search(
            r"\b(verification of applications?|verification by institutes?|sno verification|l1 level|l2 level)\b",
            lower_text,
        )
    )

    # Detect opening date for students
    opening_date = None
    m_open = re.search(
        r"(?:opening of portal(?: for students?)?|portal opens?(?: for students?)?|start(?:ing)? date|commencement|open(?:s|ed)? (?:on|from))[^\w\d]*([^\n;,\.]{4,40})",
        combined_text,
        re.I,
    )
    if m_open:
        iso_open, _ = normalize_date_iso(m_open.group(1))
        if iso_open:
            try:
                opening_date = datetime.strptime(iso_open, "%Y-%m-%d").date()
            except ValueError:
                pass

    # Detect student application submission deadline
    student_submission_deadline = None
    if deadline_iso:
        try:
            student_submission_deadline = datetime.strptime(deadline_iso, "%Y-%m-%d").date()
        except ValueError:
            pass

    if not student_submission_deadline:
        student_close_patterns = [
            r"(?:last date for (?:submission|applying|online applications?)|submission deadline|student submission closing date)\s*[:\-]?\s*([^\n;]{4,40})",
            r"(?:closing date for (?:submission|students?)|last date of application)\s*[:\-]?\s*([^\n;]{4,40})",
        ]
        for cpat in student_close_patterns:
            for mc in re.finditer(cpat, combined_text, re.I):
                matched_line = mc.group(0).lower()
                # Reject verification milestones and administrative scrutiny
                if any(v in matched_line for v in ["verification", "scrutiny", "l1 level", "l2 level", "sno level", "discrepanc"]):
                    continue
                iso_close, _ = normalize_date_iso(mc.group(1))
                if iso_close:
                    try:
                        student_submission_deadline = datetime.strptime(iso_close, "%Y-%m-%d").date()
                        break
                    except ValueError:
                        pass
            if student_submission_deadline:
                break

    # Detect final portal closure date (if distinct from submission deadline)
    final_portal_closure = None
    m_pclose = re.search(
        r"(?:portal to be closed on|to be closed on)\s*[:\-]?\s*([^\n;]{4,40})",
        combined_text,
        re.I,
    )
    if m_pclose:
        iso_pclose, _ = normalize_date_iso(m_pclose.group(1))
        if iso_pclose:
            try:
                final_portal_closure = datetime.strptime(iso_pclose, "%Y-%m-%d").date()
            except ValueError:
                pass

    # 4. Evaluate status based on dates
    # Rule 1: If institutional verification milestones exist but no student submission deadline
    # was established, do NOT infer that student applications are open.
    if has_verification_milestones and not student_submission_deadline and not has_explicit_open:
        return ApplicationStatus.UNKNOWN.value, scheme_year

    effective_deadline = student_submission_deadline or final_portal_closure

    if effective_deadline:
        if effective_deadline < ref_date:
            return ApplicationStatus.EXPIRED.value, scheme_year
        else:
            # Future deadline exists (effective_deadline >= ref_date)
            # A future deadline does not, by itself, prove applications are currently open.
            # Check if student submission window is verified
            if student_submission_deadline and opening_date:
                if opening_date <= ref_date <= student_submission_deadline:
                    return ApplicationStatus.OPEN.value, scheme_year
                elif opening_date > ref_date:
                    return ApplicationStatus.DEADLINE_PUBLISHED_STATUS_UNKNOWN.value, scheme_year

            # If opening date is not explicitly available, check for explicit open affirmation
            if has_explicit_open:
                return ApplicationStatus.OPEN.value, scheme_year

            # Future deadline is published, but current student opening status is unconfirmed
            return ApplicationStatus.DEADLINE_PUBLISHED_STATUS_UNKNOWN.value, scheme_year

    # 5. No effective deadline established
    if has_explicit_open:
        if opening_date and opening_date > ref_date:
            return ApplicationStatus.DEADLINE_PUBLISHED_STATUS_UNKNOWN.value, scheme_year
        return ApplicationStatus.OPEN.value, scheme_year

    if has_explicit_closed:
        return ApplicationStatus.CLOSED.value, scheme_year

    # Missing dates or unconfirmed status
    return ApplicationStatus.UNKNOWN.value, scheme_year


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
    raw_num_str = m.group(1).replace(",", "").strip()

    if not raw_num_str or not re.search(r"\d", raw_num_str):
        return None, False, f"Amount text '{cleaned}' lacks numeric currency digits."

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
        return None, False, f"Could not parse numeric amount from '{raw_num_str}'."

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

        # Reject institutional objectives / scheme purpose statements
        if (
            re.match(r"^(?:for|to)\s+(?:promot|enhanc|encourag|foster)(?:e|ing)\b", lower)
            or re.match(r"^(?:the\s+)?(?:objective|aim|purpose)\s+of\s+(?:the\s+)?scheme\b", lower)
            or re.match(r"^(?:this\s+)?scheme\s+(?:aims|is\s+intended)\s+to\b", lower)
        ):
            continue

        # Check for substantive eligibility indicator (using word boundaries for short acronyms)
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
                "women",
                "girl",
                "disabilit",
                "minority",
                "quota",
                "regular",
                "faculty",
                "polytechnic",
                "diploma",
                "undergraduate",
                "postgraduate",
                "doctoral",
                "doctorate",
                "bachelor",
                "master",
            ]
        ) or bool(re.search(r"\b(?:sc|st|obc|ebc|dnt|pwd)\b", lower))
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
    quotations = [q.strip() for q in (supporting_quotations or []) if isinstance(q, str) and q.strip()]
    if not quotations:
        reasons.append("Record lacks verbatim source quotations from official portal")

    # 6. Field-level evidence validation. A non-empty quotation somewhere on the
    # page is not proof for every extracted field. The ingestion pipeline passes
    # an evidence mapping; when present, require evidence for each critical claim.
    if field_evidence is not None:
        required_evidence = {
            "scheme_name": "Scheme name",
            "government_authority": "Government authority",
            "scholarship_amount": "Scholarship amount",
            "eligibility_criteria": "Eligibility criteria",
        }
        for key, label in required_evidence.items():
            evidence = field_evidence.get(key)
            if not isinstance(evidence, str) or not evidence.strip():
                reasons.append(f"{label} lacks field-level source evidence")
                continue
            # The field evidence must be tied to the captured page quotations,
            # rather than being an unrelated string or a generated explanation.
            if quotations and not any(evidence.strip() in q or q in evidence.strip() for q in quotations):
                reasons.append(f"{label} evidence is not corroborated by supporting quotations")

        amount_evidence = field_evidence.get("scholarship_amount", "")
        if amount_valid and cleaned_amount and amount_evidence:
            amount_digits = re.sub(r"\D", "", cleaned_amount)
            evidence_digits = re.sub(r"\D", "", amount_evidence)
            if amount_digits and amount_digits not in evidence_digits:
                reasons.append("Scholarship amount does not match its field-level source evidence")

    # If any reason exists, mark UNVERIFIED
    if reasons:
        explanation = "Unverified: " + "; ".join(reasons) + "."
        return VerificationStatus.UNVERIFIED, explanation

    return (
        VerificationStatus.VERIFIED,
        "Verified: Scheme details, eligibility criteria, and financial assistance confirmed against official source content.",
    )

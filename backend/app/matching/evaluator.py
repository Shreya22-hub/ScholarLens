"""Explainable eligibility evaluator for ScholarLens.

Evaluates a single scholarship against a student profile, enforcing conservative
verification principles, explainable reasons, and grounding in source evidence.
"""

from typing import List, Optional
from backend.app.matching.rules import (
    evaluate_criterion,
    extract_academic_percentage,
    extract_education_level,
    extract_income_ceiling,
    extract_target_category,
    extract_target_gender,
)
from backend.app.schemas.matching import (
    CriterionEvaluation,
    CriterionStatus,
    CriterionType,
    MatchOutcome,
    ScholarshipMatchResult,
    StudentProfile,
)
from backend.app.schemas.scholarship import ScholarshipRecord, VerificationStatus


class EligibilityEvaluator:
    """Evaluates student eligibility for a scholarship with explainable outcomes."""

    @staticmethod
    def evaluate(
        scholarship: ScholarshipRecord,
        profile: StudentProfile,
    ) -> ScholarshipMatchResult:
        """Evaluate a student profile against a single scholarship record."""
        passed: List[CriterionEvaluation] = []
        failed: List[CriterionEvaluation] = []
        unknown: List[CriterionEvaluation] = []

        field_evidence = scholarship.field_evidence or {}
        eligibility_evidence = field_evidence.get("eligibility_criteria")

        # 1. Evaluate scheme-name level explicit constraints (e.g. "Girl Students", "ST Students")
        scheme_name = scholarship.scheme_name or ""
        scheme_evals = evaluate_criterion(
            scheme_name,
            profile,
            evidence_quote=field_evidence.get("scheme_name"),
            is_scheme_name=True,
        )
        for ev in scheme_evals:
            if ev.status == CriterionStatus.PASS:
                passed.append(ev)
            elif ev.status == CriterionStatus.FAIL:
                failed.append(ev)
            else:
                unknown.append(ev)

        # 2. Evaluate explicit scholarship eligibility criteria
        criteria_list = scholarship.eligibility_criteria or []
        for crit in criteria_list:
            if not isinstance(crit, str) or not crit.strip():
                continue
            evals = evaluate_criterion(
                crit.strip(),
                profile,
                evidence_quote=eligibility_evidence,
            )
            for ev in evals:
                if ev.status == CriterionStatus.PASS:
                    passed.append(ev)
                elif ev.status == CriterionStatus.FAIL:
                    failed.append(ev)
                else:
                    unknown.append(ev)

        # 3. Handle cases where scholarship has no criteria recorded
        if not criteria_list and not scheme_evals:
            unknown.append(
                CriterionEvaluation(
                    criterion_type=CriterionType.GENERAL,
                    raw_criterion="Official eligibility criteria not recorded",
                    status=CriterionStatus.UNKNOWN,
                    reason="Eligibility criteria were not recorded or extracted from the source page. Official notification must be consulted.",
                    evidence_quote=None,
                )
            )

        # 4. Deduplicate evaluation items across scheme title and criteria
        seen_keys = set()
        dedup_passed: List[CriterionEvaluation] = []
        dedup_failed: List[CriterionEvaluation] = []
        dedup_unknown: List[CriterionEvaluation] = []

        for p in passed:
            k = (p.criterion_type, p.status, p.reason)
            if k not in seen_keys:
                seen_keys.add(k)
                dedup_passed.append(p)

        for f in failed:
            k = (f.criterion_type, f.status, f.reason)
            if k not in seen_keys:
                seen_keys.add(k)
                dedup_failed.append(f)

        for u in unknown:
            k = (u.criterion_type, u.status, u.reason)
            if k not in seen_keys:
                seen_keys.add(k)
                dedup_unknown.append(u)

        # 5. Determine raw match outcome
        if dedup_failed:
            outcome = MatchOutcome.NOT_ELIGIBLE
        elif dedup_unknown or not dedup_passed:
            outcome = MatchOutcome.POTENTIALLY_ELIGIBLE
        else:
            outcome = MatchOutcome.ELIGIBLE

        # 6. Apply Verification Status Constraint:
        # If scholarship is UNVERIFIED, it must NOT be presented as confirmed eligible.
        is_verified = (scholarship.verification_status == VerificationStatus.VERIFIED)
        is_provisional = not is_verified

        if outcome == MatchOutcome.ELIGIBLE and is_provisional:
            outcome = MatchOutcome.POTENTIALLY_ELIGIBLE

        # 7. Construct human-readable explanation
        explanation_parts = []
        if outcome == MatchOutcome.NOT_ELIGIBLE:
            reasons = [f.reason for f in dedup_failed]
            explanation_parts.append(f"Not eligible: {'; '.join(reasons)}.")
        elif outcome == MatchOutcome.POTENTIALLY_ELIGIBLE:
            if is_provisional and dedup_passed and not dedup_unknown:
                explanation_parts.append(
                    "Potentially eligible (Provisional): Student profile satisfies all stated criteria, "
                    "but this scholarship is UNVERIFIED against primary source circulars."
                )
            elif dedup_unknown:
                missing = [u.reason for u in dedup_unknown]
                explanation_parts.append(
                    f"Potentially eligible: Criteria pending additional profile information or manual check: {'; '.join(missing)}."
                )
            else:
                explanation_parts.append("Potentially eligible: Official criteria could not be fully confirmed.")
        else:
            explanation_parts.append(
                f"Eligible: Student profile satisfies all {len(dedup_passed)} verified eligibility condition(s)."
            )

        explanation = " ".join(explanation_parts)

        return ScholarshipMatchResult(
            scholarship_id=scholarship.id,
            scheme_name=scholarship.scheme_name,
            government_authority=scholarship.government_authority,
            verification_status=scholarship.verification_status.value,
            is_provisional=is_provisional,
            match_outcome=outcome,
            passed_criteria=dedup_passed,
            failed_criteria=dedup_failed,
            unknown_criteria=dedup_unknown,
            explanation=explanation,
            official_source_url=str(scholarship.official_source_url),
            scholarship_amount=scholarship.scholarship_amount,
            application_deadline=scholarship.application_deadline,
            evidence_references=scholarship.field_evidence or {},
        )

"""Matching service for ScholarLens.

Orchestrates database queries, evaluates student profiles against scholarship records,
and returns structured, explainable match results.
"""

from typing import List, Optional
from backend.app.db.database import Database, get_db
from backend.app.matching.evaluator import EligibilityEvaluator
from backend.app.schemas.matching import (
    MatchingResponse,
    MatchOutcome,
    ScholarshipMatchResult,
    StudentProfile,
)
from backend.app.schemas.scholarship import VerificationStatus


class MatchingService:
    """Service providing explainable scholarship matching operations."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self.evaluator = EligibilityEvaluator()

    def evaluate_profile(
        self,
        profile: StudentProfile,
        verified_only: bool = False,
        limit: int = 200,
    ) -> MatchingResponse:
        """Evaluate a student profile against all scholarships in the database."""
        scholarships = self.db.get_scholarship_records(limit=limit)

        if verified_only:
            scholarships = [
                s for s in scholarships if s.verification_status == VerificationStatus.VERIFIED
            ]

        results: List[ScholarshipMatchResult] = []
        eligible_count = 0
        potentially_eligible_count = 0
        not_eligible_count = 0
        unverified_count = 0

        for s in scholarships:
            res = self.evaluator.evaluate(s, profile)
            results.append(res)

            if res.is_provisional or res.verification_status != VerificationStatus.VERIFIED.value:
                unverified_count += 1

            if res.match_outcome == MatchOutcome.ELIGIBLE:
                eligible_count += 1
            elif res.match_outcome == MatchOutcome.POTENTIALLY_ELIGIBLE:
                potentially_eligible_count += 1
            elif res.match_outcome == MatchOutcome.NOT_ELIGIBLE:
                not_eligible_count += 1

        # Priority sort:
        # 1. Confirmed ELIGIBLE (verified)
        # 2. POTENTIALLY_ELIGIBLE
        # 3. NOT_ELIGIBLE
        outcome_rank = {
            MatchOutcome.ELIGIBLE: 0,
            MatchOutcome.POTENTIALLY_ELIGIBLE: 1,
            MatchOutcome.NOT_ELIGIBLE: 2,
        }
        results.sort(key=lambda r: (outcome_rank[r.match_outcome], r.is_provisional, r.scheme_name))

        return MatchingResponse(
            total_evaluated=len(results),
            eligible_count=eligible_count,
            potentially_eligible_count=potentially_eligible_count,
            not_eligible_count=not_eligible_count,
            unverified_count=unverified_count,
            results=results,
        )

    def get_confirmed_eligible(
        self,
        profile: StudentProfile,
        limit: int = 200,
    ) -> List[ScholarshipMatchResult]:
        """Return only verified, confirmed eligible scholarship schemes."""
        response = self.evaluate_profile(profile, verified_only=True, limit=limit)
        return [
            r
            for r in response.results
            if r.match_outcome == MatchOutcome.ELIGIBLE
            and not r.is_provisional
            and r.verification_status == VerificationStatus.VERIFIED.value
        ]

"""ScholarLens Matching Package.

Provides deterministic, explainable scholarship matching.
"""

from backend.app.matching.evaluator import EligibilityEvaluator
from backend.app.matching.rules import evaluate_criterion
from backend.app.matching.service import MatchingService

__all__ = [
    "EligibilityEvaluator",
    "MatchingService",
    "evaluate_criterion",
]

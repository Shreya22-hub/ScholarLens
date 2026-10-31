"""Automated tests for ScholarLens frontend helper functions and API integration.

Validates:
1. Input validation and payload construction (bounds checking, enums, optional fields).
2. Result categorization (confirmed eligible, potentially eligible, not eligible, provisional).
3. Display formatting for amounts and deadlines (suspicious amounts flagged, non-ISO dates labeled).
4. API client integration with fallback handling and error messages.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

from frontend.helpers import (
    categorize_match_results,
    evaluate_student_matching,
    format_amount_display,
    format_application_status_display,
    format_deadline_display,
    validate_and_build_profile,
)


class TestFrontendHelpers(unittest.TestCase):
    """Test suite for Streamlit frontend helper functions."""

    def test_validate_and_build_profile_valid(self):
        """Test valid student profile inputs construct schema-compliant payload."""
        is_valid, err, payload = validate_and_build_profile(
            ed_choice="Undergraduate (Degree / B.Tech / MBBS / B.Sc)",
            course_input="B.Tech Computer Science",
            income_num=450000.0,
            marks_num=85.5,
            gender_choice="Female (Girl Student)",
            cat_choice="OBC (Other Backward Classes)",
            state_input="Maharashtra",
            is_pwd=True,
        )
        self.assertTrue(is_valid)
        self.assertIsNone(err)
        self.assertEqual(payload["education_level"], "undergraduate")
        self.assertEqual(payload["course"], "B.Tech Computer Science")
        self.assertEqual(payload["annual_family_income"], 450000.0)
        self.assertEqual(payload["academic_percentage"], 85.5)
        self.assertEqual(payload["gender"], "female")
        self.assertEqual(payload["category"], "obc")
        self.assertEqual(payload["state_of_residence"], "Maharashtra")
        self.assertTrue(payload["is_differently_abled"])

    def test_validate_and_build_profile_invalid_marks(self):
        """Test invalid academic marks percentages (> 100 or < 0) are rejected."""
        is_valid_high, err_high, _ = validate_and_build_profile(
            ed_choice="Undergraduate (Degree / B.Tech / MBBS / B.Sc)",
            marks_num=105.0,
        )
        self.assertFalse(is_valid_high)
        self.assertIn("between 0.0 and 100.0%", err_high)

        is_valid_neg, err_neg, _ = validate_and_build_profile(
            ed_choice="Undergraduate (Degree / B.Tech / MBBS / B.Sc)",
            marks_num=-5.0,
        )
        self.assertFalse(is_valid_neg)
        self.assertIn("between 0.0 and 100.0%", err_neg)

    def test_validate_and_build_profile_negative_income(self):
        """Test negative income is rejected."""
        is_valid, err, _ = validate_and_build_profile(
            ed_choice="Undergraduate (Degree / B.Tech / MBBS / B.Sc)",
            income_num=-50000.0,
        )
        self.assertFalse(is_valid)
        self.assertIn("cannot be negative", err)

    def test_validate_and_build_profile_unspecified_optional_fields(self):
        """Test unspecified choices are omitted from the payload dictionary."""
        is_valid, err, payload = validate_and_build_profile(
            ed_choice="Not Specified",
            course_input="",
            income_num=0.0,
            marks_num=0.0,
            gender_choice="Not Specified",
            cat_choice="Not Specified",
            state_input="",
            is_pwd=False,
        )
        self.assertTrue(is_valid)
        self.assertIsNone(err)
        self.assertEqual(payload, {})

    def test_categorize_match_results(self):
        """Test categorization strictly separates verified eligible from provisional/unverified."""
        results = [
            # Confirmed eligible (verified)
            {
                "scheme_name": "Verified Merit Scholarship",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
            },
            # Provisional / Unverified (even if criteria matched)
            {
                "scheme_name": "Unverified Scheme",
                "match_outcome": "potentially_eligible",
                "verification_status": "unverified",
                "is_provisional": True,
            },
            # Potentially eligible (missing info)
            {
                "scheme_name": "Pending Info Scheme",
                "match_outcome": "potentially_eligible",
                "verification_status": "verified",
                "is_provisional": False,
            },
            # Not eligible
            {
                "scheme_name": "Ineligible Scheme",
                "match_outcome": "not_eligible",
                "verification_status": "verified",
                "is_provisional": False,
            },
        ]

        categorized = categorize_match_results(results)

        # Confirmed eligible must only contain verified schemes
        self.assertEqual(len(categorized["confirmed_eligible"]), 1)
        self.assertEqual(categorized["confirmed_eligible"][0]["scheme_name"], "Verified Merit Scholarship")

        # Potentially eligible contains both pending and provisional
        self.assertEqual(len(categorized["potentially_eligible"]), 2)

        # Not eligible
        self.assertEqual(len(categorized["not_eligible"]), 1)
        self.assertEqual(categorized["not_eligible"][0]["scheme_name"], "Ineligible Scheme")

        # Unverified provisional
        self.assertEqual(len(categorized["unverified_provisional"]), 1)
        self.assertEqual(categorized["unverified_provisional"][0]["scheme_name"], "Unverified Scheme")

    def test_format_deadline_display(self):
        """Test honest representation of dates."""
        # Established ISO date on verified scheme
        disp1, cap1 = format_deadline_display("2026-11-30", "30th November 2026", verification_status="verified")
        self.assertEqual(disp1, "2026-11-30 (ISO)")
        self.assertIn("30th November 2026", cap1)

        # Established ISO date on unverified scheme
        disp_unver, cap_unver = format_deadline_display("2026-11-30", None, verification_status="unverified")
        self.assertEqual(disp_unver, "2026-11-30 (ISO - Unverified)")
        self.assertIn("Unverified", cap_unver)

        # Unstructured raw sentence date (e.g. from Phase 2 / Record 1)
        unstructured = "Closing dates for student application is 31-10-2026"
        disp2, cap2 = format_deadline_display(unstructured, None, verification_status="unverified")
        self.assertIn("Date unverified", disp2)
        self.assertIn("not established in ISO format", cap2)

        # Unknown deadline
        disp3, cap3 = format_deadline_display(None, None)
        self.assertEqual(disp3, "To be announced / Not established")
        self.assertIsNone(cap3)

    def test_format_amount_display_and_suspicious_flags(self):
        """Test honest representation of financial assistance amounts and flagging of suspicious values."""
        # Valid amount on verified scheme
        self.assertEqual(
            format_amount_display("Rs. 50,000 per annum", verification_status="verified"),
            "Rs. 50,000 per annum",
        )

        # Missing amount
        self.assertEqual(format_amount_display(None), "Not verified / Pending official circular")
        self.assertEqual(format_amount_display("   "), "Not verified / Pending official circular")

        # Suspicious amount "Rs 30" with sanity check failure notes (Record ID 1 case)
        notes = "Extracted amount 'Rs 30' failed sanity check (< Rs. 500). Likely a token fee or malformed token."
        flagged_amount = format_amount_display("Rs 30", verification_status="unverified", verification_notes=notes)
        self.assertIn("Failed validation sanity check", flagged_amount)

        # Unverified token amount without notes
        token_amount = format_amount_display("Rs 45", verification_status="unverified")
        self.assertIn("Suspect token amount", token_amount)

        # Normal amount on unverified scheme
        unverified_amount = format_amount_display("Rs. 25,000", verification_status="unverified")
        self.assertEqual(unverified_amount, "Rs. 25,000 (Unverified)")

    def test_evaluate_student_matching_fallback_when_offline(self):
        """Test client falls back to direct service execution if API network request fails and flags fallback."""
        with patch("requests.post", side_effect=requests.ConnectionError("Connection refused")):
            success, err, res = evaluate_student_matching({"education_level": "undergraduate"})
            self.assertTrue(success)
            self.assertIsNone(err)
            self.assertIsNotNone(res)
            self.assertIn("total_evaluated", res)
            self.assertIn("results", res)
            self.assertTrue(res.get("is_fallback"))

    def test_evaluate_student_matching_api_success_flags_non_fallback(self):
        """Test client marks is_fallback=False when live API responds successfully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_evaluated": 1,
            "eligible_count": 0,
            "potentially_eligible_count": 1,
            "not_eligible_count": 0,
            "unverified_count": 1,
            "results": [],
        }
        with patch("requests.post", return_value=mock_resp):
            success, err, res = evaluate_student_matching({"education_level": "undergraduate"})
            self.assertTrue(success)
            self.assertIsNone(err)
            self.assertIsNotNone(res)
            self.assertFalse(res.get("is_fallback"))

    def test_evaluate_student_matching_api_validation_error(self):
        """Test client returns explicit validation message when API returns 422."""
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"detail": "annual_family_income must be >= 0"}
        with patch("requests.post", return_value=mock_resp):
            success, err, res = evaluate_student_matching({"annual_family_income": -100})
            self.assertFalse(success)
            self.assertIn("API Validation Error", err)
            self.assertIsNone(res)

    def test_format_application_status_display(self):
        """Test honest display of application statuses without confusing verification status."""
        badge_open, help_open = format_application_status_display("open", "2026-27")
        self.assertIn("🟢 Open for Applications (2026-27)", badge_open)
        self.assertIn("accepted", help_open)

        badge_unconfirmed, _ = format_application_status_display("deadline_published_status_unknown", "2026-27")
        self.assertIn("🟡 Deadline Published (Status Unconfirmed) (2026-27)", badge_unconfirmed)

        badge_closed, help_closed = format_application_status_display("closed", "2019-2020")
        self.assertIn("🔴 Closed / Past Cycle (2019-2020)", badge_closed)
        self.assertIn("historically", help_closed)

        badge_expired, _ = format_application_status_display("expired")
        self.assertIn("🔴 Expired / Deadline Passed", badge_expired)

        badge_unk, _ = format_application_status_display("unknown")
        self.assertIn("⚪ Status Unknown / Unconfirmed", badge_unk)

        badge_none, _ = format_application_status_display(None)
        self.assertIn("⚪ Status Unknown / Unconfirmed", badge_none)


if __name__ == "__main__":
    unittest.main()

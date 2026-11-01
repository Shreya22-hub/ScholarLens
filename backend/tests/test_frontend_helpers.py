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
    filter_match_results,
    format_amount_display,
    format_application_status_display,
    format_deadline_display,
    get_record_category_label,
    get_record_status_label,
    get_record_verification_label,
    humanize_criterion_type,
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

        # Potentially eligible contains only verified pending schemes (provisional schemes isolated in unverified_provisional)
        self.assertEqual(len(categorized["potentially_eligible"]), 1)
        self.assertEqual(categorized["potentially_eligible"][0]["scheme_name"], "Pending Info Scheme")

        # Not eligible
        self.assertEqual(len(categorized["not_eligible"]), 1)
        self.assertEqual(categorized["not_eligible"][0]["scheme_name"], "Ineligible Scheme")

        # Unverified provisional
        self.assertEqual(len(categorized["unverified_provisional"]), 1)
        self.assertEqual(categorized["unverified_provisional"][0]["scheme_name"], "Unverified Scheme")

        # Mutually exclusive partition check
        total_in_tabs = (
            len(categorized["open_confirmed_eligible"])
            + len(categorized["historical_closed_eligible"])
            + len(categorized["unknown_cycle_eligible"])
            + len(categorized["potentially_eligible"])
            + len(categorized["not_eligible"])
            + len(categorized["unverified_provisional"])
        )
        self.assertEqual(total_in_tabs, len(results))

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
        with patch.dict("os.environ", {"BACKEND_API_URL": "http://backend:8000/"}):
            with patch("requests.post", return_value=mock_resp) as mock_post:
                success, err, res = evaluate_student_matching({"education_level": "undergraduate"})
                mock_post.assert_called_once_with(
                    "http://backend:8000/api/matching/evaluate",
                    json={"education_level": "undergraduate"},
                    timeout=10.0,
                )
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
        self.assertIn("Open for applications (2026-27)", badge_open)
        self.assertIn("accepted", help_open)

        badge_unconfirmed, _ = format_application_status_display("deadline_published_status_unknown", "2026-27")
        self.assertIn("Deadline published (status unconfirmed) (2026-27)", badge_unconfirmed)

        badge_closed, help_closed = format_application_status_display("closed", "2019-2020")
        self.assertIn("Closed / past cycle (2019-2020)", badge_closed)
        self.assertIn("historically", help_closed)

        badge_expired, _ = format_application_status_display("expired")
        self.assertIn("Expired / deadline passed", badge_expired)

        badge_unk, _ = format_application_status_display("unknown")
        self.assertIn("Status unknown / unconfirmed", badge_unk)

        badge_none, _ = format_application_status_display(None)
        self.assertIn("Status unknown / unconfirmed", badge_none)

    def test_categorize_open_verified_opportunity(self):
        """Scenario 1: Verified scholarship with confirmed open status is segregated into open_confirmed_eligible."""
        results = [
            {
                "scheme_name": "AICTE Pragati Open",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "open",
            }
        ]
        categorized = categorize_match_results(results)
        self.assertEqual(len(categorized["open_confirmed_eligible"]), 1)
        self.assertEqual(categorized["open_confirmed_eligible"][0]["scheme_name"], "AICTE Pragati Open")
        self.assertEqual(len(categorized["confirmed_eligible"]), 1)
        self.assertEqual(len(categorized["historical_closed_eligible"]), 0)
        self.assertEqual(len(categorized["unknown_cycle_eligible"]), 0)
        self.assertEqual(len(categorized["unverified_provisional"]), 0)

    def test_categorize_verified_closed_scholarship(self):
        """Scenario 2: Verified scholarship with closed/expired status is segregated into historical_closed_eligible."""
        results = [
            {
                "scheme_name": "AICTE Pragati 2019-20",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "closed",
            },
            {
                "scheme_name": "UGC Ishan Uday 2023-24",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "expired",
            },
        ]
        categorized = categorize_match_results(results)
        self.assertEqual(len(categorized["historical_closed_eligible"]), 2)
        self.assertEqual(len(categorized["open_confirmed_eligible"]), 0)
        self.assertEqual(len(categorized["confirmed_eligible"]), 2)
        self.assertEqual(len(categorized["unknown_cycle_eligible"]), 0)

    def test_categorize_verified_unknown_cycle_scholarship(self):
        """Scenario 3: Verified scholarship with unknown status is segregated into unknown_cycle_eligible."""
        results = [
            {
                "scheme_name": "Sarswati Guideline Scheme",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "deadline_published_status_unknown",
            },
            {
                "scheme_name": "General Standing Guidelines",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "unknown",
            },
        ]
        categorized = categorize_match_results(results)
        self.assertEqual(len(categorized["unknown_cycle_eligible"]), 2)
        self.assertEqual(len(categorized["open_confirmed_eligible"]), 0)
        self.assertEqual(len(categorized["historical_closed_eligible"]), 0)
        self.assertEqual(len(categorized["confirmed_eligible"]), 2)

    def test_categorize_unverified_barred_from_confirmed_opportunities(self):
        """Scenario 4: Unverified scholarship is barred from confirmed/open opportunities even if matching criteria."""
        results = [
            {
                "scheme_name": "Unverified Portal Fellowship",
                "match_outcome": "eligible",
                "verification_status": "unverified",
                "is_provisional": True,
                "application_status": "open",
            }
        ]
        categorized = categorize_match_results(results)
        self.assertEqual(len(categorized["open_confirmed_eligible"]), 0)
        self.assertEqual(len(categorized["confirmed_eligible"]), 0)
        self.assertEqual(len(categorized["unverified_provisional"]), 1)
        self.assertEqual(categorized["unverified_provisional"][0]["scheme_name"], "Unverified Portal Fellowship")

    def test_categorize_empty_confirmed_open_opportunities(self):
        """Scenario 5: Empty confirmed-open opportunities section when all matches are historical, unknown, or unverified."""
        results = [
            {
                "scheme_name": "AICTE Pragati 2019-20",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "closed",
            },
            {
                "scheme_name": "AICTE Saksham Guidelines",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "unknown",
            },
            {
                "scheme_name": "Unverified Record",
                "match_outcome": "potentially_eligible",
                "verification_status": "unverified",
                "is_provisional": True,
                "application_status": "open",
            },
        ]
        categorized = categorize_match_results(results)
        self.assertEqual(len(categorized["open_confirmed_eligible"]), 0)
        self.assertEqual(len(categorized["historical_closed_eligible"]), 1)
        self.assertEqual(len(categorized["unknown_cycle_eligible"]), 1)
        self.assertEqual(len(categorized["unverified_provisional"]), 1)

    def test_student_matching_historical_scholarship_preserves_eligibility_without_claiming_open(self):
        """Scenario 6: Student matching historical criteria is confirmed eligible but segregated from open opportunities."""
        historical_match = {
            "scheme_name": "AICTE Pragati 2019-20",
            "government_authority": "AICTE",
            "match_outcome": "eligible",
            "is_provisional": False,
            "verification_status": "verified",
            "application_status": "closed",
            "scheme_year": "2019-20",
            "passed_criteria": [
                {"criterion_type": "income", "reason": "Income ₹4,00,000 <= ₹8,00,000"},
                {"criterion_type": "gender", "reason": "Gender female matches girl student scheme"},
                {"criterion_type": "education_level", "reason": "Degree level matched"},
            ],
            "failed_criteria": [],
            "unknown_criteria": [],
        }
        categorized = categorize_match_results([historical_match])
        # Student IS confirmed eligible under criteria
        self.assertIn(historical_match, categorized["confirmed_eligible"])
        # Student IS in historical_closed_eligible
        self.assertIn(historical_match, categorized["historical_closed_eligible"])
        # Student is STRICTLY NOT in open_confirmed_eligible
        self.assertNotIn(historical_match, categorized["open_confirmed_eligible"])

    def test_subtabs_are_mutually_exclusive_and_partition_all_records(self):
        """Scenario 7: Ensure each scholarship appears in exactly one primary UI subtab without duplication."""
        mock_results = [
            {"scheme_name": "Open Scheme", "match_outcome": "eligible", "verification_status": "verified", "is_provisional": False, "application_status": "open"},
            {"scheme_name": "Closed Scheme", "match_outcome": "eligible", "verification_status": "verified", "is_provisional": False, "application_status": "closed"},
            {"scheme_name": "Unknown Cycle Scheme", "match_outcome": "eligible", "verification_status": "verified", "is_provisional": False, "application_status": "unknown"},
            {"scheme_name": "Pending Info Scheme", "match_outcome": "potentially_eligible", "verification_status": "verified", "is_provisional": False},
            {"scheme_name": "Ineligible Scheme", "match_outcome": "not_eligible", "verification_status": "verified", "is_provisional": False},
            {"scheme_name": "Unverified Scheme", "match_outcome": "potentially_eligible", "verification_status": "unverified", "is_provisional": True},
        ]
        cat = categorize_match_results(mock_results)

        # Tab 1: Open Opportunities
        t1 = cat["open_confirmed_eligible"]
        # Tab 2: Historical & Unconfirmed Matches
        t2 = cat["historical_closed_eligible"] + cat["unknown_cycle_eligible"]
        # Tab 3: Potentially Eligible (verified pending only)
        t3 = cat["potentially_eligible"]
        # Tab 4: Not Eligible (verified failing only)
        t4 = cat["not_eligible"]
        # Tab 5: Provisional / Unverified (unverified only)
        t5 = cat["unverified_provisional"]

        # Exact counts
        self.assertEqual(len(t1), 1)
        self.assertEqual(len(t2), 2)
        self.assertEqual(len(t3), 1)
        self.assertEqual(len(t4), 1)
        self.assertEqual(len(t5), 1)

        # Confirm strict mutual exclusivity: intersection between any two tabs is empty
        names_t1 = {r["scheme_name"] for r in t1}
        names_t2 = {r["scheme_name"] for r in t2}
        names_t3 = {r["scheme_name"] for r in t3}
        names_t4 = {r["scheme_name"] for r in t4}
        names_t5 = {r["scheme_name"] for r in t5}

        all_tabs = [names_t1, names_t2, names_t3, names_t4, names_t5]
        for i in range(len(all_tabs)):
            for j in range(i + 1, len(all_tabs)):
                overlap = all_tabs[i].intersection(all_tabs[j])
                self.assertEqual(overlap, set(), f"Tabs {i} and {j} overlap on {overlap}")

        # Confirm tab counts sum to evaluated total
        total_tab_records = len(t1) + len(t2) + len(t3) + len(t4) + len(t5)
        self.assertEqual(total_tab_records, len(mock_results))

        # Confirm unverified scheme is ONLY in Tab 5
        self.assertIn("Unverified Scheme", names_t5)
        self.assertNotIn("Unverified Scheme", names_t3)
        self.assertNotIn("Unverified Scheme", names_t1)
        self.assertNotIn("Unverified Scheme", names_t2)
        self.assertNotIn("Unverified Scheme", names_t4)

    def test_humanize_criterion_type(self):
        """Test humanizing raw backend criterion type enums into student-friendly labels."""
        self.assertEqual(humanize_criterion_type("education_level"), "Education Level")
        self.assertEqual(humanize_criterion_type("EDUCATION_LEVEL"), "Education Level")
        self.assertEqual(humanize_criterion_type("academic_marks"), "Academic Marks Requirement")
        self.assertEqual(humanize_criterion_type("income"), "Family Income Ceiling")
        self.assertEqual(humanize_criterion_type("annual_family_income"), "Family Income Ceiling")
        self.assertEqual(humanize_criterion_type("gender"), "Gender Restriction")
        self.assertEqual(humanize_criterion_type("category"), "Social Category")
        self.assertEqual(humanize_criterion_type("disability"), "Disability Benchmark")
        self.assertEqual(humanize_criterion_type("state"), "State / Domicile")
        self.assertEqual(humanize_criterion_type("course"), "Degree / Discipline")
        self.assertEqual(humanize_criterion_type(""), "General Criterion")
        self.assertEqual(humanize_criterion_type(None), "General Criterion")

    def test_get_record_labels(self):
        """Test category, status, and verification classification helpers."""
        r_open = {
            "match_outcome": "eligible",
            "verification_status": "verified",
            "is_provisional": False,
            "application_status": "open",
        }
        self.assertEqual(get_record_category_label(r_open), "Confirmed Open")
        self.assertEqual(get_record_status_label(r_open), "Open")
        self.assertEqual(get_record_verification_label(r_open), "Verified")

        r_hist = {
            "match_outcome": "eligible",
            "verification_status": "verified",
            "is_provisional": False,
            "application_status": "closed",
        }
        self.assertEqual(get_record_category_label(r_hist), "Historical / Unconfirmed")
        self.assertEqual(get_record_status_label(r_hist), "Closed / Past Cycle")

        r_unk = {
            "match_outcome": "eligible",
            "verification_status": "verified",
            "is_provisional": False,
            "application_status": "unknown",
        }
        self.assertEqual(get_record_category_label(r_unk), "Historical / Unconfirmed")
        self.assertEqual(get_record_status_label(r_unk), "Unknown / Unconfirmed")

        r_pot = {
            "match_outcome": "potentially_eligible",
            "verification_status": "verified",
            "is_provisional": False,
        }
        self.assertEqual(get_record_category_label(r_pot), "Potentially Eligible")

        r_not = {
            "match_outcome": "not_eligible",
            "verification_status": "verified",
            "is_provisional": False,
        }
        self.assertEqual(get_record_category_label(r_not), "Not Eligible")

        r_prov = {
            "match_outcome": "eligible",
            "verification_status": "unverified",
            "is_provisional": True,
        }
        self.assertEqual(get_record_category_label(r_prov), "Provisional / Unverified")
        self.assertEqual(get_record_verification_label(r_prov), "Unverified / Provisional")

    def test_filter_match_results_search_and_filters(self):
        """Test client-side search across fields and practical filter combinations."""
        records = [
            {
                "scheme_name": "AICTE Pragati Scholarship Scheme",
                "government_authority": "All India Council for Technical Education (AICTE)",
                "description": "Scholarship for girl students pursuing technical education.",
                "explanation": "Criteria matched: Degree student, female gender, income within ceiling.",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "closed",
                "scheme_year": 2019,
                "course": "undergraduate",
            },
            {
                "scheme_name": "AICTE Saksham Scholarship Scheme",
                "government_authority": "All India Council for Technical Education (AICTE)",
                "description": "Financial assistance for specially-abled students.",
                "explanation": "Criteria matched: Degree student, benchmark disability verified.",
                "match_outcome": "eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "closed",
                "scheme_year": 2019,
                "course": "undergraduate",
            },
            {
                "scheme_name": "AICTE AURA Scheme",
                "government_authority": "All India Council for Technical Education (AICTE)",
                "description": "Augmenting utilization of research assets.",
                "explanation": "Criteria pending review.",
                "match_outcome": "potentially_eligible",
                "verification_status": "verified",
                "is_provisional": False,
                "application_status": "unknown",
                "scheme_year": 2024,
            },
            {
                "scheme_name": "MeitY Post-Doctoral Fellowship",
                "government_authority": "Ministry of Electronics and Information Technology (MeitY)",
                "description": "Fellowship for doctoral and post-doctoral research in electronics.",
                "explanation": "Unverified draft circular.",
                "match_outcome": "eligible",
                "verification_status": "unverified",
                "is_provisional": True,
                "application_status": "open",
            },
        ]

        # 1. Search by name (case-insensitive)
        res_name = filter_match_results(records, query="pragati")
        self.assertEqual(len(res_name), 1)
        self.assertEqual(res_name[0]["scheme_name"], "AICTE Pragati Scholarship Scheme")

        # 2. Search by authority
        res_meity = filter_match_results(records, query="MeitY")
        self.assertEqual(len(res_meity), 1)
        self.assertEqual(res_meity[0]["scheme_name"], "MeitY Post-Doctoral Fellowship")

        # 3. Search by keywords across description / criteria
        res_girl = filter_match_results(records, query="girl")
        self.assertEqual(len(res_girl), 1)

        # 4. Multi-token search (AND matching)
        res_multi = filter_match_results(records, query="AICTE saksham")
        self.assertEqual(len(res_multi), 1)
        self.assertEqual(res_multi[0]["scheme_name"], "AICTE Saksham Scholarship Scheme")

        # 5. Non-matching query
        res_none = filter_match_results(records, query="NonExistentScheme999")
        self.assertEqual(len(res_none), 0)

        # 6. Filter by Application Status
        res_open = filter_match_results(records, status="Open")
        self.assertEqual(len(res_open), 1)
        self.assertEqual(res_open[0]["scheme_name"], "MeitY Post-Doctoral Fellowship")

        res_closed = filter_match_results(records, status="Closed / Past Cycle")
        self.assertEqual(len(res_closed), 2)

        res_unk = filter_match_results(records, status="Unknown / Unconfirmed")
        self.assertEqual(len(res_unk), 1)
        self.assertEqual(res_unk[0]["scheme_name"], "AICTE AURA Scheme")

        # 7. Filter by Verification
        res_veri = filter_match_results(records, verification="Verified")
        self.assertEqual(len(res_veri), 3)

        res_unveri = filter_match_results(records, verification="Unverified / Provisional")
        self.assertEqual(len(res_unveri), 1)

        # 8. Filter by Eligibility Category
        res_cat_hist = filter_match_results(records, category="Historical / Unconfirmed")
        self.assertEqual(len(res_cat_hist), 2)

        res_cat_pot = filter_match_results(records, category="Potentially Eligible")
        self.assertEqual(len(res_cat_pot), 1)

        res_cat_prov = filter_match_results(records, category="Provisional / Unverified")
        self.assertEqual(len(res_cat_prov), 1)

        # 9. Filter by Governing Authority
        res_auth = filter_match_results(records, authority="Ministry of Electronics and Information Technology (MeitY)")
        self.assertEqual(len(res_auth), 1)

        # 10. Combined search and filters
        res_comb = filter_match_results(
            records,
            query="scholarship",
            authority="All India Council for Technical Education (AICTE)",
            status="Closed / Past Cycle",
            verification="Verified",
            category="Historical / Unconfirmed",
        )
        self.assertEqual(len(res_comb), 2)

        # 11. Reset / All defaults returns all records
        res_all = filter_match_results(records)
        self.assertEqual(len(res_all), len(records))


if __name__ == "__main__":
    unittest.main()

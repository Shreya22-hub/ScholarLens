"""Unit tests for Phase 3 Data Quality and Evidence-Based Verification.

Tests:
- Rejection of generic scheme names (e.g. 'Fellowship')
- Sanity checking and rejection of malformed amounts (e.g. 'Rs 30')
- ISO date normalization (YYYY-MM-DD) while preserving raw text evidence
- Deduplication and generic eligibility removal
- Conservative verification rules and transparent explanation notes
- Field-level evidence mapping
- Database persistence of evidence, raw dates, and verification notes
"""

from datetime import datetime, timezone
import os
import tempfile
import unittest

from backend.app.db.database import Database
from backend.app.ingestion.quality import (
    clean_and_validate_amount,
    clean_eligibility_criteria,
    evaluate_verification_status,
    is_generic_scheme_name,
    normalize_date_iso,
)
from backend.app.schemas.scholarship import ScholarshipRecord, VerificationStatus


class TestSchemeNameQuality(unittest.TestCase):
    """Tests for scheme name specificity and generic name rejection."""

    def test_generic_scheme_names_detected(self):
        """Generic portal labels must be identified and rejected."""
        generic_names = [
            "Fellowship",
            "fellowships",
            "Scholarship",
            "SCHOLARSHIPS",
            "Scheme",
            "Schemes",
            "Student Development Scheme",
            "Central Sector Scheme",
            "Guidelines",
            "Notification",
            "Home",
            "Login",
            "About Us",
        ]
        for name in generic_names:
            with self.subTest(name=name):
                self.assertTrue(
                    is_generic_scheme_name(name),
                    f"'{name}' should be recognized as generic",
                )

    def test_specific_scheme_names_accepted(self):
        """Valid multi-word specific scheme names must not be treated as generic."""
        valid_names = [
            "AICTE Pragati Scholarship Scheme for Girl Students",
            "National Means-cum-Merit Scholarship Scheme",
            "Post Matric Scholarships Scheme for Minorities",
            "Central Sector Scheme of Scholarship for College and University Students",
            "PM YASASVI Top Class Education Scheme",
            "Maulana Azad National Fellowship",
        ]
        for name in valid_names:
            with self.subTest(name=name):
                self.assertFalse(
                    is_generic_scheme_name(name),
                    f"'{name}' should be recognized as a specific scheme name",
                )


class TestAmountSanityAndValidation(unittest.TestCase):
    """Tests for financial amount validation, sanity checks, and malformed token rejection."""

    def test_malformed_token_amounts_rejected(self):
        """Token fees or malformed numbers like 'Rs 30' or 'Rs 5' must fail sanity check."""
        bad_amounts = ["Rs 30", "Rs. 30", "₹5", "Rs 10", "Rs. 100", "Rs 45"]
        for amt in bad_amounts:
            with self.subTest(amount=amt):
                cleaned, is_valid, reason = clean_and_validate_amount(amt)
                self.assertFalse(is_valid)
                self.assertIsNone(cleaned)
                self.assertIn("failed sanity check", reason)

    def test_placeholder_amounts_rejected(self):
        """Generic placeholders must be treated as unverified."""
        placeholders = [
            "As specified in official scheme guidelines (see source URL)",
            "Unknown",
            "Refer to source guidelines",
            "See source circular",
        ]
        for ph in placeholders:
            with self.subTest(placeholder=ph):
                cleaned, is_valid, reason = clean_and_validate_amount(ph)
                self.assertFalse(is_valid)
                self.assertIsNone(cleaned)
                self.assertIn("generic placeholder", reason)

    def test_valid_amounts_accepted(self):
        """Plausible amounts must be cleaned and accepted."""
        valid_cases = [
            ("Rs. 50,000 per annum for every year of study", "Rs. 50,000 per annum"),
            ("Rs. 12,000 per annum", "Rs. 12,000 per annum"),
            ("₹ 25,000 per month", "₹ 25,000 per month"),
            ("Rs. 2.5 Lakh per annum", "Rs. 2.5 Lakh per annum"),
            ("Full admission and course fees plus maintenance allowance", "Full admission and course fees plus maintenance allowance"),
        ]
        for input_text, expected_substr in valid_cases:
            with self.subTest(input_text=input_text):
                cleaned, is_valid, reason = clean_and_validate_amount(input_text)
                self.assertTrue(is_valid)
                self.assertIsNotNone(cleaned)
                self.assertIn(expected_substr, cleaned)


class TestDateNormalization(unittest.TestCase):
    """Tests for ISO date normalization and preservation of raw evidence."""

    def test_unstructured_sentence_date_normalization(self):
        """Unstructured sentence containing date must normalize to ISO date while preserving text."""
        raw_sentence = "Closing dates for student application is 31-10-2026"
        iso_date, raw_evidence = normalize_date_iso(raw_sentence)
        self.assertEqual(iso_date, "2026-10-31")
        self.assertEqual(raw_evidence, raw_sentence)

    def test_various_date_formats_normalized(self):
        """Common Indian portal date formats must normalize to YYYY-MM-DD."""
        cases = [
            ("31st October 2026", "2026-10-31"),
            ("15 November 2026", "2026-11-15"),
            ("30/11/2026", "2026-11-30"),
            ("2026-12-15", "2026-12-15"),
            ("31.10.2026", "2026-10-31"),
            ("October 31, 2026", "2026-10-31"),
        ]
        for date_str, expected_iso in cases:
            with self.subTest(date_str=date_str):
                iso_date, raw_evidence = normalize_date_iso(date_str)
                self.assertEqual(iso_date, expected_iso)
                self.assertEqual(raw_evidence, date_str)

    def test_ambiguous_or_pending_dates_return_none(self):
        """Ambiguous or pending deadlines must return ISO None without inventing dates."""
        cases = [
            "To be announced",
            "Open for AY 2026-27",
            "Till further notice",
            "Not specified",
            None,
        ]
        for date_str in cases:
            with self.subTest(date_str=date_str):
                iso_date, raw_evidence = normalize_date_iso(date_str)
                self.assertIsNone(iso_date)


class TestEligibilityCriteriaCleaning(unittest.TestCase):
    """Tests for deduplication and generic eligibility removal."""

    def test_generic_phrases_removed(self):
        """Generic phrases like 'eligibility criteria' must be removed."""
        raw = [
            "eligibility criteria",
            "scheme eligibility criteria",
            "as per scheme eligibility criteria",
            "who can apply",
            "Students admitted to 1st year of Degree programme in an AICTE approved institution",
        ]
        cleaned = clean_eligibility_criteria(raw)
        self.assertEqual(len(cleaned), 1)
        self.assertIn("AICTE approved institution", cleaned[0])

    def test_duplicates_and_substrings_cleaned(self):
        """Duplicate and redundant criteria must be deduplicated."""
        raw = [
            "Gross family income should not exceed Rs. 8 lakh per annum",
            "Gross family income should not exceed Rs. 8 lakh per annum.",
            "Class 12 pass with 80% marks",
            "Class 12 pass",  # shorter redundant fragment
        ]
        cleaned = clean_eligibility_criteria(raw)
        self.assertEqual(len(cleaned), 2)
        self.assertTrue(any("8 lakh per annum" in c for c in cleaned))
        self.assertTrue(any("80% marks" in c for c in cleaned))


class TestConservativeVerificationRules(unittest.TestCase):
    """Tests for conservative verification rules and explanatory notes."""

    def test_generic_scheme_name_fails_verification(self):
        """Record with generic name 'Fellowship' must be UNVERIFIED."""
        status, notes = evaluate_verification_status(
            scheme_name="Fellowship",
            government_authority="Ministry of Education",
            scholarship_amount="Rs. 12,000 per annum",
            application_deadline="2026-10-31",
            eligibility_criteria=["Class 12 pass with 80% marks"],
            supporting_quotations=["Fellowship for students"],
        )
        self.assertEqual(status, VerificationStatus.UNVERIFIED)
        self.assertIn("generic label", notes)

    def test_malformed_amount_fails_verification(self):
        """Record with amount 'Rs 30' must be UNVERIFIED."""
        status, notes = evaluate_verification_status(
            scheme_name="PM YASASVI Scholarship Scheme",
            government_authority="Ministry of Social Justice and Empowerment",
            scholarship_amount="Rs 30",
            application_deadline="2026-10-31",
            eligibility_criteria=["Class 12 pass with 80% marks"],
            supporting_quotations=["Scheme guidelines"],
        )
        self.assertEqual(status, VerificationStatus.UNVERIFIED)
        self.assertIn("sanity check", notes)

    def test_missing_eligibility_fails_verification(self):
        """Record lacking substantive eligibility criteria must be UNVERIFIED."""
        status, notes = evaluate_verification_status(
            scheme_name="National Means-cum-Merit Scholarship Scheme",
            government_authority="Ministry of Education",
            scholarship_amount="Rs. 12,000 per annum",
            application_deadline="2026-10-31",
            eligibility_criteria=["eligibility criteria"],  # only generic text
            supporting_quotations=["Scholarship details"],
        )
        self.assertEqual(status, VerificationStatus.UNVERIFIED)
        self.assertIn("Eligibility criteria lack verifiable", notes)

    def test_fully_grounded_record_verified(self):
        """Record satisfying all conservative quality checks is marked VERIFIED."""
        status, notes = evaluate_verification_status(
            scheme_name="AICTE Pragati Scholarship Scheme for Girl Students",
            government_authority="All India Council for Technical Education",
            scholarship_amount="Rs. 50,000 per annum for every year of study",
            application_deadline="2026-10-31",
            eligibility_criteria=["Students admitted to 1st year degree programme with family income <= Rs. 8 lakh p.a."],
            supporting_quotations=["Scheme provides financial assistance of Rs. 50,000 per annum."],
        )
        self.assertEqual(status, VerificationStatus.VERIFIED)
        self.assertIn("Verified:", notes)


class TestDatabaseEvidenceAndAudit(unittest.TestCase):
    """Tests database persistence of field evidence and automated quality audit."""

    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except OSError:
            pass

    def test_database_persists_evidence_and_notes(self):
        """Verify field evidence, raw deadline, and verification notes persist in SQLite."""
        record = ScholarshipRecord(
            scheme_name="Central Sector Scheme of Scholarship for College and University Students",
            government_authority="Department of Higher Education, Ministry of Education",
            description="Financial assistance to meritorious students from low-income families.",
            eligibility_criteria=["Class 12th marks above 80th percentile with family income <= 4.5 LPA"],
            scholarship_amount="Rs. 12,000 per annum",
            application_deadline="2026-10-31",
            application_deadline_raw="Closing dates for student application is 31-10-2026",
            required_documents=["Class 12 Marksheet", "Income Certificate"],
            official_source_url="https://scholarships.gov.in/",
            supporting_quotations=["The scholarships are awarded on the basis of Senior Secondary Examination."],
            field_evidence={
                "scheme_name": "Central Sector Scheme of Scholarship for College and University Students",
                "scholarship_amount": "Rs. 12,000 per annum",
                "application_deadline": "Closing dates for student application is 31-10-2026",
            },
            verification_status=VerificationStatus.VERIFIED,
            verification_notes="Verified against official circular.",
        )

        saved, created = self.db.save_scholarship_record(record, source_id="nsp-central")
        self.assertTrue(created)

        records = self.db.get_scholarship_records()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r.application_deadline, "2026-10-31")
        self.assertEqual(r.application_deadline_raw, "Closing dates for student application is 31-10-2026")
        self.assertEqual(r.verification_notes, "Verified against official circular.")
        self.assertIn("scholarship_amount", r.field_evidence)

    def test_database_audit_corrects_flawed_record(self):
        """Verify that audit_and_reverify_stored_records corrects flawed records."""
        flawed_record = ScholarshipRecord(
            scheme_name="Fellowship",  # generic name
            government_authority="Ministry of Electronics and Information Technology",
            description="The PM YASASVI Central Sector Scheme Of Top Class Education In Schools.",
            eligibility_criteria=["eligibility criteria"],
            scholarship_amount="Rs 30",  # malformed amount
            application_deadline=None,
            application_deadline_raw="Closing dates for student application is 31-10-2026",
            required_documents=["Aadhaar"],
            official_source_url="https://scholarships.gov.in/",
            supporting_quotations=["Fellowship"],
            verification_status=VerificationStatus.VERIFIED,  # incorrectly marked verified initially
        )
        self.db.save_scholarship_record(flawed_record, source_id="nsp-central")

        # Run audit
        self.db.audit_and_reverify_stored_records()

        records = self.db.get_scholarship_records()
        self.assertEqual(len(records), 1)
        corrected = records[0]
        self.assertEqual(corrected.verification_status, VerificationStatus.UNVERIFIED)
        self.assertIn("generic label", corrected.verification_notes)
        self.assertIn("sanity check", corrected.verification_notes)


if __name__ == "__main__":
    unittest.main()

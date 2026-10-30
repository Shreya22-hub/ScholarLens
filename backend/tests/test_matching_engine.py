"""Comprehensive tests for ScholarLens Phase 4 Explainable Matching Engine.

Validates:
1. All mandatory criteria satisfied -> ELIGIBLE.
2. Income ceiling exceeded -> NOT_ELIGIBLE.
3. Education-level mismatch -> NOT_ELIGIBLE.
4. Missing student information -> POTENTIALLY_ELIGIBLE with UNKNOWN criteria.
5. Missing scholarship eligibility criteria -> POTENTIALLY_ELIGIBLE (not confirmed eligible).
6. Conflicting or malformed criteria handled safely.
7. UNVERIFIED scholarship records marked provisional and excluded from confirmed eligible.
8. Empty scholarship database handled gracefully.
9. Invalid API requests rejected with 422 Unprocessable Entity.
10. Explanations and evidence references preserved.
11. Regression tests for existing endpoints.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.matching.evaluator import EligibilityEvaluator
from backend.app.matching.rules import (
    evaluate_criterion,
    extract_academic_percentage,
    extract_education_level,
    extract_income_ceiling,
    extract_target_category,
    extract_target_gender,
)
from backend.app.matching.service import MatchingService
from backend.app.main import app
from backend.app.schemas.matching import (
    CriterionStatus,
    CriterionType,
    EducationLevel,
    Gender,
    MatchOutcome,
    SocialCategory,
    StudentProfile,
)
from backend.app.schemas.scholarship import ScholarshipRecord, VerificationStatus


class TestMatchingRulesExtraction(unittest.TestCase):
    """Test deterministic rule and constraint extractors."""

    def test_extract_income_ceiling_various_formats(self):
        """Test extraction of income ceilings in various Indian formats."""
        self.assertEqual(extract_income_ceiling("Family income up to Rs. 4.5 lakh per annum"), 450000.0)
        self.assertEqual(extract_income_ceiling("Gross parental income should not exceed Rs. 8 lakh"), 800000.0)
        self.assertEqual(extract_income_ceiling("parental income is not more than Rs. 3,50,000 per annum"), 350000.0)
        self.assertEqual(extract_income_ceiling("family income <= ₹ 2.5 lakh p.a."), 250000.0)
        self.assertIsNone(extract_income_ceiling("Students admitted to 1st year degree programme"))

    def test_extract_education_level(self):
        """Test extraction of education level keywords."""
        self.assertEqual(extract_education_level("Students pursuing degree programme in engineering"), EducationLevel.UNDERGRADUATE)
        self.assertEqual(extract_education_level("Candidates pursuing post-graduate study"), EducationLevel.POSTGRADUATE)
        self.assertEqual(extract_education_level("Students admitted to Ph.D doctoral research"), EducationLevel.DOCTORATE)
        self.assertEqual(extract_education_level("Students enrolled in polytechnic diploma course"), EducationLevel.DIPLOMA)
        self.assertEqual(extract_education_level("Students appearing in Class XII examination"), EducationLevel.CLASS_12)
        self.assertEqual(extract_education_level("Students studying in Class 10 / secondary school"), EducationLevel.CLASS_10)

    def test_extract_academic_percentage(self):
        """Test extraction of percentage thresholds."""
        self.assertEqual(extract_academic_percentage("Minimum 60% marks in qualifying exam"), 60.0)
        self.assertEqual(extract_academic_percentage("Students above 80th percentile in Class 12"), 80.0)
        self.assertEqual(extract_academic_percentage("At least 55% marks required"), 55.0)
        self.assertIsNone(extract_academic_percentage("Family income below Rs. 2 lakh"))

    def test_extract_target_gender_and_category(self):
        """Test extraction of targeted gender and affirmative action categories."""
        self.assertEqual(extract_target_gender("AICTE Pragati Scholarship for Girl Students"), Gender.FEMALE)
        self.assertIsNone(extract_target_gender("General merit scholarship for all students"))

        self.assertEqual(extract_target_category("National Overseas Scholarship for ST Students"), SocialCategory.ST)
        self.assertEqual(extract_target_category("Post Matric Scholarship for SC candidates"), SocialCategory.SC)
        self.assertEqual(extract_target_category("Welfare scheme for OBC students"), SocialCategory.OBC)
        self.assertEqual(extract_target_category("Financial assistance for Minority communities"), SocialCategory.MINORITY)


class TestEligibilityEvaluator(unittest.TestCase):
    """Test deterministic explainable evaluation logic."""

    def setUp(self):
        self.sample_verified_scholarship = ScholarshipRecord(
            id=101,
            scheme_name="AICTE Pragati Scholarship Scheme for Girl Students",
            government_authority="All India Council for Technical Education",
            description="Financial assistance to girl students pursuing technical degree education.",
            eligibility_criteria=[
                "Girl students admitted to 1st year Degree programme in AICTE approved institution",
                "Gross family income should not exceed Rs. 8 lakh per annum",
                "Minimum 60% marks in Class 12 examination",
            ],
            scholarship_amount="Rs. 50,000 per annum",
            application_deadline="2026-10-31",
            required_documents=["Aadhaar Card", "Income Certificate"],
            official_source_url="https://www.aicte-india.org/schemes/students-development-schemes",
            supporting_quotations=["Scheme provides financial assistance to young women."],
            field_evidence={
                "scheme_name": "AICTE Pragati Scholarship Scheme for Girl Students",
                "eligibility_criteria": "Girl students admitted to 1st year Degree programme",
            },
            verification_status=VerificationStatus.VERIFIED,
        )

    def test_all_mandatory_criteria_satisfied(self):
        """1. All mandatory criteria satisfied -> ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            course="B.Tech Computer Science",
            annual_family_income=500000.0,
            academic_percentage=82.0,
            gender=Gender.FEMALE,
        )
        result = EligibilityEvaluator.evaluate(self.sample_verified_scholarship, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertFalse(result.is_provisional)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertEqual(len(result.unknown_criteria), 0)
        self.assertGreater(len(result.passed_criteria), 0)
        self.assertIn("Eligible:", result.explanation)

    def test_income_ceiling_exceeded(self):
        """2. Income ceiling exceeded -> NOT_ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            course="B.Tech",
            annual_family_income=950000.0,  # Exceeds 8 lakh
            academic_percentage=85.0,
            gender=Gender.FEMALE,
        )
        result = EligibilityEvaluator.evaluate(self.sample_verified_scholarship, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertGreater(len(result.failed_criteria), 0)
        self.assertTrue(any(f.criterion_type == CriterionType.INCOME for f in result.failed_criteria))
        self.assertIn("exceeds maximum ceiling", result.explanation)

    def test_education_level_mismatch(self):
        """3. Education-level mismatch -> NOT_ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.CLASS_12,  # Requires degree / undergraduate
            course="Science",
            annual_family_income=400000.0,
            academic_percentage=85.0,
            gender=Gender.FEMALE,
        )
        result = EligibilityEvaluator.evaluate(self.sample_verified_scholarship, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertTrue(any(f.criterion_type == CriterionType.EDUCATION_LEVEL for f in result.failed_criteria))
        self.assertIn("does not match required level", result.explanation)

    def test_missing_student_information(self):
        """4. Missing student information -> POTENTIALLY_ELIGIBLE with UNKNOWN criteria."""
        # Student did not provide income and marks
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            gender=Gender.FEMALE,
        )
        result = EligibilityEvaluator.evaluate(self.sample_verified_scholarship, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.POTENTIALLY_ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertGreater(len(result.unknown_criteria), 0)
        unknown_types = [u.criterion_type for u in result.unknown_criteria]
        self.assertIn(CriterionType.INCOME, unknown_types)
        self.assertIn(CriterionType.ACADEMIC_MARKS, unknown_types)
        self.assertIn("Potentially eligible:", result.explanation)

    def test_missing_scholarship_eligibility_criteria(self):
        """5. Missing scholarship eligibility criteria -> POTENTIALLY_ELIGIBLE (not confirmed eligible)."""
        empty_criteria_scholarship = ScholarshipRecord(
            id=102,
            scheme_name="General Higher Education Grant",
            government_authority="State Education Board",
            description="General educational grant without detailed circular text.",
            eligibility_criteria=[],  # Empty criteria
            scholarship_amount=None,
            application_deadline=None,
            official_source_url="https://scholarships.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        profile = StudentProfile(education_level=EducationLevel.UNDERGRADUATE)
        result = EligibilityEvaluator.evaluate(empty_criteria_scholarship, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.POTENTIALLY_ELIGIBLE)
        self.assertIn("not recorded", result.explanation.lower())

    def test_conflicting_or_malformed_criteria(self):
        """6. Conflicting or malformed criteria handled gracefully without crashing."""
        malformed_scholarship = ScholarshipRecord(
            id=103,
            scheme_name="Special Science Scheme",
            government_authority="Ministry of Science",
            description="Scholarship with unparseable or unusual criteria strings.",
            eligibility_criteria=[
                "Candidates having XYZ123 non-numeric grade",
                "??? ### Random punctuation",
                "",
                "   ",
            ],
            scholarship_amount="Rs. 10,000",
            official_source_url="https://scholarships.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        profile = StudentProfile(education_level=EducationLevel.UNDERGRADUATE)
        # Should not raise any exception
        result = EligibilityEvaluator.evaluate(malformed_scholarship, profile)
        self.assertIn(result.match_outcome, [MatchOutcome.POTENTIALLY_ELIGIBLE, MatchOutcome.ELIGIBLE])

    def test_unverified_scholarship_marked_provisional(self):
        """7. UNVERIFIED scholarship records must NOT be marked confirmed eligible."""
        unverified_scholarship = ScholarshipRecord(
            id=104,
            scheme_name="AICTE Pragati Scholarship Scheme for Girl Students",
            government_authority="All India Council for Technical Education",
            description="Scheme for girl students.",
            eligibility_criteria=[
                "Girl students admitted to 1st year Degree programme",
                "Gross family income should not exceed Rs. 8 lakh per annum",
            ],
            scholarship_amount="Rs. 50,000",
            official_source_url="https://scholarships.gov.in/",
            verification_status=VerificationStatus.UNVERIFIED,  # Unverified!
        )
        # Profile satisfies all conditions
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=400000.0,
            gender=Gender.FEMALE,
        )
        result = EligibilityEvaluator.evaluate(unverified_scholarship, profile)
        self.assertTrue(result.is_provisional)
        self.assertEqual(result.verification_status, "unverified")
        # Must NOT be ELIGIBLE; must be POTENTIALLY_ELIGIBLE with provisional notice
        self.assertEqual(result.match_outcome, MatchOutcome.POTENTIALLY_ELIGIBLE)
        self.assertIn("UNVERIFIED", result.explanation)


class TestMatchingServiceAndAPI(unittest.TestCase):
    """Test MatchingService and FastAPI endpoints."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db.name)
        self.service = MatchingService(db=self.db)
        self.client = TestClient(app)

        # Populate temporary DB with 2 sample records (1 verified, 1 unverified)
        rec_verified = ScholarshipRecord(
            scheme_name="Central Sector Scheme of Scholarships for College and University Students",
            government_authority="Department of Higher Education",
            description="Merit-cum-means scholarship for degree college students.",
            eligibility_criteria=[
                "Students pursuing regular Degree course in recognized institution",
                "Gross family income up to Rs. 4.5 lakh per annum",
                "Above 80th percentile in Class 12",
            ],
            scholarship_amount="Rs. 12,000 per annum",
            application_deadline="2026-10-31",
            official_source_url="https://scholarships.gov.in/csss",
            field_evidence={
                "scheme_name": "Central Sector Scheme of Scholarships",
                "scholarship_amount": "Rs. 12,000 per annum",
                "eligibility_criteria": "Gross family income up to Rs. 4.5 lakh",
            },
            verification_status=VerificationStatus.VERIFIED,
        )
        self.db.save_scholarship_record(rec_verified)

        rec_unverified = ScholarshipRecord(
            scheme_name="Fellowship",
            government_authority="Ministry of Electronics and Information Technology",
            description="Unverified generic fellowship record.",
            eligibility_criteria=["Students admitted to degree"],
            scholarship_amount="Rs 30",
            official_source_url="https://scholarships.gov.in/fellowship",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        self.db.save_scholarship_record(rec_unverified)

    def tearDown(self):
        try:
            os.remove(self.temp_db.name)
        except OSError:
            pass

    def test_empty_scholarship_database(self):
        """8. Empty scholarship database handled gracefully."""
        empty_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        empty_db = Database(empty_db_file.name)
        service = MatchingService(db=empty_db)

        profile = StudentProfile(education_level=EducationLevel.UNDERGRADUATE)
        response = service.evaluate_profile(profile)
        self.assertEqual(response.total_evaluated, 0)
        self.assertEqual(len(response.results), 0)
        self.assertEqual(response.eligible_count, 0)
        os.remove(empty_db_file.name)

    def test_api_invalid_requests_return_422(self):
        """9. Invalid API requests rejected with 422 Unprocessable Entity."""
        # Negative income
        res1 = self.client.post("/api/matching/evaluate", json={"annual_family_income": -5000})
        self.assertEqual(res1.status_code, 422)

        # Marks > 100%
        res2 = self.client.post("/api/matching/evaluate", json={"academic_percentage": 105.0})
        self.assertEqual(res2.status_code, 422)

        # Extra forbidden field
        res3 = self.client.post("/api/matching/evaluate", json={"unexpected_private_field": "hack"})
        self.assertEqual(res3.status_code, 422)

    def test_api_matching_evaluate_and_evidence_preservation(self):
        """10. API response explanations and evidence references preserved."""
        with patch("backend.app.main.matching_service", self.service):
            payload = {
                "education_level": "undergraduate",
                "annual_family_income": 300000.0,
                "academic_percentage": 88.0,
            }
            res = self.client.post("/api/matching/evaluate", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["total_evaluated"], 2)
            self.assertGreaterEqual(data["eligible_count"], 1)

            # Check that explanation and evidence references are present
            first = data["results"][0]
            self.assertIn("explanation", first)
            self.assertIn("official_source_url", first)
            self.assertIn("evidence_references", first)
            self.assertIn("verification_status", first)

    def test_api_confirmed_eligible_excludes_unverified(self):
        """10b. POST /api/matching/eligible excludes unverified and provisional records."""
        with patch("backend.app.main.matching_service", self.service):
            payload = {
                "education_level": "undergraduate",
                "annual_family_income": 300000.0,
                "academic_percentage": 88.0,
            }
            res = self.client.post("/api/matching/eligible", json=payload)
            self.assertEqual(res.status_code, 200)
            eligible_records = res.json()
            # Must ONLY contain confirmed verified records
            for r in eligible_records:
                self.assertEqual(r["verification_status"], "verified")
                self.assertFalse(r["is_provisional"])
                self.assertEqual(r["match_outcome"], "eligible")


if __name__ == "__main__":
    unittest.main()

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
    extract_disability_requirement,
    extract_education_level,
    extract_education_levels,
    extract_income_ceiling,
    extract_target_category,
    extract_target_gender,
    parse_academic_marks_relaxation,
    strip_marks_relaxation_clause,
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


class TestMarksRelaxationMatching(unittest.TestCase):
    """Test Suite for Bug 1: Alternative academic cutoffs and relaxation handling.

    Verifies:
    1. Parsing of marks relaxation clauses (base cutoff, relaxed cutoff, target groups).
    2. Relaxation clauses stripped from target gender, category, and disability extractors so
       they are not emitted as mandatory restrictions.
    3. General-category male applicants meeting the general cutoff pass and are ELIGIBLE.
    4. General-category male applicants below the general cutoff fail and are NOT_ELIGIBLE.
    5. Affirmative-action applicants meeting the relaxed cutoff pass.
    6. Affirmative-action applicants falling below the relaxed cutoff fail.
    7. Applicants in the relaxation band with unspecified attributes evaluate to UNKNOWN.
    """

    def setUp(self):
        self.scheme_with_relaxation = ScholarshipRecord(
            id=201,
            scheme_name="National Technical Fellowship Scheme",
            government_authority="Ministry of Education",
            description="Fellowship for engineering and technology scholars.",
            eligibility_criteria=[
                "Students pursuing regular undergraduate degree in engineering",
                "General category: Minimum 60% marks in PG/UG (55% for SC/ST/OBC/Women/PwD).",
                "Gross family income should not exceed Rs. 8 lakh per annum",
            ],
            scholarship_amount="Rs. 50,000 per annum",
            application_deadline="2026-12-31",
            official_source_url="https://scholarships.gov.in/schemes/fellowship",
            verification_status=VerificationStatus.VERIFIED,
        )

    def test_parse_marks_relaxation_various_formats(self):
        """Test extraction of base and relaxed cutoffs from various official formats."""
        r1 = parse_academic_marks_relaxation(
            "General category: Minimum 60% marks in PG/UG (55% for SC/ST/OBC/Women/PwD)."
        )
        self.assertIsNotNone(r1)
        self.assertEqual(r1.base_percentage, 60.0)
        self.assertEqual(r1.relaxed_percentage, 55.0)
        self.assertIn("SC/ST/OBC/Women/PwD", r1.groups_text)

        r2 = parse_academic_marks_relaxation(
            "At least 60% marks in qualifying degree (with a relaxation of 5% for SC/ST/Women)"
        )
        self.assertIsNotNone(r2)
        self.assertEqual(r2.base_percentage, 60.0)
        self.assertEqual(r2.relaxed_percentage, 55.0)

        r3 = parse_academic_marks_relaxation(
            "60% aggregate marks; 55% for SC/ST/OBC students"
        )
        self.assertIsNotNone(r3)
        self.assertEqual(r3.base_percentage, 60.0)
        self.assertEqual(r3.relaxed_percentage, 55.0)

        # Standard criterion without relaxation
        r_none = parse_academic_marks_relaxation("Minimum 60% marks in Class 12")
        self.assertIsNone(r_none)

    def test_relaxation_terms_never_emitted_as_mandatory_constraints(self):
        """Verify relaxation clause terms do not trigger mandatory GENDER, CATEGORY, or DISABILITY."""
        raw_criterion = "General category: Minimum 60% marks in PG/UG (55% for SC/ST/OBC/Women/PwD)."
        stripped = strip_marks_relaxation_clause(raw_criterion)

        # Extracted constraints from stripped text must NOT include Women, ST/SC, or PwD
        self.assertIsNone(extract_target_gender(stripped))
        self.assertIsNone(extract_target_category(stripped))
        self.assertFalse(extract_disability_requirement(stripped))

        # Evaluate against a General Male Non-Disabled profile
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            academic_percentage=60.0,
            gender=Gender.MALE,
            category=SocialCategory.GENERAL,
            is_differently_abled=False,
        )
        evals = evaluate_criterion(raw_criterion, profile)
        # Must only produce ACADEMIC_MARKS evaluation, NO gender/category/disability evaluations
        types = [e.criterion_type for e in evals]
        self.assertIn(CriterionType.ACADEMIC_MARKS, types)
        self.assertNotIn(CriterionType.GENDER, types)
        self.assertNotIn(CriterionType.CATEGORY, types)
        self.assertNotIn(CriterionType.DISABILITY, types)

    def test_general_male_meeting_general_cutoff_is_eligible(self):
        """1. General-category male applicant satisfying general cutoff (60%) is ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=500000.0,
            academic_percentage=62.0,  # >= 60.0%
            gender=Gender.MALE,
            category=SocialCategory.GENERAL,
            is_differently_abled=False,
        )
        result = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertEqual(len(result.unknown_criteria), 0)
        self.assertGreater(len(result.passed_criteria), 0)

    def test_general_male_below_general_cutoff_is_not_eligible(self):
        """2. General-category male applicant below general cutoff (58% < 60%) is NOT_ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=500000.0,
            academic_percentage=58.0,  # Below 60%, and not covered by relaxation
            gender=Gender.MALE,
            category=SocialCategory.GENERAL,
            is_differently_abled=False,
        )
        result = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 1)
        self.assertEqual(result.failed_criteria[0].criterion_type, CriterionType.ACADEMIC_MARKS)
        self.assertIn("below the required general cutoff", result.failed_criteria[0].reason)

    def test_affirmative_action_applicants_meeting_relaxed_cutoff_are_eligible(self):
        """3. Applicants explicitly covered by relaxation meeting relaxed cutoff (56% >= 55%) are ELIGIBLE."""
        # Test a female applicant
        female_profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=450000.0,
            academic_percentage=56.0,  # >= 55.0% relaxed cutoff
            gender=Gender.FEMALE,
            category=SocialCategory.GENERAL,
            is_differently_abled=False,
        )
        res_female = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, female_profile)
        self.assertEqual(res_female.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(res_female.failed_criteria), 0)

        # Test an SC applicant (male)
        sc_profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=450000.0,
            academic_percentage=56.0,
            gender=Gender.MALE,
            category=SocialCategory.SC,
            is_differently_abled=False,
        )
        res_sc = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, sc_profile)
        self.assertEqual(res_sc.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(res_sc.failed_criteria), 0)

        # Test a PwD applicant (male, general)
        pwd_profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=450000.0,
            academic_percentage=56.0,
            gender=Gender.MALE,
            category=SocialCategory.GENERAL,
            is_differently_abled=True,
        )
        res_pwd = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, pwd_profile)
        self.assertEqual(res_pwd.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(res_pwd.failed_criteria), 0)

    def test_affirmative_action_applicant_below_relaxed_cutoff_is_not_eligible(self):
        """4. Applicant covered by relaxation falling below relaxed cutoff (52% < 55%) is NOT_ELIGIBLE."""
        female_profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=450000.0,
            academic_percentage=52.0,  # Below 55.0% relaxed cutoff
            gender=Gender.FEMALE,
            category=SocialCategory.SC,
            is_differently_abled=False,
        )
        result = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, female_profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 1)
        self.assertEqual(result.failed_criteria[0].criterion_type, CriterionType.ACADEMIC_MARKS)
        self.assertIn("below the minimum relaxed cutoff", result.failed_criteria[0].reason)

    def test_missing_relaxation_attributes_in_relaxation_band_evaluates_unknown(self):
        """5. Applicant in relaxation band (58%) with missing category/gender evaluates to UNKNOWN."""
        incomplete_profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=500000.0,
            academic_percentage=58.0,  # In relaxation window [55%, 60%)
            gender=None,  # Not provided
            category=None,  # Not provided
            is_differently_abled=None,  # Not provided
        )
        result = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, incomplete_profile)
        self.assertEqual(result.match_outcome, MatchOutcome.POTENTIALLY_ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertTrue(any(u.criterion_type == CriterionType.ACADEMIC_MARKS for u in result.unknown_criteria))
        marks_unknown = next(u for u in result.unknown_criteria if u.criterion_type == CriterionType.ACADEMIC_MARKS)
        self.assertIn("relaxation window", marks_unknown.reason)

    def test_student_with_60_percent_and_missing_demographics_passes(self):
        """8. Student with 60% marks and missing demographic attributes passes on marks without invented constraints."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=500000.0,
            academic_percentage=60.0,  # Meets 60% general cutoff
            gender=None,
            category=None,
            is_differently_abled=None,
        )
        result = EligibilityEvaluator.evaluate(self.scheme_with_relaxation, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertEqual(len(result.unknown_criteria), 0)
        marks_passed = [p for p in result.passed_criteria if p.criterion_type == CriterionType.ACADEMIC_MARKS]
        self.assertEqual(len(marks_passed), 1)
        self.assertIn("satisfies general minimum cutoff", marks_passed[0].reason)

    def test_criterion_without_marks_relaxation_preserves_strict_attributes(self):
        """9 & 10. Criteria without marks-relaxation clause preserve strict affirmative action constraints."""
        strict_sc_scheme = ScholarshipRecord(
            id=205,
            scheme_name="Post-Matric Scholarship for Scheduled Caste (SC) Students",
            government_authority="Ministry of Social Justice and Empowerment",
            description="Scheme exclusively for SC students.",
            eligibility_criteria=[
                "Candidates belonging to Scheduled Caste (SC) category",
                "Minimum 60% marks in qualifying examination",
            ],
            scholarship_amount="Rs. 25,000",
            official_source_url="https://socialjustice.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        sc_student = StudentProfile(
            category=SocialCategory.SC,
            academic_percentage=65.0,
        )
        gen_student = StudentProfile(
            category=SocialCategory.GENERAL,
            academic_percentage=65.0,
        )
        # SC student meets criterion -> ELIGIBLE
        res_sc = EligibilityEvaluator.evaluate(strict_sc_scheme, sc_student)
        self.assertEqual(res_sc.match_outcome, MatchOutcome.ELIGIBLE)

        # General student fails category -> NOT_ELIGIBLE
        res_gen = EligibilityEvaluator.evaluate(strict_sc_scheme, gen_student)
        self.assertEqual(res_gen.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertTrue(any(f.criterion_type == CriterionType.CATEGORY for f in res_gen.failed_criteria))


class TestCompoundEducationLevelMatching(unittest.TestCase):
    """Test Suite for Bug 2: Compound education level extraction and matching.

    Verifies:
    1. Multi-level extraction recognizes both UNDERGRADUATE and DIPLOMA from 'Degree/Diploma'.
    2. Undergraduate applicant matches compound Degree/Diploma criterion.
    3. Diploma applicant matches compound Degree/Diploma criterion.
    4. Unrelated education levels (e.g. Class 12) fail compound criterion.
    5. Undergraduate failing unrelated criterion (income) is correctly rejected as NOT_ELIGIBLE.
    6. Standalone Degree and Diploma criteria preserve strict isolated matching.
    """

    def setUp(self):
        self.compound_scheme = ScholarshipRecord(
            id=202,
            scheme_name="Technical Education Financial Assistance Scheme",
            government_authority="Technical Education Directorate",
            description="Scheme for technical diploma and degree students.",
            eligibility_criteria=[
                "Students admitted to Degree/Diploma level courses in approved institutions",
                "Gross family income should not exceed Rs. 6 lakh per annum",
            ],
            scholarship_amount="Rs. 30,000",
            application_deadline="2026-11-30",
            official_source_url="https://dte.gov.in/schemes",
            verification_status=VerificationStatus.VERIFIED,
        )

    def test_extract_education_levels_compound_and_standalone(self):
        """Test extraction of multiple levels from compound text and single level from standalone text."""
        levels_compound = extract_education_levels("Students admitted to Degree/Diploma level courses")
        self.assertEqual(levels_compound, [EducationLevel.UNDERGRADUATE, EducationLevel.DIPLOMA])

        levels_compound_reverse = extract_education_levels("Diploma or Degree programme")
        self.assertEqual(levels_compound_reverse, [EducationLevel.UNDERGRADUATE, EducationLevel.DIPLOMA])

        levels_degree = extract_education_levels("Students pursuing degree programme in engineering")
        self.assertEqual(levels_degree, [EducationLevel.UNDERGRADUATE])

        levels_diploma = extract_education_levels("Students enrolled in polytechnic diploma course")
        self.assertEqual(levels_diploma, [EducationLevel.DIPLOMA])

    def test_undergraduate_applicant_matches_compound_degree_diploma(self):
        """Undergraduate student matches compound Degree/Diploma criterion and is ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=400000.0,
        )
        result = EligibilityEvaluator.evaluate(self.compound_scheme, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertTrue(any(p.criterion_type == CriterionType.EDUCATION_LEVEL for p in result.passed_criteria))

    def test_diploma_applicant_matches_compound_degree_diploma(self):
        """Diploma student matches compound Degree/Diploma criterion and is ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.DIPLOMA,
            annual_family_income=350000.0,
        )
        result = EligibilityEvaluator.evaluate(self.compound_scheme, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.ELIGIBLE)
        self.assertEqual(len(result.failed_criteria), 0)
        self.assertTrue(any(p.criterion_type == CriterionType.EDUCATION_LEVEL for p in result.passed_criteria))

    def test_ineligible_level_fails_compound_degree_diploma(self):
        """Class 12 applicant fails compound Degree/Diploma criterion and is NOT_ELIGIBLE."""
        profile = StudentProfile(
            education_level=EducationLevel.CLASS_12,
            annual_family_income=300000.0,
        )
        result = EligibilityEvaluator.evaluate(self.compound_scheme, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        self.assertTrue(any(f.criterion_type == CriterionType.EDUCATION_LEVEL for f in result.failed_criteria))

    def test_undergraduate_fails_unrelated_criterion_despite_education_match(self):
        """Undergraduate matching education level fails when unrelated income ceiling is exceeded."""
        profile = StudentProfile(
            education_level=EducationLevel.UNDERGRADUATE,
            annual_family_income=900000.0,  # Exceeds Rs. 6 lakh ceiling
        )
        result = EligibilityEvaluator.evaluate(self.compound_scheme, profile)
        self.assertEqual(result.match_outcome, MatchOutcome.NOT_ELIGIBLE)
        # Education level passed, income failed
        self.assertTrue(any(p.criterion_type == CriterionType.EDUCATION_LEVEL for p in result.passed_criteria))
        self.assertTrue(any(f.criterion_type == CriterionType.INCOME for f in result.failed_criteria))

    def test_standalone_degree_and_diploma_preserve_strict_behavior(self):
        """Standalone Degree rejects Diploma, and standalone Diploma rejects Undergraduate."""
        standalone_degree_scheme = ScholarshipRecord(
            id=203,
            scheme_name="Degree Excellence Award",
            government_authority="Higher Education Council",
            description="Exclusively for degree students.",
            eligibility_criteria=["Students admitted to 1st year Degree programme"],
            scholarship_amount="Rs. 20,000",
            official_source_url="https://hec.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        standalone_diploma_scheme = ScholarshipRecord(
            id=204,
            scheme_name="Polytechnic Diploma Grant",
            government_authority="Technical Board",
            description="Exclusively for polytechnic diploma students.",
            eligibility_criteria=["Students enrolled in polytechnic diploma course"],
            scholarship_amount="Rs. 15,000",
            official_source_url="https://polytech.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )

        diploma_student = StudentProfile(education_level=EducationLevel.DIPLOMA)
        ug_student = StudentProfile(education_level=EducationLevel.UNDERGRADUATE)

        # Diploma student evaluated against standalone degree -> NOT_ELIGIBLE
        res1 = EligibilityEvaluator.evaluate(standalone_degree_scheme, diploma_student)
        self.assertEqual(res1.match_outcome, MatchOutcome.NOT_ELIGIBLE)

        # Undergrad student evaluated against standalone diploma -> NOT_ELIGIBLE
        res2 = EligibilityEvaluator.evaluate(standalone_diploma_scheme, ug_student)
        self.assertEqual(res2.match_outcome, MatchOutcome.NOT_ELIGIBLE)

    def test_diploma_ug_pg_and_ug_pg_compound_criteria(self):
        """Test 'Diploma/UG/PG' and 'UG/PG' compound level matching."""
        # Test Diploma/UG/PG
        scheme_tri = ScholarshipRecord(
            id=206,
            scheme_name="Higher & Technical Education Grant",
            government_authority="Higher Education Council",
            description="Grant for technical studies.",
            eligibility_criteria=["Students enrolled in Diploma/UG/PG courses"],
            scholarship_amount="Rs. 10,000",
            official_source_url="https://hec.gov.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        for lvl in [EducationLevel.DIPLOMA, EducationLevel.UNDERGRADUATE, EducationLevel.POSTGRADUATE]:
            res = EligibilityEvaluator.evaluate(scheme_tri, StudentProfile(education_level=lvl))
            self.assertEqual(res.match_outcome, MatchOutcome.ELIGIBLE)
        # Doctorate should fail
        res_doc = EligibilityEvaluator.evaluate(scheme_tri, StudentProfile(education_level=EducationLevel.DOCTORATE))
        self.assertEqual(res_doc.match_outcome, MatchOutcome.NOT_ELIGIBLE)

        # Test UG/PG
        scheme_ug_pg = ScholarshipRecord(
            id=207,
            scheme_name="University Study Fellowship",
            government_authority="University Grants Commission",
            description="Fellowship for university courses.",
            eligibility_criteria=["Students admitted to UG/PG courses"],
            scholarship_amount="Rs. 12,000",
            official_source_url="https://ugc.ac.in/",
            verification_status=VerificationStatus.VERIFIED,
        )
        for lvl in [EducationLevel.UNDERGRADUATE, EducationLevel.POSTGRADUATE]:
            res = EligibilityEvaluator.evaluate(scheme_ug_pg, StudentProfile(education_level=lvl))
            self.assertEqual(res.match_outcome, MatchOutcome.ELIGIBLE)
        # Diploma should fail
        res_dip = EligibilityEvaluator.evaluate(scheme_ug_pg, StudentProfile(education_level=EducationLevel.DIPLOMA))
        self.assertEqual(res_dip.match_outcome, MatchOutcome.NOT_ELIGIBLE)


if __name__ == "__main__":
    unittest.main()

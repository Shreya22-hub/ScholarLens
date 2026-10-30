"""Basic unit tests for ScholarLens Pydantic scholarship schemas.

Tests validation rules, defaults, required fields, and URL constraints.
"""

from datetime import datetime
import unittest
from pydantic import ValidationError

from backend.app.schemas.scholarship import (
    ScholarshipRecord,
    ScholarshipSource,
    VerificationStatus,
)


class TestScholarshipRecordSchema(unittest.TestCase):
    """Test suite for ScholarshipRecord Pydantic model."""

    def setUp(self):
        """Prepare sample valid data for testing."""
        self.valid_data = {
            "scheme_name": "Central Sector Scheme of Scholarship for College and University Students",
            "government_authority": "Department of Higher Education, Ministry of Education",
            "description": "Financial assistance to meritorious students from low-income families.",
            "eligibility_criteria": [
                "Class 12th marks above 80th percentile",
                "Gross family income <= Rs. 4.5 LPA",
            ],
            "scholarship_amount": "Rs. 12,000 per annum for graduation",
            "application_deadline": "31st October 2026",
            "required_documents": [
                "Class 12 Marksheet",
                "Income Certificate",
                "Aadhaar Card",
            ],
            "official_source_url": "https://scholarships.gov.in/",
            "supporting_quotations": [
                "The scholarships are awarded on the basis of results of Senior Secondary Examination."
            ],
            "last_verified_timestamp": "2026-10-30T12:00:00Z",
            "verification_status": "unverified",
        }

    def test_valid_scholarship_record_creation(self):
        """Test instantiation with complete valid scholarship data."""
        record = ScholarshipRecord(**self.valid_data)
        self.assertEqual(
            record.scheme_name,
            "Central Sector Scheme of Scholarship for College and University Students",
        )
        self.assertEqual(
            record.government_authority,
            "Department of Higher Education, Ministry of Education",
        )
        self.assertEqual(str(record.official_source_url), "https://scholarships.gov.in/")
        self.assertEqual(record.verification_status, VerificationStatus.UNVERIFIED)
        self.assertEqual(len(record.eligibility_criteria), 2)
        self.assertEqual(len(record.required_documents), 3)
        self.assertEqual(len(record.supporting_quotations), 1)

    def test_default_values(self):
        """Test default values for optional and list fields."""
        minimal_data = {
            "scheme_name": "Post Matric Scholarships Scheme for Minorities",
            "government_authority": "Ministry of Minority Affairs",
            "description": "Scholarship for students belonging to minority communities.",
            "scholarship_amount": "Admission and course fees plus maintenance allowance",
            "official_source_url": "https://scholarships.gov.in/",
        }
        record = ScholarshipRecord(**minimal_data)
        self.assertEqual(record.verification_status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(record.last_verified_timestamp)
        self.assertIsNone(record.application_deadline)
        self.assertEqual(record.eligibility_criteria, [])
        self.assertEqual(record.required_documents, [])
        self.assertEqual(record.supporting_quotations, [])

    def test_missing_required_fields_raise_validation_error(self):
        """Test that omitting required fields raises ValidationError."""
        required_fields = [
            "scheme_name",
            "government_authority",
            "description",
            "scholarship_amount",
            "official_source_url",
        ]
        for field in required_fields:
            with self.subTest(missing_field=field):
                data = self.valid_data.copy()
                del data[field]
                with self.assertRaises(ValidationError):
                    ScholarshipRecord(**data)

    def test_empty_string_validation(self):
        """Test that empty or whitespace-only text raises ValidationError."""
        empty_targets = [
            ("scheme_name", "   "),
            ("government_authority", ""),
            ("description", " "),
            ("scholarship_amount", "    "),
        ]
        for field, bad_value in empty_targets:
            with self.subTest(field=field):
                data = self.valid_data.copy()
                data[field] = bad_value
                with self.assertRaises(ValidationError):
                    ScholarshipRecord(**data)

    def test_invalid_url_raises_validation_error(self):
        """Test that invalid URL format fails validation."""
        bad_urls = [
            "not-a-url",
            "ftp://invalid-protocol.gov.in",
            "scholarships.gov.in",  # missing scheme
        ]
        for bad_url in bad_urls:
            with self.subTest(bad_url=bad_url):
                data = self.valid_data.copy()
                data["official_source_url"] = bad_url
                with self.assertRaises(ValidationError):
                    ScholarshipRecord(**data)

    def test_list_cleaning_filter_empty_items(self):
        """Test that list fields strip extra whitespace and filter blank items."""
        data = self.valid_data.copy()
        data["eligibility_criteria"] = ["  Criterion A  ", "", "   ", "Criterion B"]
        record = ScholarshipRecord(**data)
        self.assertEqual(record.eligibility_criteria, ["Criterion A", "Criterion B"])

    def test_verification_status_enum_values(self):
        """Test that valid and invalid verification statuses are handled correctly."""
        for status in ["unverified", "pending", "verified", "flagged", "expired"]:
            data = self.valid_data.copy()
            data["verification_status"] = status
            record = ScholarshipRecord(**data)
            self.assertEqual(record.verification_status.value, status)

        with self.assertRaises(ValidationError):
            data = self.valid_data.copy()
            data["verification_status"] = "invalid_status"
            ScholarshipRecord(**data)

    def test_alias_last_verified(self):
        """Test that last_verified alias populates last_verified_timestamp."""
        data = self.valid_data.copy()
        del data["last_verified_timestamp"]
        data["last_verified"] = "2026-10-30T10:00:00Z"
        record = ScholarshipRecord(**data)
        self.assertIsInstance(record.last_verified_timestamp, datetime)

    def test_model_serialization(self):
        """Test serializing model to dictionary and JSON."""
        record = ScholarshipRecord(**self.valid_data)
        record_dict = record.model_dump(mode="json")
        self.assertIsInstance(record_dict, dict)
        self.assertEqual(record_dict["scheme_name"], self.valid_data["scheme_name"])
        self.assertEqual(record_dict["official_source_url"], "https://scholarships.gov.in/")


class TestScholarshipSourceSchema(unittest.TestCase):
    """Test suite for ScholarshipSource configuration model."""

    def test_valid_source_creation(self):
        """Test creating a valid ScholarshipSource."""
        source_data = {
            "id": "nsp-portal",
            "name": "National Scholarship Portal",
            "authority": "MeitY",
            "url": "https://scholarships.gov.in/",
            "category": "Central Government Portal",
            "is_active": True,
            "verified": False,
            "verification_status": "unverified",
        }
        source = ScholarshipSource(**source_data)
        self.assertEqual(source.id, "nsp-portal")
        self.assertFalse(source.verified)
        self.assertEqual(str(source.url), "https://scholarships.gov.in/")

    def test_source_invalid_url(self):
        """Test ScholarshipSource rejects invalid URL."""
        with self.assertRaises(ValidationError):
            ScholarshipSource(
                id="invalid",
                name="Invalid Source",
                authority="None",
                url="just-a-string",
            )


if __name__ == "__main__":
    unittest.main()

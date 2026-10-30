"""Unit and integration tests for ScholarLens Phase 2 Ingestion Pipeline.

Tests:
- Source registry loading and schema validation
- HTML extraction with verbatim quotations
- PDF extraction
- Invalid/incomplete scholarship handling
- SQLite duplicate prevention
- Failed HTTP requests and robots.txt compliance
- Ingestion telemetry and status reporting
- Restriction against arbitrary URL crawling
"""

from datetime import datetime, timezone
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError

from backend.app.db.database import Database
from backend.app.ingestion.extractors.html_extractor import HTMLExtractor
from backend.app.ingestion.extractors.pdf_extractor import PDFExtractor
from backend.app.ingestion.fetcher import FetchResult, PoliteFetcher
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.registry import SourceRegistry
from backend.app.main import app
from backend.app.schemas.scholarship import (
    IngestionRunRecord,
    ScholarshipRecord,
    ScholarshipSource,
    SourceType,
    VerificationStatus,
)


class TestSourceRegistry(unittest.TestCase):
    """Tests for Source Registry loading, validation, and field coverage."""

    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)
        self.registry = SourceRegistry(config_path="config/sources.json", db=self.db)

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except OSError:
            pass

    def test_sources_loaded_and_fields_present(self):
        """Verify all configured sources have required registry fields."""
        sources = self.registry.get_all_sources()
        self.assertGreaterEqual(len(sources), 5)

        for s in sources:
            # 1. Source name & authority
            self.assertTrue(bool(s.name))
            self.assertTrue(bool(s.authority))
            # 2. Official URL
            self.assertTrue(str(s.url).startswith("http"))
            # 3. Source type (HTML or PDF)
            self.assertIn(s.source_type, [SourceType.HTML, SourceType.PDF])
            # 4. Enabled status
            self.assertIsInstance(s.is_active, bool)
            # 5. Last checked timestamp (None or datetime)
            self.assertTrue(s.last_checked_timestamp is None or isinstance(s.last_checked_timestamp, datetime))
            # 6. Verification status
            self.assertIn(s.verification_status, list(VerificationStatus))
            # 7. Last error (None or str)
            self.assertTrue(s.last_error is None or isinstance(s.last_error, str))

    def test_get_enabled_sources(self):
        """Verify enabled sources filtering."""
        enabled = self.registry.get_enabled_sources()
        self.assertTrue(all(s.is_active for s in enabled))

    def test_lookup_and_arbitrary_rejection(self):
        """Verify lookup of registered source and rejection of arbitrary sources."""
        self.assertTrue(self.registry.is_source_registered("nsp-central"))
        self.assertFalse(self.registry.is_source_registered("random-unauthorized-url-target"))


class TestExtractors(unittest.TestCase):
    """Tests for HTML and PDF text and scholarship extraction."""

    def test_html_clean_text_and_extraction(self):
        """Test HTML parsing extracts scheme details and verbatim quotes."""
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head><title>AICTE Schemes</title></head>
        <body>
            <header><nav><a href="/home">Home</a></nav></header>
            <h1>AICTE Pragati Scholarship Scheme for Girl Students</h1>
            <p>Department of Higher Education, Ministry of Education, Government of India</p>
            <div>
                <p>Scheme provides financial assistance to young women pursuing technical education.</p>
                <p>Eligibility: Students admitted to 1st year of Degree programme in an AICTE approved institution. Gross family income should not exceed Rs. 8 lakh per annum.</p>
                <p>Scholarship Amount: Rs. 50,000 per annum for every year of study.</p>
                <p>Application deadline is 31st October 2026 for online submission.</p>
                <p>Mandatory documents include Aadhaar Card, Income Certificate, and Class 12 Marksheet.</p>
            </div>
            <footer><p>Copyright 2026 AICTE</p></footer>
        </body>
        </html>
        """
        clean_text = HTMLExtractor.extract_clean_text(sample_html)
        self.assertIn("AICTE Pragati Scholarship", clean_text)
        self.assertNotIn("Home", clean_text)  # nav ignored

        candidates = HTMLExtractor.extract_scholarships_from_html(
            html_content=sample_html,
            source_url="https://www.aicte-india.org/schemes/students-development-schemes",
            default_authority="All India Council for Technical Education",
        )
        self.assertEqual(len(candidates), 1)
        rec = candidates[0]
        self.assertIn("Pragati", rec["scheme_name"])
        self.assertIn("50,000", rec["scholarship_amount"])
        self.assertEqual(rec["application_deadline"], "2026-10-31")
        self.assertIn("31st October 2026", rec["application_deadline_raw"])
        self.assertGreater(len(rec["eligibility_criteria"]), 0)
        self.assertGreater(len(rec["supporting_quotations"]), 0)

        # Confirm verbatim quote is a real substring in clean text
        quote = rec["supporting_quotations"][0]
        self.assertIn(quote, clean_text)

    def test_html_extraction_returns_empty_when_no_scholarship_content(self):
        """Test extractor does not hallucinate scholarships on unrelated pages."""
        login_html = "<html><body><h1>User Login</h1><p>Please enter your credentials to access the system.</p></body></html>"
        candidates = HTMLExtractor.extract_scholarships_from_html(
            html_content=login_html,
            source_url="https://example.gov.in/login",
        )
        self.assertEqual(candidates, [])

    def test_pdf_extraction_with_synthetic_content(self):
        """Test PDF extraction on synthetic content."""
        sample_text = (
            "Guidelines for Central Sector Scheme of Scholarship\n"
            "Ministry of Education, Government of India\n"
            "The objective is to provide financial assistance to meritorious students.\n"
            "Eligibility: Students with family income less than Rs. 4.5 lakh per annum.\n"
            "Scholarship Amount: Rs. 12,000 per annum for undergraduate study.\n"
            "Application deadline: 30th November 2026.\n"
            "Documents: Aadhaar Card, Income Certificate.\n"
        )
        with patch.object(PDFExtractor, "extract_clean_text", return_value=sample_text):
            candidates = PDFExtractor.extract_scholarships_from_pdf(
                pdf_bytes=b"fake-pdf-bytes",
                source_url="https://www.education.gov.in/guidelines.pdf",
                default_authority="Ministry of Education",
            )
            self.assertEqual(len(candidates), 1)
            rec = candidates[0]
            self.assertIn("Central Sector Scheme", rec["scheme_name"])
            self.assertIn("12,000", rec["scholarship_amount"])
            self.assertGreater(len(rec["supporting_quotations"]), 0)


class TestStorageAndDuplicatePrevention(unittest.TestCase):
    """Tests SQLite persistence and duplicate prevention."""

    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except OSError:
            pass

    def test_duplicate_prevention_on_same_url_and_scheme(self):
        """Verify that repeatedly saving the same record updates instead of creating duplicates."""
        record = ScholarshipRecord(
            scheme_name="Post Matric Scholarship for SC Students",
            government_authority="Ministry of Social Justice and Empowerment",
            description="Centrally sponsored scheme for post-matriculation study.",
            eligibility_criteria=["Annual family income <= Rs. 2.5 Lakh"],
            scholarship_amount="Compulsory non-refundable fees plus maintenance allowance",
            application_deadline="31-10-2026",
            required_documents=["Caste Certificate", "Income Certificate"],
            official_source_url="https://scholarships.gov.in/",
            supporting_quotations=["Centrally sponsored scheme implemented through State Governments."],
            verification_status=VerificationStatus.UNVERIFIED,
        )

        saved1, created1 = self.db.save_scholarship_record(record, source_id="nsp-central")
        self.assertTrue(created1)

        # Retrieve records
        records_initial = self.db.get_scholarship_records()
        self.assertEqual(len(records_initial), 1)

        # Save again with updated amount
        record.scholarship_amount = "Revised: Full fees reimbursement"
        saved2, created2 = self.db.save_scholarship_record(record, source_id="nsp-central")
        self.assertFalse(created2)  # Updated, not created
        self.assertEqual(saved2.id, saved1.id)

        # Verify still only 1 record exists in database
        records_after = self.db.get_scholarship_records()
        self.assertEqual(len(records_after), 1)
        self.assertEqual(records_after[0].scholarship_amount, "Revised: Full fees reimbursement")


class TestPoliteFetcherAndHTTPFailures(unittest.TestCase):
    """Tests polite fetcher error handling and robots.txt compliance with mocked HTTP."""

    def test_robots_txt_disallow_blocks_fetch(self):
        """Test fetcher aborts when robots.txt disallows crawling."""
        fetcher = PoliteFetcher(respect_robots=True)

        def mock_handle_request(request: httpx.Request) -> httpx.Response:
            if "robots.txt" in str(request.url):
                return httpx.Response(200, text="User-agent: *\nDisallow: /restricted/")
            return httpx.Response(200, text="Restricted Content")

        transport = httpx.MockTransport(mock_handle_request)
        with httpx.Client(transport=transport) as mock_client:
            result = fetcher.fetch(
                url="https://portal.gov.in/restricted/scheme.html",
                source_id="test-portal",
                client=mock_client,
            )
            self.assertFalse(result.success)
            self.assertFalse(result.robots_allowed)
            self.assertIn("robots.txt", result.error_message)

    def test_http_403_forbidden_recorded_without_bypass(self):
        """Test fetcher gracefully handles 403 Forbidden without attempting to bypass."""
        fetcher = PoliteFetcher(respect_robots=False)

        def mock_handle_request(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden: Access Denied")

        transport = httpx.MockTransport(mock_handle_request)
        with httpx.Client(transport=transport) as mock_client:
            result = fetcher.fetch(
                url="https://portal.gov.in/schemes",
                source_id="test-portal",
                client=mock_client,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.status_code, 403)
            self.assertIn("Access restricted", result.error_message)

    def test_http_429_rate_limit_handled(self):
        """Test fetcher stops on 429 Too Many Requests."""
        fetcher = PoliteFetcher(respect_robots=False)

        def mock_handle_request(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Too Many Requests")

        transport = httpx.MockTransport(mock_handle_request)
        with httpx.Client(transport=transport) as mock_client:
            result = fetcher.fetch(
                url="https://portal.gov.in/schemes",
                source_id="test-portal",
                client=mock_client,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.status_code, 429)
            self.assertIn("Rate limit", result.error_message)

    def test_network_timeout_recorded(self):
        """Test fetcher records timeout error properly."""
        fetcher = PoliteFetcher(respect_robots=False, max_retries=1)

        def mock_handle_request(request: httpx.Request):
            raise httpx.ReadTimeout("Connection timed out", request=request)

        transport = httpx.MockTransport(mock_handle_request)
        with httpx.Client(transport=transport) as mock_client:
            result = fetcher.fetch(
                url="https://portal.gov.in/schemes",
                source_id="test-portal",
                client=mock_client,
            )
            self.assertFalse(result.success)
            self.assertIn("ReadTimeout", result.error_message)


class TestIngestionPipelineAndAPI(unittest.TestCase):
    """End-to-end ingestion pipeline execution and API tests with mocked HTTP."""

    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)
        self.registry = SourceRegistry(config_path="config/sources.json", db=self.db)
        self.fetcher = PoliteFetcher(respect_robots=False)
        self.pipeline = IngestionPipeline(registry=self.registry, fetcher=self.fetcher, db=self.db)
        self.client = TestClient(app)

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except OSError:
            pass

    def test_pipeline_successful_ingestion_and_telemetry(self):
        """Test full pipeline cycle: fetch -> extract -> validate -> store -> telemetry."""
        sample_page = """
        <html><body>
        <h1>National Means-cum-Merit Scholarship Scheme</h1>
        <p>Department of School Education and Literacy, Ministry of Education</p>
        <p>The scheme provides scholarships to meritorious students of economically weaker sections.</p>
        <p>Eligibility: Students studying in Class IX whose parental income is not more than Rs. 3,50,000 per annum.</p>
        <p>Scholarship Amount: Rs. 12,000 per annum (Rs. 1000 per month).</p>
        <p>Application deadline: 15th November 2026.</p>
        </body></html>
        """

        def mock_handle_request(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sample_page, headers={"content-type": "text/html"})

        transport = httpx.MockTransport(mock_handle_request)
        with httpx.Client(transport=transport) as mock_client:
            res = self.pipeline.ingest_source("nsp-central", client=mock_client)
            self.assertTrue(res["success"])
            self.assertGreaterEqual(res["records_extracted"], 1)

            # Check database records
            records = self.db.get_scholarship_records()
            self.assertGreaterEqual(len(records), 1)
            self.assertIn("Means-cum-Merit", records[0].scheme_name)

            # Check source telemetry
            updated_source = self.registry.get_source("nsp-central")
            self.assertIsNotNone(updated_source.last_checked_timestamp)
            self.assertIsNone(updated_source.last_error)

            # Check ingestion run record in DB
            runs = self.db.get_recent_ingestion_runs()
            self.assertGreaterEqual(len(runs), 1)
            self.assertEqual(runs[0].source_id, "nsp-central")
            self.assertEqual(runs[0].status_code, 200)

    def test_arbitrary_url_or_unknown_source_rejected_by_api(self):
        """Test API refuses to ingest arbitrary URLs or unregistered IDs."""
        res = self.client.post("/api/ingest", json={"source_id": "malicious-arbitrary-site"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("not configured", res.json()["detail"])

    def test_sources_api_returns_configured_portals(self):
        """Test GET /api/sources returns list of configured sources."""
        res = self.client.get("/api/sources")
        self.assertEqual(res.status_code, 200)
        sources = res.json()
        self.assertGreaterEqual(len(sources), 5)
        self.assertTrue(any(s["id"] == "nsp-central" for s in sources))


if __name__ == "__main__":
    unittest.main()

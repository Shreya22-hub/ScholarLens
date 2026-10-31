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
import re
import sqlite3
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
from backend.app.ingestion.link_follower import fetch_guideline_document, is_allowed_guideline_url
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.quality import determine_application_status, evaluate_verification_status
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

    def test_html_table_extraction_valid_row(self):
        """Test extracting valid, fully grounded scholarship rows from structured HTML tables."""
        table_html = """
        <!DOCTYPE html>
        <html>
        <body>
            <h1>Official Scholarship Schemes</h1>
            <table class="scheme-directory">
                <thead>
                    <tr>
                        <th>S.No.</th>
                        <th>Name of Scheme</th>
                        <th>Government Authority</th>
                        <th>Eligibility Criteria</th>
                        <th>Scholarship Amount</th>
                        <th>Application Deadline</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>1</td>
                        <td>National Means-cum-Merit Scholarship Scheme</td>
                        <td>Department of School Education and Literacy</td>
                        <td>Students of Class 9 with parental income less than Rs. 3.5 lakh per annum and minimum 55% marks in Class 7.</td>
                        <td>Rs. 12,000 per annum</td>
                        <td>31-10-2026</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        candidates = HTMLExtractor.extract_scholarships_from_html(
            html_content=table_html,
            source_url="https://scholarships.gov.in/schemes",
            default_authority="Ministry of Education",
        )
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand["scheme_name"], "National Means-cum-Merit Scholarship Scheme")
        self.assertEqual(cand["government_authority"], "Department of School Education and Literacy")
        self.assertEqual(cand["scholarship_amount"], "Rs. 12,000 per annum")
        self.assertEqual(cand["application_deadline"], "2026-10-31")
        self.assertGreaterEqual(len(cand["eligibility_criteria"]), 1)
        self.assertIn("income less than Rs. 3.5 lakh", cand["eligibility_criteria"][0])

        # Verify field evidence grounding
        evidence = cand.get("field_evidence", {})
        self.assertIn("scheme_name", evidence)
        self.assertIn("government_authority", evidence)
        self.assertIn("scholarship_amount", evidence)
        self.assertIn("eligibility_criteria", evidence)

        # Verify that candidate can pass conservative verification check
        status, reason = evaluate_verification_status(
            scheme_name=cand["scheme_name"],
            government_authority=cand["government_authority"],
            scholarship_amount=cand["scholarship_amount"],
            application_deadline=cand["application_deadline"],
            eligibility_criteria=cand["eligibility_criteria"],
            supporting_quotations=cand["supporting_quotations"],
            field_evidence=cand["field_evidence"],
        )
        self.assertEqual(status, VerificationStatus.VERIFIED)
        self.assertIn("Verified:", reason)

    def test_html_table_extraction_rejects_header_and_navigation_tables(self):
        """Test table extractor ignores navigation, login, empty, and generic-scheme tables."""
        table_html = """
        <!DOCTYPE html>
        <html>
        <body>
            <!-- Unrelated navigation table -->
            <table class="nav-table">
                <tr><td><a href="/login">Student Login</a></td><td><a href="/register">Register</a></td></tr>
                <tr><td><a href="/contact">Contact Us</a></td><td><a href="/faq">FAQs</a></td></tr>
            </table>

            <!-- Table with generic label and repeated headers -->
            <table class="schemes">
                <tr><th>S.No.</th><th>Scheme</th><th>Authority</th></tr>
                <tr><th>S.No.</th><th>Scheme</th><th>Authority</th></tr>
                <tr><td>1</td><td>Fellowship</td><td>UGC</td></tr>
                <tr><td></td><td></td><td></td></tr>
            </table>
        </body>
        </html>
        """
        candidates = HTMLExtractor.extract_scholarships_from_html(
            html_content=table_html,
            source_url="https://example.gov.in/schemes",
        )
        self.assertEqual(candidates, [])

    def test_html_table_extraction_missing_fields_remain_unverified(self):
        """Test candidate with missing or ambiguous fields in table remains UNVERIFIED."""
        table_html = """
        <!DOCTYPE html>
        <html>
        <body>
            <table>
                <tr>
                    <th>Scheme Name</th>
                    <th>Authority</th>
                    <th>Eligibility</th>
                    <th>Amount</th>
                    <th>Deadline</th>
                </tr>
                <tr>
                    <td>Incomplete Research Support Scholarship Scheme</td>
                    <td>Ministry of Education</td>
                    <td>General eligibility conditions apply.</td>
                    <td>Rs 30</td>
                    <td>TBA</td>
                </tr>
            </table>
        </body>
        </html>
        """
        candidates = HTMLExtractor.extract_scholarships_from_html(
            html_content=table_html,
            source_url="https://example.gov.in/schemes",
        )
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        # Rs 30 fails sanity check, so amount_final is None
        self.assertIsNone(cand["scholarship_amount"])

        status, reason = evaluate_verification_status(
            scheme_name=cand["scheme_name"],
            government_authority=cand["government_authority"],
            scholarship_amount=cand["scholarship_amount"],
            application_deadline=cand["application_deadline"],
            eligibility_criteria=cand["eligibility_criteria"],
            supporting_quotations=cand["supporting_quotations"],
            field_evidence=cand["field_evidence"],
        )
        self.assertEqual(status, VerificationStatus.UNVERIFIED)
        self.assertIn("Amount is not specified", reason)


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


class TestSafeLinkFollowingAndGuidelineExtraction(unittest.TestCase):
    """Regression tests for Phase 6 official guideline retrieval and verification."""

    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)
        self.fetcher = PoliteFetcher(respect_robots=False)
        self.registry = SourceRegistry(config_path="config/sources.json", db=self.db)
        self.pipeline = IngestionPipeline(registry=self.registry, fetcher=self.fetcher, db=self.db)

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except OSError:
            pass

    def test_is_allowed_guideline_url_accepts_official_domains(self):
        """Test only verified government and source domains are permitted."""
        allowed_urls = [
            ("https://aicte.gov.in/downloads/schemes/guidelines.pdf", "https://www.aicte.gov.in/schemes/students-development-schemes"),
            ("https://www.aicte.gov.in/downloads/schemes/notification.pdf", "https://www.aicte.gov.in/schemes/students-development-schemes"),
            ("https://scholarships.gov.in/downloads/scheme_guidelines.pdf", "https://scholarships.gov.in/"),
            ("https://education.gov.in/sites/upload_files/mhrd/files/guidelines.pdf", "https://education.gov.in/scholarships"),
            ("https://tribal.nic.in/downloads/circular.pdf", "https://tribal.nic.in/"),
        ]
        for url, source in allowed_urls:
            allowed, reason = is_allowed_guideline_url(url, source_url=source)
            self.assertTrue(allowed, f"URL {url} should be allowed: {reason}")
            self.assertIsNone(reason)

    def test_is_allowed_guideline_url_rejects_third_party_and_unverified_domains(self):
        """Test external, commercial, and third-party domains are rejected."""
        disallowed_urls = [
            ("https://www.mitacs.ca/programs/globalink.pdf", "https://www.aicte.gov.in/schemes"),
            ("https://commercial-coaching.com/scholarships.pdf", "https://scholarships.gov.in/"),
            ("http://127.0.0.1/exploit.pdf", "https://aicte.gov.in/"),
            ("http://localhost:8000/test.pdf", "https://aicte.gov.in/"),
            ("ftp://aicte.gov.in/downloads/scheme.pdf", "https://aicte.gov.in/"),
            ("https://fake-gov-portal.com/schemes.pdf", "https://aicte.gov.in/"),
        ]
        for url, source in disallowed_urls:
            allowed, reason = is_allowed_guideline_url(url, source_url=source)
            self.assertFalse(allowed, f"URL {url} should be blocked")
            self.assertIsNotNone(reason)

    def test_fetch_guideline_document_blocks_external_domain(self):
        """Test fetching rejects third-party external links before network transmission."""
        result = fetch_guideline_document(
            url="https://www.mitacs.ca/programs/internship.pdf",
            source_id="aicte-schemes",
            fetcher=self.fetcher,
            source_url="https://www.aicte.gov.in/schemes/students-development-schemes",
        )
        self.assertFalse(result.success)
        self.assertIn("Access denied", result.error_message)

    def test_fetch_guideline_document_blocks_oversized_files(self):
        """Test fetching rejects documents exceeding maximum size (15MB)."""
        def mock_oversized_stream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf", "content-length": "20000000"},
                content=b"%PDF" + b"0" * 1000,
            )

        transport = httpx.MockTransport(mock_oversized_stream)
        with httpx.Client(transport=transport) as mock_client:
            res = fetch_guideline_document(
                url="https://aicte.gov.in/downloads/massive.pdf",
                source_id="aicte-schemes",
                fetcher=self.fetcher,
                source_url="https://aicte.gov.in/schemes",
                client=mock_client,
                max_size_bytes=1000,
            )
            self.assertFalse(res.success)
            self.assertIn("maximum permitted size", res.error_message)

    def test_guideline_link_extraction_from_html_containers(self):
        """Test extracting guideline document links associated with scheme containers."""
        html_page = """
        <html><body>
        <div class="schemeListItem">
            <h4 class="headingText">AICTE SARSWATI SCHOLARSHIP 2024</h4>
            <div class="schemeDetails">
                <p class="schemeDetailsText">A scholarship scheme for meritorious girls.</p>
                <a class="schemeLinkBtn" href="https://aicte.gov.in/downloads/schemes/Sarswati Notification 2026-27.pdf">AICTE Sarswati Notification 2026-27</a>
                <a class="schemeLinkBtn" href="https://sarswati.aicte.gov.in/login.php">Portal Link</a>
            </div>
        </div>
        <div class="schemeListItem">
            <h4 class="headingText">Mitacs Globalink Research Internship</h4>
            <div class="schemeDetails">
                <a href="https://www.mitacs.ca/program.pdf">Info PDF</a>
            </div>
        </div>
        </body></html>
        """
        links = HTMLExtractor.extract_guideline_links(
            html_content=html_page,
            base_url="https://www.aicte.gov.in/schemes/students-development-schemes",
        )
        self.assertEqual(len(links), 2)
        sarswati_link = next(l for l in links if "Sarswati" in l["url"])
        self.assertEqual(sarswati_link["scheme_name"], "AICTE SARSWATI SCHOLARSHIP 2024")
        self.assertTrue(sarswati_link["is_pdf"])

    def test_determine_application_status_expired_deadline(self):
        """Test expired deadline accurately detected and not represented as open."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        status, year = determine_application_status(
            deadline_iso="2026-10-16",
            raw_text="Extension of last date for submission of online applications under APDF Scheme: 16 October, 2026",
            reference_date=ref_date,
        )
        self.assertEqual(status, "expired")

    def test_determine_application_status_future_deadline_without_confirmed_opening(self):
        """Test future deadline without confirmed opening date produces DEADLINE_PUBLISHED_STATUS_UNKNOWN."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        status, year = determine_application_status(
            deadline_iso="2027-04-30",
            raw_text="The last date for submission of online applications is 30th April 2027 for Academic Year 2026-27.",
            reference_date=ref_date,
        )
        self.assertEqual(status, "deadline_published_status_unknown")
        self.assertEqual(year, "2026-27")

    def test_determine_application_status_explicitly_open(self):
        """Test explicitly open applications (active window or explicit open statement)."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        # Case 1: Verified open student window (opening_date <= today <= student_closing_date)
        status, year = determine_application_status(
            deadline_iso="2026-11-30",
            raw_text=(
                "Opening of portal for students: 15th October 2026\n"
                "Last date for submission of online applications by students: 30th November 2026\n"
                "Academic Year 2026-27"
            ),
            reference_date=ref_date,
        )
        self.assertEqual(status, "open")
        self.assertEqual(year, "2026-27")

        # Case 2: Explicit open announcement statement
        status2, year2 = determine_application_status(
            deadline_iso="2027-03-31",
            raw_text="Portal is now open for receiving fresh applications for 2026-27. Last date: 31st March 2027.",
            reference_date=ref_date,
        )
        self.assertEqual(status2, "open")
        self.assertEqual(year2, "2026-27")

    def test_determine_application_status_verification_date_not_inferred_as_open(self):
        """Test future institute verification date does not falsely infer student applications are open."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        # Sarswati 2026-27 schedule with opening date and institute verification, but no student submission deadline
        status, year = determine_application_status(
            deadline_iso=None,
            raw_text=(
                "AICTE SARSWATI SCHOLARSHIP SCHEME FOR A/Y 2026-27\n"
                "1. Opening of portal for students (Fresh and Renewal): 15th October 2026\n"
                "2. The last date for verification of applications by Institutes (L1 Level): 31st January 2027\n"
                "3. The Last date Verification of applications at State Nodal Officer: 15th February 2027\n"
                "4. Portal to be closed on 28th February 2027\n"
            ),
            reference_date=ref_date,
        )
        # Must NOT infer open from future verification date!
        self.assertEqual(status, "unknown")
        self.assertEqual(year, "2026-27")

    def test_determine_application_status_missing_dates(self):
        """Test missing dates returns unknown without guessing."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        status, year = determine_application_status(
            deadline_iso=None,
            raw_text="General guidelines for AICTE scholarships. No dates announced yet.",
            reference_date=ref_date,
        )
        self.assertEqual(status, "unknown")
        self.assertIsNone(year)

    def test_determine_application_status_conflicting_status_evidence(self):
        """Test conflicting open and closed statements produce unknown status."""
        ref_date = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        status, year = determine_application_status(
            deadline_iso="2027-02-28",
            raw_text="Notice: Applications are now open for fresh candidates. However, portal is closed for this session.",
            reference_date=ref_date,
        )
        self.assertEqual(status, "unknown")

    def test_pdf_extraction_with_full_grounding_verified(self):
        """Test PDF circular with all required claims grounded achieves VERIFIED status."""
        pdf_text = (
            "ALL INDIA COUNCIL FOR TECHNICAL EDUCATION (A STATUTORY BODY OF THE GOVERNMENT OF INDIA)\n"
            "Nelson Mandela Marg, Vasant Kunj, New Delhi-110070\n"
            "AICTE POST-DOCTORAL FELLOWSHIP (APDF)\n"
            "Candidates who have been awarded the Ph.D. degree from recognized University in Engineering and Technology.\n"
            "1 Fellowship Rs. 80,000 /-per month plus applicable HRA\n"
            "2 Research Grant Rs. 2,00,000/- per annum\n"
            "Last date of submission was 16 October 2026.\n"
        )
        with patch.object(PDFExtractor, "extract_clean_text", return_value=pdf_text):
            candidates = PDFExtractor.extract_scholarships_from_pdf(
                pdf_bytes=b"%PDF-1.4 mock",
                source_url="https://aicte.gov.in/downloads/schemes/APDF.pdf",
                default_authority="All India Council for Technical Education (AICTE), Government of India",
            )
            self.assertEqual(len(candidates), 1)
            cand = candidates[0]
            self.assertEqual(cand["scheme_name"], "AICTE POST-DOCTORAL FELLOWSHIP (APDF)")
            self.assertEqual(cand["scholarship_amount"], "Rs. 80,000")
            self.assertIn("government_authority", cand["field_evidence"])
            self.assertIn("scholarship_amount", cand["field_evidence"])

            status, reason = evaluate_verification_status(
                scheme_name=cand["scheme_name"],
                government_authority=cand["government_authority"],
                scholarship_amount=cand["scholarship_amount"],
                application_deadline=cand["application_deadline"],
                eligibility_criteria=cand["eligibility_criteria"],
                supporting_quotations=cand["supporting_quotations"],
                field_evidence=cand["field_evidence"],
            )
            self.assertEqual(status, VerificationStatus.VERIFIED)
            self.assertIn("Verified:", reason)

    def test_pipeline_guideline_following_and_storage(self):
        """Test pipeline discovers official guideline link, fetches PDF, and stores records without duplicates."""
        html_content = """
        <html><body>
        <div class="schemeListItem">
            <h4 class="headingText">PRAGATI SCHOLARSHIP</h4>
            <div class="schemeDetails">
                <p class="schemeDetailsText">Scholarship/Contingency is awarded to meritorious girls taking admission in AICTE approved Technical institution at Degree/ Diploma. Total 10000 scholarship are given @ Rs 50000 as incidentals each year.</p>
                <a class="schemeLinkBtn" href="https://aicte.gov.in/downloads/schemes/Sarswati Notification 2026-27.pdf">AICTE Sarswati Notification 2026-27</a>
            </div>
        </div>
        </body></html>
        """
        notification_pdf_text = (
            "ALL INDIA COUNCIL FOR TECHNICAL EDUCATION (A STATUTORY BODY OF THE GOVERNMENT OF INDIA)\n"
            "Nelson Mandela Marg, Vasant Kunj, New Delhi-110070\n"
            "AICTE SARSWATI SCHOLARSHIP SCHEME FOR A/Y 2026-27\n"
            "NOTIFICATION\n"
            "The AICTE SARSWATI Scholarship scheme for BBA, BCA & BMS Girls Students in AICTE-approved institutions were implemented during Academic Year 2024-25.\n"
            "1. Opening of portal for students (Fresh and Renewal): 15th October 2026\n"
            "2. The last date for verification of applications by Institutes (L1 Level): 31st January 2027\n"
            "3. The Last date Verification of applications at State Nodal Officer: 15th February 2027\n"
            "4. Portal to be closed on 28th February 2027\n"
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "robots.txt" in url_str:
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if "students-development-schemes" in url_str:
                return httpx.Response(200, text=html_content, headers={"content-type": "text/html"})
            if "Sarswati" in url_str:
                return httpx.Response(200, content=b"%PDF-1.4 mock", headers={"content-type": "application/pdf"})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        with patch.object(PDFExtractor, "extract_clean_text", return_value=notification_pdf_text):
            with httpx.Client(transport=transport) as mock_client:
                res = self.pipeline.ingest_source("aicte-schemes", client=mock_client)
                self.assertTrue(res["success"])
                # Pragati from HTML + Sarswati from guideline PDF
                self.assertGreaterEqual(res["records_extracted"], 2)

                records = self.db.get_scholarship_records()
                names = [r.scheme_name for r in records]
                self.assertTrue(any("PRAGATI" in n for n in names))
                self.assertTrue(any("SARSWATI" in n for n in names))

                pragati_rec = next(r for r in records if "PRAGATI" in r.scheme_name)
                self.assertEqual(pragati_rec.verification_status, VerificationStatus.VERIFIED)
                self.assertEqual(pragati_rec.scholarship_amount, "Rs 50000")

                sarswati_rec = next(r for r in records if "SARSWATI" in r.scheme_name)
                self.assertEqual(sarswati_rec.verification_status, VerificationStatus.UNVERIFIED)
                self.assertEqual(sarswati_rec.application_status, "unknown")
                self.assertEqual(sarswati_rec.scheme_year, "2026-27")

                # Test re-ingestion does not duplicate records
                res2 = self.pipeline.ingest_source("aicte-schemes", client=mock_client)
                records2 = self.db.get_scholarship_records()
                self.assertEqual(len(records), len(records2))

    def test_repeated_ingestion_duplicate_prevention_and_conflict_safety(self):
        """Test repeated ingestion preserves records, prevents duplicates, and never silently verifies conflicting evidence."""
        raw_record = {
            "scheme_name": "Test Engineering Merit Fellowship",
            "government_authority": "All India Council for Technical Education",
            "description": "Financial aid for meritorious engineering scholars.",
            "scholarship_amount": "Rs. 50,000 per annum",
            "eligibility_criteria": ["Pursuing regular B.Tech with 60% marks", "Family income up to Rs 4.5 lakh"],
            "application_deadline": "2027-05-31",
            "official_source_url": "https://aicte.gov.in/schemes/merit-fellowship",
            "supporting_quotations": [
                "All India Council for Technical Education invites applications for Test Engineering Merit Fellowship",
                "Awarded @ Rs. 50,000 per annum to meritorious B.Tech students",
                "Pursuing regular B.Tech with 60% marks",
            ],
            "field_evidence": {
                "scheme_name": "Test Engineering Merit Fellowship",
                "government_authority": "All India Council for Technical Education",
                "scholarship_amount": "Awarded @ Rs. 50,000 per annum to meritorious B.Tech students",
                "eligibility_criteria": "Pursuing regular B.Tech with 60% marks",
            },
            "scheme_year": "2026-27",
            "application_status": "open",
        }

        # First save
        status, notes = evaluate_verification_status(
            scheme_name=raw_record["scheme_name"],
            government_authority=raw_record["government_authority"],
            scholarship_amount=raw_record["scholarship_amount"],
            application_deadline=raw_record["application_deadline"],
            eligibility_criteria=raw_record["eligibility_criteria"],
            supporting_quotations=raw_record["supporting_quotations"],
            field_evidence=raw_record["field_evidence"],
        )
        self.assertEqual(status, VerificationStatus.VERIFIED)
        raw_record["verification_status"] = status
        raw_record["verification_notes"] = notes

        rec = ScholarshipRecord(**raw_record)
        saved1, created1 = self.db.save_scholarship_record(rec)
        self.assertTrue(created1)
        all_recs = self.db.get_scholarship_records()
        self.assertEqual(len(all_recs), 1)

        # Repeated ingestion with identical source_url and scheme_name
        saved2, created2 = self.db.save_scholarship_record(rec)
        self.assertFalse(created2)
        all_recs2 = self.db.get_scholarship_records()
        self.assertEqual(len(all_recs2), 1)
        self.assertEqual(all_recs2[0].scheme_year, "2026-27")
        self.assertEqual(str(all_recs2[0].official_source_url), "https://aicte.gov.in/schemes/merit-fellowship")
        self.assertEqual(all_recs2[0].supporting_quotations, raw_record["supporting_quotations"])

        # Update with conflicting amount evidence (amount claims Rs 90000 but evidence says Rs 50000)
        raw_record["scholarship_amount"] = "Rs. 90,000 per annum"
        conflicting_status, conflicting_notes = evaluate_verification_status(
            scheme_name=raw_record["scheme_name"],
            government_authority=raw_record["government_authority"],
            scholarship_amount=raw_record["scholarship_amount"],
            application_deadline=raw_record["application_deadline"],
            eligibility_criteria=raw_record["eligibility_criteria"],
            supporting_quotations=raw_record["supporting_quotations"],
            field_evidence=raw_record["field_evidence"],
        )
        # Must NOT be silently verified!
        self.assertEqual(conflicting_status, VerificationStatus.UNVERIFIED)
        self.assertIn("does not match its field-level source evidence", conflicting_notes)

        raw_record["verification_status"] = conflicting_status
        raw_record["verification_notes"] = conflicting_notes
        rec_conflict = ScholarshipRecord(**raw_record)
        saved3, created3 = self.db.save_scholarship_record(rec_conflict)
        self.assertFalse(created3)
        all_recs3 = self.db.get_scholarship_records()
        self.assertEqual(len(all_recs3), 1)
        self.assertEqual(all_recs3[0].verification_status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(all_recs3[0].last_verified_timestamp)

        # Subsequent ingestion without valid evidence cannot silently restore VERIFIED
        rec_unverified_attempt = ScholarshipRecord(
            **{**raw_record, "verification_status": VerificationStatus.UNVERIFIED, "field_evidence": {}}
        )
        saved_subsequent, created_subsequent = self.db.save_scholarship_record(rec_unverified_attempt)
        self.assertFalse(created_subsequent)
        recs_subsequent = self.db.get_scholarship_records()
        self.assertEqual(len(recs_subsequent), 1)
        self.assertEqual(recs_subsequent[0].verification_status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(recs_subsequent[0].last_verified_timestamp)

        # Test distinct academic year for the same scheme can be stored independently
        raw_record_next_year = dict(raw_record)
        raw_record_next_year["scheme_year"] = "2027-28"
        raw_record_next_year["scholarship_amount"] = "Rs. 50,000 per annum"
        raw_record_next_year["verification_status"] = VerificationStatus.VERIFIED
        raw_record_next_year["verification_notes"] = notes
        rec_next_year = ScholarshipRecord(**raw_record_next_year)
        saved4, created4 = self.db.save_scholarship_record(rec_next_year)
        self.assertTrue(created4)
        all_recs4 = self.db.get_scholarship_records()
        self.assertEqual(len(all_recs4), 2)
        years = {r.scheme_year for r in all_recs4}
        self.assertEqual(years, {"2026-27", "2027-28"})

        # Re-ingesting 2027-28 does NOT duplicate
        saved5, created5 = self.db.save_scholarship_record(rec_next_year)
        self.assertFalse(created5)
        all_recs5 = self.db.get_scholarship_records()
        self.assertEqual(len(all_recs5), 2)

    def test_database_schema_migration_from_legacy_uniqueness_constraint(self):
        """Test migrating a database with legacy UNIQUE(official_source_url, scheme_name) schema.

        Verifies:
        - Schema table constraint is upgraded to UNIQUE(official_source_url, scheme_name, scheme_year).
        - Existing records and original IDs (including records with NULL scheme_year) are preserved.
        - No data loss or silent corruption.
        - Re-running the migration is safe and idempotent.
        - Coexistence of same scheme across different academic years works without constraint errors.
        - Duplicate prevention works per (source_url, scheme_name, scheme_year).
        - NULL and empty academic years are handled consistently.
        """
        temp_legacy = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_legacy_path = temp_legacy.name
        temp_legacy.close()

        try:
            # 1. Setup a legacy SQLite database mimicking Phase 1-5 schema exactly
            conn = sqlite3.connect(temp_legacy_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'html',
                    category TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    verified INTEGER NOT NULL DEFAULT 0,
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    last_checked_timestamp TEXT,
                    last_verified_timestamp TEXT,
                    last_error TEXT,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE ingestion_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    status_code INTEGER,
                    success INTEGER NOT NULL,
                    content_type TEXT,
                    records_extracted INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    FOREIGN KEY (source_id) REFERENCES sources (id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE scholarship_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT,
                    scheme_name TEXT NOT NULL,
                    government_authority TEXT NOT NULL,
                    description TEXT NOT NULL,
                    eligibility_criteria TEXT NOT NULL,
                    scholarship_amount TEXT,
                    application_deadline TEXT,
                    application_deadline_raw TEXT,
                    required_documents TEXT NOT NULL,
                    official_source_url TEXT NOT NULL,
                    supporting_quotations TEXT NOT NULL,
                    field_evidence TEXT,
                    last_verified_timestamp TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    verification_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(official_source_url, scheme_name)
                )
                """
            )

            # Insert representative existing records with explicit IDs and NULL scheme_year
            cursor.execute(
                """
                INSERT INTO scholarship_records (
                    id, source_id, scheme_name, government_authority, description,
                    eligibility_criteria, scholarship_amount, application_deadline,
                    required_documents, official_source_url, supporting_quotations,
                    field_evidence, last_verified_timestamp, verification_status,
                    verification_notes, created_at, updated_at
                ) VALUES (
                    1, 'test-src', 'Fellowship', 'Ministry of Tribal Affairs',
                    'National Fellowship for ST Students', '["Pursuing regular M.Phil/Ph.D"]',
                    'Rs 30', '31-10-2026', '["Caste Certificate"]',
                    'https://tribal.nic.in/schemes/fellowship',
                    '["Fellowship of Rs 30 per month"]', '{}',
                    NULL, 'unverified', 'Suspiciously small token amount Rs 30',
                    '2026-10-30T10:00:00Z', '2026-10-30T10:00:00Z'
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO scholarship_records (
                    id, source_id, scheme_name, government_authority, description,
                    eligibility_criteria, scholarship_amount, application_deadline,
                    required_documents, official_source_url, supporting_quotations,
                    field_evidence, last_verified_timestamp, verification_status,
                    verification_notes, created_at, updated_at
                ) VALUES (
                    2, 'aicte-src', 'AICTE Pragati', 'AICTE',
                    'Pragati Scholarship for Girls', '["Girls studying diploma/degree"]',
                    'Rs. 50,000 per annum', '2026-12-31', '["Income Certificate"]',
                    'https://aicte.gov.in/schemes/pragati',
                    '["Rs. 50,000 per annum"]', '{}',
                    '2026-10-30T11:00:00Z', 'unverified', 'Missing required field evidence',
                    '2026-10-30T10:00:00Z', '2026-10-30T10:00:00Z'
                )
                """
            )
            conn.commit()

            # Confirm legacy table schema has UNIQUE(official_source_url, scheme_name)
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scholarship_records'")
            legacy_sql = cursor.fetchone()["sql"]
            self.assertIn("UNIQUE(official_source_url, scheme_name)", legacy_sql)
            conn.close()

            # 2. Open legacy database through Database class which auto-executes migration
            migrated_db = Database(temp_legacy_path)

            # 3. Inspect sqlite_master for updated table definition and unique index
            conn_check = sqlite3.connect(temp_legacy_path)
            conn_check.row_factory = sqlite3.Row
            cursor_check = conn_check.cursor()

            cursor_check.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scholarship_records'")
            new_sql = cursor_check.fetchone()["sql"]
            self.assertIn("UNIQUE(official_source_url, scheme_name, scheme_year)", new_sql)
            self.assertNotIn("UNIQUE(official_source_url, scheme_name)", new_sql.replace("scheme_year", ""))

            cursor_check.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_records_url_scheme_year'")
            idx_row = cursor_check.fetchone()
            self.assertIsNotNone(idx_row)

            # 4. Verify existing records and IDs were preserved intact
            recs = migrated_db.get_scholarship_records()
            self.assertEqual(len(recs), 2)
            rec1 = next(r for r in recs if r.id == 1)
            rec2 = next(r for r in recs if r.id == 2)
            self.assertIsNotNone(rec1)
            self.assertIsNotNone(rec2)
            self.assertEqual(rec1.scheme_name, "Fellowship")
            self.assertEqual(rec1.scholarship_amount, "Rs 30")
            self.assertIsNone(rec1.scheme_year)
            self.assertEqual(rec2.scheme_name, "AICTE Pragati")
            self.assertEqual(rec2.scholarship_amount, "Rs. 50,000 per annum")
            self.assertIsNone(rec2.scheme_year)

            # 5. Verify re-running migration is safe and returns False
            rerun_migrated = migrated_db.migrate_legacy_uniqueness_constraint()
            self.assertFalse(rerun_migrated)

            # 6. Verify same scheme with different academic years can now coexist without constraint errors
            new_cycle_pragati = ScholarshipRecord(
                scheme_name="AICTE Pragati",
                government_authority="AICTE",
                description="Pragati Scholarship for Girls 2026-27",
                scholarship_amount="Rs. 50,000 per annum",
                application_deadline="2027-01-31",
                eligibility_criteria=["Girls studying degree"],
                official_source_url="https://aicte.gov.in/schemes/pragati",
                supporting_quotations=["Rs. 50,000 per annum"],
                field_evidence={"scholarship_amount": "Rs. 50,000 per annum"},
                scheme_year="2026-27",
                verification_status=VerificationStatus.UNVERIFIED,
            )
            saved_cycle, created_cycle = migrated_db.save_scholarship_record(new_cycle_pragati)
            self.assertTrue(created_cycle)

            all_after_new_cycle = migrated_db.get_scholarship_records()
            self.assertEqual(len(all_after_new_cycle), 3)

            # 7. Verify duplicate insertion with same scheme and scheme_year updates existing rather than creating duplicate
            saved_dupe, created_dupe = migrated_db.save_scholarship_record(new_cycle_pragati)
            self.assertFalse(created_dupe)
            self.assertEqual(len(migrated_db.get_scholarship_records()), 3)

            # 8. Verify updating record with NULL/empty scheme_year updates record 1 instead of duplicating
            fellowship_update = ScholarshipRecord(
                scheme_name="Fellowship",
                government_authority="Ministry of Tribal Affairs",
                description="Updated Fellowship description",
                scholarship_amount="Rs 30",
                application_deadline="31-10-2026",
                eligibility_criteria=["Pursuing regular M.Phil/Ph.D"],
                official_source_url="https://tribal.nic.in/schemes/fellowship",
                supporting_quotations=["Fellowship of Rs 30 per month"],
                field_evidence={},
                scheme_year=None,
                verification_status=VerificationStatus.UNVERIFIED,
            )
            saved_f, created_f = migrated_db.save_scholarship_record(fellowship_update)
            self.assertFalse(created_f)
            self.assertEqual(saved_f.id, 1)
            self.assertEqual(len(migrated_db.get_scholarship_records()), 3)

            conn_check.close()
        finally:
            try:
                os.remove(temp_legacy_path)
            except OSError:
                pass



if __name__ == "__main__":
    unittest.main()

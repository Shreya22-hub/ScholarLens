"""Ingestion pipeline orchestrator for ScholarLens.

Coordinates registry validation, polite fetching, document extraction,
strict Pydantic schema validation, and persistent storage with duplicate prevention.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.config import settings
from backend.app.db.database import Database, get_db
from backend.app.ingestion.extractors.html_extractor import HTMLExtractor
from backend.app.ingestion.extractors.pdf_extractor import PDFExtractor
from backend.app.ingestion.fetcher import FetchResult, PoliteFetcher
from backend.app.ingestion.link_follower import fetch_guideline_document, is_allowed_guideline_url
from backend.app.ingestion.quality import evaluate_verification_status
from backend.app.ingestion.registry import SourceRegistry
from backend.app.schemas.scholarship import (
    IngestionRunRecord,
    ScholarshipRecord,
    ScholarshipSource,
    SourceType,
    VerificationStatus,
)


class IngestionPipeline:
    """Orchestrates end-to-end ingestion and verification for configured scholarship sources."""

    def __init__(
        self,
        registry: Optional[SourceRegistry] = None,
        fetcher: Optional[PoliteFetcher] = None,
        db: Optional[Database] = None,
    ):
        self.db = db or get_db()
        self.registry = registry or SourceRegistry(db=self.db)
        self.fetcher = fetcher or PoliteFetcher()

    def ingest_source(
        self,
        source_id: str,
        client: Optional[httpx.Client] = None,
    ) -> Dict[str, Any]:
        """Ingest a single configured official source.

        Validates against the source registry to prevent arbitrary URL scraping.
        """
        source = self.registry.get_source(source_id)
        if not source:
            return {
                "source_id": source_id,
                "success": False,
                "error": f"Source ID '{source_id}' is not in the configured registry. Crawling unverified arbitrary URLs is restricted.",
                "records_extracted": 0,
            }

        if not source.is_active:
            return {
                "source_id": source_id,
                "success": False,
                "error": f"Source '{source.name}' ({source_id}) is currently disabled.",
                "records_extracted": 0,
            }

        # 1. Polite fetch
        url_str = str(source.url)
        fetch_result = self.fetcher.fetch(url=url_str, source_id=source_id, client=client)

        # 2. Record run telemetry
        run_record = IngestionRunRecord(
            source_id=source_id,
            url=url_str,
            fetched_at=fetch_result.fetched_at,
            status_code=fetch_result.status_code,
            success=fetch_result.success,
            content_type=fetch_result.content_type,
            records_extracted=0,
            error_message=fetch_result.error_message,
        )

        if not fetch_result.success:
            # Update telemetry and record run
            self.db.record_ingestion_run(run_record)
            self.registry.update_source_telemetry(
                source_id=source_id,
                last_checked=fetch_result.fetched_at,
                verification_status=VerificationStatus.FLAGGED if fetch_result.status_code else VerificationStatus.UNVERIFIED,
                verified=False,
                last_error=fetch_result.error_message,
            )
            return {
                "source_id": source_id,
                "url": url_str,
                "status_code": fetch_result.status_code,
                "success": False,
                "error": fetch_result.error_message,
                "records_extracted": 0,
            }

        # 3. Document Extraction (HTML or PDF)
        candidate_records_raw: List[Dict[str, Any]] = []
        is_pdf = (
            source.source_type == SourceType.PDF
            or "application/pdf" in (fetch_result.content_type or "").lower()
            or url_str.lower().endswith(".pdf")
        )

        if is_pdf:
            candidate_records_raw = PDFExtractor.extract_scholarships_from_pdf(
                pdf_bytes=fetch_result.content_bytes,
                source_url=url_str,
                default_authority=source.authority,
            )
        else:
            html_text = fetch_result.text_content or fetch_result.content_bytes.decode("utf-8", errors="replace")
            candidate_records_raw = HTMLExtractor.extract_scholarships_from_html(
                html_content=html_text,
                source_url=url_str,
                default_authority=source.authority,
            )

            # Discover and safely retrieve official guideline documents on verified government domains
            guideline_links = HTMLExtractor.extract_guideline_links(
                html_content=html_text,
                base_url=url_str,
            )

            followed_count = 0
            for g_item in guideline_links:
                g_url = g_item["url"]
                # Enforce domain policy: must belong to registered source or official government domain
                is_allowed, reason = is_allowed_guideline_url(g_url, source_url=url_str)
                if not is_allowed:
                    continue

                # Enforce robots.txt policy
                if not self.fetcher.can_fetch(g_url, client=client):
                    continue

                g_fetch = fetch_guideline_document(
                    url=g_url,
                    source_id=source_id,
                    fetcher=self.fetcher,
                    source_url=url_str,
                    client=client,
                )

                if g_fetch.success and g_fetch.content_bytes:
                    is_g_pdf = (
                        "application/pdf" in (g_fetch.content_type or "").lower()
                        or g_url.lower().endswith(".pdf")
                        or g_fetch.content_bytes[:4] == b"%PDF"
                    )
                    if is_g_pdf:
                        guideline_candidates = PDFExtractor.extract_scholarships_from_pdf(
                            pdf_bytes=g_fetch.content_bytes,
                            source_url=g_url,
                            default_authority=source.authority,
                        )
                        for gc in guideline_candidates:
                            gc["guideline_url"] = g_url
                            candidate_records_raw.append(gc)

                        followed_count += 1
                        if followed_count >= 5:
                            break

        # 4. Strict Validation and Conservative Verification Assignment
        saved_records: List[ScholarshipRecord] = []
        verified_count = 0

        for raw in candidate_records_raw:
            try:
                # Conservative verification rules:
                status, explanation = evaluate_verification_status(
                    scheme_name=raw.get("scheme_name", ""),
                    government_authority=raw.get("government_authority", ""),
                    scholarship_amount=raw.get("scholarship_amount"),
                    application_deadline=raw.get("application_deadline"),
                    eligibility_criteria=raw.get("eligibility_criteria", []),
                    supporting_quotations=raw.get("supporting_quotations", []),
                    field_evidence=raw.get("field_evidence", {}),
                )

                raw["verification_status"] = status
                raw["verification_notes"] = explanation
                if status == VerificationStatus.VERIFIED:
                    raw["last_verified_timestamp"] = datetime.now(timezone.utc)
                    verified_count += 1
                else:
                    raw["last_verified_timestamp"] = None

                # Pydantic validation
                record = ScholarshipRecord(**raw)

                # 5. Persistent Storage with Duplicate Prevention
                saved_record, created = self.db.save_scholarship_record(record, source_id=source.id)
                saved_records.append(saved_record)

            except Exception:
                # Skip candidate that fails Pydantic validation
                continue

        # Update run telemetry and source status
        run_record.records_extracted = len(saved_records)
        self.db.record_ingestion_run(run_record)

        source_verification_status = (
            VerificationStatus.VERIFIED if verified_count > 0 else VerificationStatus.UNVERIFIED
        )

        self.registry.update_source_telemetry(
            source_id=source_id,
            last_checked=fetch_result.fetched_at,
            verification_status=source_verification_status,
            verified=(verified_count > 0),
            last_error=None,
            last_verified=datetime.now(timezone.utc) if verified_count > 0 else None,
        )

        return {
            "source_id": source_id,
            "url": url_str,
            "status_code": fetch_result.status_code,
            "success": True,
            "records_extracted": len(saved_records),
            "records_verified": verified_count,
            "records": [r.model_dump(mode="json") for r in saved_records],
            "error": None,
        }

    def ingest_all_enabled_sources(
        self, client: Optional[httpx.Client] = None
    ) -> List[Dict[str, Any]]:
        """Run ingestion sequentially across all enabled configured sources."""
        enabled = self.registry.get_enabled_sources()
        results = []
        for src in enabled:
            res = self.ingest_source(src.id, client=client)
            results.append(res)
        return results

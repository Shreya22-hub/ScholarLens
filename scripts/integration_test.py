"""Comprehensive Integration Test for ScholarLens Phase 3.

Tests the full flow:
Official source -> Fetcher -> HTML/PDF extraction -> Data quality verification -> Database -> API response.
"""

import json
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.db.database import Database
from backend.app.ingestion.fetcher import PoliteFetcher
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.registry import SourceRegistry
from backend.app.main import app
from backend.app.schemas.scholarship import VerificationStatus


def run_integration_tests():
    print("=" * 70)
    print("SCHOLARLENS PHASE 3 INTEGRATION TEST")
    print("=" * 70)

    db_path = settings.database_path
    sources_path = settings.sources_config_path

    print(f"Database Path: {db_path}")
    print(f"Sources Path:  {sources_path}")
    print("-" * 70)

    registry = SourceRegistry(sources_path)
    db = Database(db_path)
    pipeline = IngestionPipeline(registry=registry, db=db)
    client = TestClient(app)

    # 1. Inspect existing state and Bad Record "Fellowship" / "Rs 30"
    print("\n--- [CHECK 1] Inspect Existing Database Records ---")
    initial_records = db.get_scholarship_records()
    print(f"Initial record count in DB: {len(initial_records)}")

    bad_record = next((r for r in initial_records if r.scheme_name == "Fellowship"), None)
    if bad_record:
        print(f"Found existing record ID: {bad_record.id}")
        print(f"  Scheme Name:        {bad_record.scheme_name}")
        print(f"  Amount:             {bad_record.scholarship_amount}")
        print(f"  Status:             {bad_record.verification_status}")
        print(f"  Notes:              {bad_record.verification_notes}")
        assert bad_record.verification_status == VerificationStatus.UNVERIFIED, (
            "ERROR: 'Fellowship' record should be UNVERIFIED!"
        )
        print("  -> CONFIRMED: Bad record 'Fellowship' / 'Rs 30' remains UNVERIFIED.")
    else:
        print("  -> Notice: 'Fellowship' record not found by exact name match.")

    # 2. Run ingestion on all configured official sources
    print("\n--- [CHECK 2] Run Ingestion Against Configured Official Sources ---")
    enabled_sources = registry.get_enabled_sources()
    print(f"Configured enabled sources: {len(enabled_sources)}")

    source_reports = []

    for src in enabled_sources:
        print(f"\nProcessing Source: [{src.id}] - {src.name}")
        print(f"  URL:  {src.url}")
        print(f"  Type: {src.source_type}")

        # Run pipeline ingestion for this source
        res = pipeline.ingest_source(src.id)
        
        extracted_records = res.get("records", [])
        verified_in_run = sum(
            1 for r in extracted_records if r.get("verification_status") == "verified"
        )
        unverified_in_run = sum(
            1 for r in extracted_records if r.get("verification_status") == "unverified"
        )

        report = {
            "source_id": src.id,
            "name": src.name,
            "url": str(src.url),
            "type": str(src.source_type),
            "fetch_success": res.get("success", False),
            "status_code": res.get("status_code"),
            "records_extracted": res.get("records_extracted", 0),
            "records_verified": verified_in_run,
            "records_unverified": unverified_in_run,
            "error": res.get("error"),
        }
        source_reports.append(report)

        print(f"  Fetch Success:      {report['fetch_success']} (HTTP {report['status_code']})")
        print(f"  Records Extracted:  {report['records_extracted']}")
        print(f"  Verified Count:     {report['records_verified']}")
        print(f"  Unverified Count:   {report['records_unverified']}")
        if report["error"]:
            print(f"  Error / Note:       {report['error']}")

    # 3. Check for Duplicate Records on Second Ingestion Run
    print("\n--- [CHECK 3] Test Duplicate Prevention on Second Ingestion Run ---")
    count_after_first_run = len(db.get_scholarship_records())
    print(f"Record count after Run 1: {count_after_first_run}")

    print("Executing Run 2 on all enabled sources...")
    for src in enabled_sources:
        pipeline.ingest_source(src.id)

    count_after_second_run = len(db.get_scholarship_records())
    print(f"Record count after Run 2: {count_after_second_run}")
    duplicates_created = count_after_second_run - count_after_first_run
    print(f"New records created in Run 2: {duplicates_created}")
    assert duplicates_created == 0, f"ERROR: Duplicate records were created: {duplicates_created}"
    print("  -> CONFIRMED: Duplicate prevention is functioning perfectly (0 duplicates created).")

    # 4. Verify API response representations
    print("\n--- [CHECK 4] Verify GET /api/scholarships Behavior ---")
    api_res = client.get("/api/scholarships")
    assert api_res.status_code == 200, f"API error: {api_res.status_code}"
    api_records = api_res.json()
    print(f"Total scholarships returned by API: {len(api_records)}")

    # Check that verification status and notes are clearly presented
    for rec in api_records:
        status = rec.get("verification_status")
        notes = rec.get("verification_notes")
        print(f"  Scholarship #{rec['id']}: '{rec['scheme_name']}'")
        print(f"    Verification Status: {status}")
        print(f"    Notes:               {notes[:100]}..." if notes and len(notes) > 100 else f"    Notes: {notes}")
        print(f"    Source Link:         {rec.get('official_source_url')}")
        assert status in ("verified", "unverified"), f"Invalid status: {status}"
        if status == "unverified":
            assert notes, f"Unverified record #{rec['id']} must provide verification_notes explaining why."

    # 5. Check API filter parameters
    print("\n--- [CHECK 5] Verify API Filters for Verified / Unverified ---")
    verified_res = client.get("/api/scholarships?status=verified")
    assert verified_res.status_code == 200
    verified_list = verified_res.json()
    print(f"Verified scholarships filter:   {len(verified_list)}")
    for r in verified_list:
        assert r["verification_status"] == "verified", f"Record {r['id']} is not verified!"

    unverified_res = client.get("/api/scholarships?status=unverified")
    assert unverified_res.status_code == 200
    unverified_list = unverified_res.json()
    print(f"Unverified scholarships filter: {len(unverified_list)}")
    for r in unverified_list:
        assert r["verification_status"] == "unverified", f"Record {r['id']} is not unverified!"

    print("\n" + "=" * 70)
    print("INTEGRATION TEST SUMMARY REPORT")
    print("=" * 70)
    print(json.dumps(source_reports, indent=2))

    return source_reports


if __name__ == "__main__":
    run_integration_tests()

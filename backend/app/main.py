"""ScholarLens FastAPI backend entrypoint with Phase 2 Ingestion & Storage API."""

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.db.database import Database, get_db
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.registry import SourceRegistry
from backend.app.schemas.scholarship import (
    IngestionRunRecord,
    ScholarshipRecord,
    ScholarshipSource,
    VerificationStatus,
)

app = FastAPI(
    title="ScholarLens API",
    description="Backend API for ScholarLens — Indian Government Scholarships Discovery & Verification Platform",
    version="0.2.0",
)

# Enable CORS for local Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database and Ingestion singletons
db = get_db()
registry = SourceRegistry(db=db)
pipeline = IngestionPipeline(registry=registry, db=db)


class IngestionRequest(BaseModel):
    """Payload to trigger ingestion."""

    source_id: Optional[str] = None


@app.get("/", tags=["Root"])
def root() -> Dict[str, str]:
    """Root endpoint returning service status."""
    return {
        "service": "ScholarLens API",
        "status": "operational",
        "phase": "Phase 2 - Polite Ingestion & Verified Storage Active",
    }


@app.get("/health", tags=["System"])
def health_check() -> Dict[str, str]:
    """System health check endpoint."""
    return {"status": "healthy"}


# =============================================================================
# Sources Endpoints
# =============================================================================


@app.get("/sources", response_model=List[ScholarshipSource], tags=["Sources"])
@app.get("/api/sources", response_model=List[ScholarshipSource], tags=["Sources"])
def list_sources(enabled_only: bool = False) -> List[ScholarshipSource]:
    """Retrieve list of configured official scholarship portal sources with runtime telemetry."""
    if enabled_only:
        return registry.get_enabled_sources()
    return registry.get_all_sources()


@app.get("/api/sources/{source_id}", response_model=ScholarshipSource, tags=["Sources"])
def get_source_details(source_id: str) -> ScholarshipSource:
    """Retrieve details and status for a specific configured source."""
    source = registry.get_source(source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=f"Source '{source_id}' is not in the configured registry.",
        )
    return source


# =============================================================================
# Ingestion Endpoints (Restricted to Configured Sources)
# =============================================================================


@app.post("/api/ingest", tags=["Ingestion"])
def trigger_ingestion(payload: Optional[IngestionRequest] = None) -> Dict[str, Any]:
    """Trigger polite ingestion for configured sources.

    Strictly forbids unrestricted crawling; only configured registry sources can be ingested.
    """
    source_id = payload.source_id if payload else None
    if source_id:
        if not registry.is_source_registered(source_id):
            raise HTTPException(
                status_code=400,
                detail=f"Source ID '{source_id}' is not configured in sources registry. Crawling arbitrary URLs is prohibited.",
            )
        result = pipeline.ingest_source(source_id)
        return {"summary": "Single source ingestion executed", "results": [result]}

    # Ingest all enabled sources
    results = pipeline.ingest_all_enabled_sources()
    return {"summary": f"Ingestion executed for {len(results)} enabled source(s)", "results": results}


@app.post("/api/sources/{source_id}/ingest", tags=["Ingestion"])
def trigger_source_ingestion(source_id: str) -> Dict[str, Any]:
    """Trigger ingestion for a specific registered source."""
    if not registry.is_source_registered(source_id):
        raise HTTPException(
            status_code=400,
            detail=f"Source ID '{source_id}' is not configured in sources registry. Crawling arbitrary URLs is prohibited.",
        )
    return pipeline.ingest_source(source_id)


@app.get("/api/ingestion/runs", response_model=List[IngestionRunRecord], tags=["Ingestion"])
def list_ingestion_runs(limit: int = Query(default=20, ge=1, le=100)) -> List[IngestionRunRecord]:
    """View recent ingestion run logs and results."""
    return db.get_recent_ingestion_runs(limit=limit)


# =============================================================================
# Scholarships Endpoints
# =============================================================================


@app.get("/scholarships", response_model=List[ScholarshipRecord], tags=["Scholarships"])
@app.get("/api/scholarships", response_model=List[ScholarshipRecord], tags=["Scholarships"])
def list_scholarships(
    verification_status: Optional[VerificationStatus] = None,
    source_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> List[ScholarshipRecord]:
    """Retrieve stored scholarship records with optional filtering by status and source."""
    return db.get_scholarship_records(
        verification_status=verification_status,
        source_id=source_id,
        limit=limit,
    )

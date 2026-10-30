"""ScholarLens Ingestion Package."""

from backend.app.ingestion.fetcher import PoliteFetcher, FetchResult
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.registry import SourceRegistry

__all__ = ["PoliteFetcher", "FetchResult", "IngestionPipeline", "SourceRegistry"]

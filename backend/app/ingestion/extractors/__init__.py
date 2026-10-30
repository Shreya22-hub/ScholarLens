"""ScholarLens Document Text and Scholarship Extractors."""

from backend.app.ingestion.extractors.html_extractor import HTMLExtractor
from backend.app.ingestion.extractors.pdf_extractor import PDFExtractor

__all__ = ["HTMLExtractor", "PDFExtractor"]

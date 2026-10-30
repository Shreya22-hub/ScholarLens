"""Source registry module for ScholarLens.

Reads config/sources.json, validates against ScholarshipSource schema,
and synchronizes with the persistent SQLite database.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.config import settings
from backend.app.db.database import Database, get_db
from backend.app.schemas.scholarship import ScholarshipSource, SourceType, VerificationStatus


class SourceRegistry:
    """Manages the lifecycle, configuration, and telemetry of official scholarship sources."""

    def __init__(self, config_path: Optional[str] = None, db: Optional[Database] = None):
        self.config_path = Path(config_path or settings.sources_config_path)
        self.db = db or get_db()
        self._load_and_sync()

    def _load_and_sync(self) -> List[ScholarshipSource]:
        """Load sources from json and synchronize with database."""
        sources = self.load_configured_sources()
        if sources:
            self.db.sync_sources(sources)
        return sources

    def load_configured_sources(self) -> List[ScholarshipSource]:
        """Read and validate sources from configuration file."""
        if not self.config_path.is_file():
            return []

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_sources = data.get("sources", [])
        validated: List[ScholarshipSource] = []
        for raw in raw_sources:
            try:
                src = ScholarshipSource(**raw)
                validated.append(src)
            except Exception as e:
                # Log or skip malformed configured source
                continue
        return validated

    def get_all_sources(self) -> List[ScholarshipSource]:
        """Retrieve all sources from database with runtime status."""
        return self.db.get_sources(enabled_only=False)

    def get_enabled_sources(self) -> List[ScholarshipSource]:
        """Retrieve only enabled sources from database."""
        return self.db.get_sources(enabled_only=True)

    def get_source(self, source_id: str) -> Optional[ScholarshipSource]:
        """Retrieve specific source by ID."""
        return self.db.get_source(source_id)

    def is_source_registered(self, source_id: str) -> bool:
        """Check if source_id is registered (prevents arbitrary scraping targets)."""
        return self.get_source(source_id) is not None

    def update_source_telemetry(
        self,
        source_id: str,
        last_checked: datetime,
        verification_status: VerificationStatus,
        verified: bool,
        last_error: Optional[str] = None,
        last_verified: Optional[datetime] = None,
    ) -> None:
        """Update runtime verification status and fetch history."""
        self.db.update_source_status(
            source_id=source_id,
            last_checked=last_checked,
            verification_status=verification_status,
            verified=verified,
            last_error=last_error,
            last_verified=last_verified,
        )

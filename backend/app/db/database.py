"""SQLite database access layer for ScholarLens.

Provides persistent storage for configured sources, fetch/ingestion history,
and extracted scholarship records with duplicate prevention.
"""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

from backend.app.schemas.scholarship import (
    IngestionRunRecord,
    ScholarshipRecord,
    ScholarshipSource,
    SourceType,
    VerificationStatus,
)


def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO 8601 string in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _from_iso(val: Optional[str]) -> Optional[datetime]:
    """Parse ISO string to datetime."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


class Database:
    """Manages SQLite connection and queries."""

    def __init__(self, db_path: str = "data/scholarlens.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Create a transaction-scoped connection that is always closed."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create database tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Sources table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
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

            # Ingestion runs / fetch history table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
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

            # Scholarship records table with academic-year-aware uniqueness
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS scholarship_records (
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
                    scheme_year TEXT,
                    application_status TEXT,
                    guideline_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(official_source_url, scheme_name, scheme_year)
                )
                """
            )

            # Auto-migrate columns if table previously created without them
            for col, col_type in [
                ("application_deadline_raw", "TEXT"),
                ("field_evidence", "TEXT"),
                ("verification_notes", "TEXT"),
                ("scheme_year", "TEXT"),
                ("application_status", "TEXT"),
                ("guideline_url", "TEXT"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE scholarship_records ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_url_scheme ON scholarship_records(official_source_url, scheme_name)"
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_url_scheme_year ON scholarship_records(official_source_url, scheme_name, ifnull(scheme_year, ''))"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ingestion_source ON ingestion_runs(source_id, fetched_at)"
            )
            conn.commit()

        # Migrate legacy uniqueness constraint if table was created with previous schema
        self.migrate_legacy_uniqueness_constraint()

        # Audit and reverify existing records against conservative rules
        self.audit_and_reverify_stored_records()

    # =========================================================================
    # Source Registry Storage Methods
    # =========================================================================

    def sync_sources(self, sources: List[ScholarshipSource]) -> None:
        """Synchronize configured sources into database without overwriting runtime state."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for src in sources:
                cursor.execute("SELECT id FROM sources WHERE id = ?", (src.id,))
                existing = cursor.fetchone()
                if existing:
                    # Update configuration fields while preserving runtime telemetry
                    cursor.execute(
                        """
                        UPDATE sources
                        SET name = ?, authority = ?, url = ?, source_type = ?,
                            category = ?, enabled = ?, notes = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            src.name,
                            src.authority,
                            str(src.url),
                            src.source_type.value,
                            src.category,
                            1 if src.is_active else 0,
                            src.notes,
                            now,
                            src.id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO sources (
                            id, name, authority, url, source_type, category,
                            enabled, verified, verification_status,
                            last_checked_timestamp, last_verified_timestamp,
                            last_error, notes, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            src.id,
                            src.name,
                            src.authority,
                            str(src.url),
                            src.source_type.value,
                            src.category,
                            1 if src.is_active else 0,
                            1 if src.verified else 0,
                            src.verification_status.value,
                            _to_utc_iso(src.last_checked_timestamp),
                            _to_utc_iso(src.last_verified_timestamp),
                            src.last_error,
                            src.notes,
                            now,
                        ),
                    )
            conn.commit()

    def get_sources(self, enabled_only: bool = False) -> List[ScholarshipSource]:
        """Retrieve all sources from database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM sources"
            params: Tuple[Any, ...] = ()
            if enabled_only:
                query += " WHERE enabled = 1"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_source(row) for row in rows]

    def get_source(self, source_id: str) -> Optional[ScholarshipSource]:
        """Retrieve single source by id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_source(row)

    def update_source_status(
        self,
        source_id: str,
        last_checked: datetime,
        verification_status: VerificationStatus,
        verified: bool,
        last_error: Optional[str] = None,
        last_verified: Optional[datetime] = None,
    ) -> None:
        """Update runtime telemetry for a source after fetch/ingestion."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sources
                SET last_checked_timestamp = ?,
                    verification_status = ?,
                    verified = ?,
                    last_error = ?,
                    last_verified_timestamp = COALESCE(?, last_verified_timestamp),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _to_utc_iso(last_checked),
                    verification_status.value,
                    1 if verified else 0,
                    last_error,
                    _to_utc_iso(last_verified) if last_verified else None,
                    now,
                    source_id,
                ),
            )
            conn.commit()

    def _row_to_source(self, row: sqlite3.Row) -> ScholarshipSource:
        """Convert a sqlite row to ScholarshipSource."""
        return ScholarshipSource(
            id=row["id"],
            name=row["name"],
            authority=row["authority"],
            url=row["url"],
            source_type=SourceType(row["source_type"]) if row["source_type"] in ["html", "pdf"] else SourceType.HTML,
            category=row["category"],
            is_active=bool(row["enabled"]),
            verified=bool(row["verified"]),
            verification_status=VerificationStatus(row["verification_status"]),
            last_checked_timestamp=_from_iso(row["last_checked_timestamp"]),
            last_verified_timestamp=_from_iso(row["last_verified_timestamp"]),
            last_error=row["last_error"],
            notes=row["notes"],
        )

    # =========================================================================
    # Ingestion Runs Storage Methods
    # =========================================================================

    def record_ingestion_run(self, run: IngestionRunRecord) -> int:
        """Save an ingestion fetch event to history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ingestion_runs (
                    source_id, url, fetched_at, status_code, success,
                    content_type, records_extracted, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.source_id,
                    str(run.url),
                    _to_utc_iso(run.fetched_at),
                    run.status_code,
                    1 if run.success else 0,
                    run.content_type,
                    run.records_extracted,
                    run.error_message,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_recent_ingestion_runs(self, limit: int = 50) -> List[IngestionRunRecord]:
        """Fetch recent ingestion runs ordered newest first."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM ingestion_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            runs = []
            for r in rows:
                runs.append(
                    IngestionRunRecord(
                        id=r["id"],
                        source_id=r["source_id"],
                        url=r["url"],
                        fetched_at=_from_iso(r["fetched_at"]) or datetime.now(timezone.utc),
                        status_code=r["status_code"],
                        success=bool(r["success"]),
                        content_type=r["content_type"],
                        records_extracted=r["records_extracted"],
                        error_message=r["error_message"],
                    )
                )
            return runs

    # =========================================================================
    # Scholarship Records Storage Methods (with Duplicate Prevention)
    # =========================================================================

    def save_scholarship_record(
        self, record: ScholarshipRecord, source_id: Optional[str] = None
    ) -> Tuple[ScholarshipRecord, bool]:
        """Insert or update scholarship record with duplicate prevention.

        Uses UNIQUE(official_source_url, scheme_name).
        Returns tuple of (saved_record, created: bool).
        """
        now = datetime.now(timezone.utc).isoformat()
        eligibility_json = json.dumps(record.eligibility_criteria, ensure_ascii=False)
        documents_json = json.dumps(record.required_documents, ensure_ascii=False)
        quotations_json = json.dumps(record.supporting_quotations, ensure_ascii=False)
        field_evidence_json = json.dumps(record.field_evidence or {}, ensure_ascii=False)
        url_str = str(record.official_source_url)
        last_verified_str = _to_utc_iso(record.last_verified_timestamp)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if record.scheme_year:
                cursor.execute(
                    """
                    SELECT id FROM scholarship_records
                    WHERE official_source_url = ? AND scheme_name = ? AND scheme_year = ?
                    """,
                    (url_str, record.scheme_name, record.scheme_year),
                )
            else:
                cursor.execute(
                    """
                    SELECT id FROM scholarship_records
                    WHERE official_source_url = ? AND scheme_name = ? AND (scheme_year IS NULL OR scheme_year = '')
                    """,
                    (url_str, record.scheme_name),
                )
            existing = cursor.fetchone()

            if existing:
                record_id = existing["id"]
                effective_verified_ts = (
                    last_verified_str if record.verification_status == VerificationStatus.VERIFIED else None
                )
                cursor.execute(
                    """
                    UPDATE scholarship_records
                    SET source_id = COALESCE(?, source_id),
                        government_authority = ?,
                        description = ?,
                        eligibility_criteria = ?,
                        scholarship_amount = ?,
                        application_deadline = ?,
                        application_deadline_raw = ?,
                        required_documents = ?,
                        supporting_quotations = ?,
                        field_evidence = ?,
                        last_verified_timestamp = ?,
                        verification_status = ?,
                        verification_notes = ?,
                        scheme_year = ?,
                        application_status = ?,
                        guideline_url = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        source_id,
                        record.government_authority,
                        record.description,
                        eligibility_json,
                        record.scholarship_amount,
                        record.application_deadline,
                        record.application_deadline_raw,
                        documents_json,
                        quotations_json,
                        field_evidence_json,
                        effective_verified_ts,
                        record.verification_status.value,
                        record.verification_notes,
                        record.scheme_year,
                        record.application_status,
                        record.guideline_url,
                        now,
                        record_id,
                    ),
                )
                conn.commit()
                record.id = record_id
                return record, False
            else:
                cursor.execute(
                    """
                    INSERT INTO scholarship_records (
                        source_id, scheme_name, government_authority, description,
                        eligibility_criteria, scholarship_amount, application_deadline,
                        application_deadline_raw, required_documents, official_source_url,
                        supporting_quotations, field_evidence, last_verified_timestamp,
                        verification_status, verification_notes, scheme_year,
                        application_status, guideline_url, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        record.scheme_name,
                        record.government_authority,
                        record.description,
                        eligibility_json,
                        record.scholarship_amount,
                        record.application_deadline,
                        record.application_deadline_raw,
                        documents_json,
                        url_str,
                        quotations_json,
                        field_evidence_json,
                        last_verified_str,
                        record.verification_status.value,
                        record.verification_notes,
                        record.scheme_year,
                        record.application_status,
                        record.guideline_url,
                        now,
                        now,
                    ),
                )
                conn.commit()
                record.id = cursor.lastrowid
                return record, True

    def get_scholarship_records(
        self,
        verification_status: Optional[VerificationStatus] = None,
        source_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[ScholarshipRecord]:
        """Query stored scholarship records with optional filters."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM scholarship_records WHERE 1=1"
            params: List[Any] = []

            if verification_status:
                query += " AND verification_status = ?"
                params.append(verification_status.value)
            if source_id:
                query += " AND source_id = ?"
                params.append(source_id)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [self._row_to_scholarship_record(r) for r in rows]

    def _row_to_scholarship_record(self, row: sqlite3.Row) -> ScholarshipRecord:
        """Convert a database row into ScholarshipRecord model."""
        keys = row.keys()
        return ScholarshipRecord(
            id=row["id"],
            scheme_name=row["scheme_name"],
            government_authority=row["government_authority"],
            description=row["description"],
            eligibility_criteria=json.loads(row["eligibility_criteria"] or "[]"),
            scholarship_amount=row["scholarship_amount"],
            application_deadline=row["application_deadline"],
            application_deadline_raw=row["application_deadline_raw"] if "application_deadline_raw" in keys else None,
            required_documents=json.loads(row["required_documents"] or "[]"),
            official_source_url=row["official_source_url"],
            supporting_quotations=json.loads(row["supporting_quotations"] or "[]"),
            field_evidence=json.loads(row["field_evidence"] or "{}") if ("field_evidence" in keys and row["field_evidence"]) else {},
            last_verified_timestamp=_from_iso(row["last_verified_timestamp"]),
            verification_status=VerificationStatus(row["verification_status"]),
            verification_notes=row["verification_notes"] if "verification_notes" in keys else None,
            scheme_year=row["scheme_year"] if "scheme_year" in keys else None,
            application_status=row["application_status"] if "application_status" in keys else None,
            guideline_url=row["guideline_url"] if "guideline_url" in keys else None,
        )

    def audit_and_reverify_stored_records(self) -> None:
        """Re-evaluate stored records against conservative verification rules.

        Corrects any previously misclassified records in the database.
        """
        from backend.app.ingestion.quality import evaluate_verification_status

        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scholarship_records")
            rows = cursor.fetchall()
            for r in rows:
                eligibility = json.loads(r["eligibility_criteria"] or "[]")
                quotes = json.loads(r["supporting_quotations"] or "[]")
                field_ev = json.loads(r["field_evidence"] or "{}") if ("field_evidence" in r.keys() and r["field_evidence"]) else {}

                status, explanation = evaluate_verification_status(
                    scheme_name=r["scheme_name"],
                    government_authority=r["government_authority"],
                    scholarship_amount=r["scholarship_amount"],
                    application_deadline=r["application_deadline"],
                    eligibility_criteria=eligibility,
                    supporting_quotations=quotes,
                    field_evidence=field_ev,
                )

                last_v = r["last_verified_timestamp"] if status == VerificationStatus.VERIFIED else None

                cursor.execute(
                    """
                    UPDATE scholarship_records
                    SET verification_status = ?,
                        verification_notes = ?,
                        last_verified_timestamp = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (status.value, explanation, last_v, now, r["id"]),
                )

            # Update sources verification status accordingly
            cursor.execute("SELECT id FROM sources")
            source_ids = [row["id"] for row in cursor.fetchall()]
            for s_id in source_ids:
                cursor.execute(
                    "SELECT COUNT(*) FROM scholarship_records WHERE source_id = ? AND verification_status = 'verified'",
                    (s_id,),
                )
                verified_count = cursor.fetchone()[0]
                if verified_count == 0:
                    cursor.execute(
                        """
                        UPDATE sources
                        SET verification_status = 'unverified',
                            verified = 0
                        WHERE id = ? AND verification_status = 'verified'
                        """,
                        (s_id,),
                    )
            conn.commit()

    def migrate_legacy_uniqueness_constraint(self) -> bool:
        """Migrate a database created with legacy UNIQUE(official_source_url, scheme_name)
        to academic-year-aware uniqueness UNIQUE(official_source_url, scheme_name, scheme_year).

        Safely preserves all existing records, IDs, text fields, and timestamps.
        Returns True if a migration was performed, False if already up to date.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='scholarship_records'"
            )
            row = cursor.fetchone()
            if not row or not row["sql"]:
                return False

            sql = row["sql"]
            # Check if table has the obsolete constraint without scheme_year
            has_legacy = bool(
                re.search(
                    r"UNIQUE\s*\(\s*[`\"]?official_source_url[`\"]?\s*,\s*[`\"]?scheme_name[`\"]?\s*\)",
                    sql,
                    re.IGNORECASE,
                )
            )
            if not has_legacy:
                return False

            # Create new table with updated constraint
            cursor.execute(
                """
                CREATE TABLE scholarship_records_v2 (
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
                    scheme_year TEXT,
                    application_status TEXT,
                    guideline_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(official_source_url, scheme_name, scheme_year)
                )
                """
            )

            cursor.execute("PRAGMA table_info(scholarship_records)")
            old_cols = [col["name"] for col in cursor.fetchall()]
            target_cols = [
                "id", "source_id", "scheme_name", "government_authority", "description",
                "eligibility_criteria", "scholarship_amount", "application_deadline",
                "application_deadline_raw", "required_documents", "official_source_url",
                "supporting_quotations", "field_evidence", "last_verified_timestamp",
                "verification_status", "verification_notes", "scheme_year",
                "application_status", "guideline_url", "created_at", "updated_at",
            ]
            common_cols = [c for c in target_cols if c in old_cols]
            cols_str = ", ".join(common_cols)

            cursor.execute(
                f"INSERT INTO scholarship_records_v2 ({cols_str}) SELECT {cols_str} FROM scholarship_records"
            )
            cursor.execute("DROP TABLE scholarship_records")
            cursor.execute("ALTER TABLE scholarship_records_v2 RENAME TO scholarship_records")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_url_scheme ON scholarship_records(official_source_url, scheme_name)"
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_url_scheme_year ON scholarship_records(official_source_url, scheme_name, ifnull(scheme_year, ''))"
            )
            conn.commit()
            return True


_db_instance: Optional[Database] = None


def get_db(db_path: Optional[str] = None) -> Database:
    """Singleton-style database provider."""
    global _db_instance
    if db_path is None:
        from backend.app.config import settings

        db_path = settings.database_path
    if _db_instance is None or str(_db_instance.db_path) != str(db_path):
        _db_instance = Database(db_path)
    return _db_instance

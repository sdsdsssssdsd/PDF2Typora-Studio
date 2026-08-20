"""SQLite database schema, migration, and connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("database")

CURRENT_SCHEMA_VERSION = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_number INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'WAITING',
    image_path TEXT,
    markdown_path TEXT,
    json_path TEXT,
    model_name TEXT,
    provider TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    prompt_hash TEXT,
    image_hash TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS figures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_number INTEGER NOT NULL,
    figure_index INTEGER NOT NULL,
    file_path TEXT,
    bbox TEXT,
    status TEXT NOT NULL DEFAULT 'WAITING',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    page_number INTEGER,
    payload TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_number INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'transcribe',
    provider TEXT NOT NULL,
    model TEXT,
    model_name TEXT,
    model_digest TEXT,
    request_hash TEXT,
    prompt_hash TEXT,
    image_hash TEXT,
    schema_version TEXT,
    temperature REAL,
    context_length INTEGER,
    think INTEGER,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    total_duration_ns INTEGER,
    load_duration_ns INTEGER,
    prompt_eval_count INTEGER,
    prompt_eval_duration_ns INTEGER,
    eval_count INTEGER,
    eval_duration_ns INTEGER,
    response_length INTEGER,
    duration_ms INTEGER,
    artifact_path TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_number INTEGER,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_stage_states (
    page_number INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_path TEXT,
    settings_hash TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (page_number, stage)
);

CREATE TABLE IF NOT EXISTS batch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_pages TEXT NOT NULL,
    primary_model TEXT,
    primary_model_digest TEXT,
    fallback_model TEXT,
    fallback_model_digest TEXT,
    prompt_hash TEXT,
    validator_version TEXT,
    total_pages INTEGER DEFAULT 0,
    completed_pages INTEGER DEFAULT 0,
    review_pages INTEGER DEFAULT 0,
    failed_pages INTEGER DEFAULT 0,
    cached_pages INTEGER DEFAULT 0,
    skipped_pages INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_items (
    run_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    selected_model TEXT,
    selected_model_digest TEXT,
    request_hash TEXT,
    artifact_path TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, page_number)
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def initialize(self) -> None:
        """Create schema if needed and apply safe migrations."""
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()
        self.migrate()

    def migrate(self) -> None:
        conn = self.connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
            current = 1
        else:
            current = int(row["version"])

        if current < 2:
            logger.info("Migrating database %s: v%d → v2", self.db_path, current)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_stage_states (
                    page_number INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_path TEXT,
                    settings_hash TEXT,
                    error_message TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (page_number, stage)
                )
                """
            )
            current = 2

        if current < 3:
            logger.info("Migrating database %s: v%d → v3", self.db_path, current)
            self._ensure_ai_requests_v3(conn)
            current = 3

        if current < 4:
            logger.info("Migrating database %s: v%d → v4", self.db_path, current)
            self._ensure_batch_tables_v4(conn)
            current = 4

        if current < 5:
            logger.info("Migrating database %s: v%d → v5", self.db_path, current)
            self._ensure_figures_v5(conn)
            current = 5

        if current < 6:
            logger.info("Migrating database %s: v%d → v6", self.db_path, current)
            self._ensure_figures_v6(conn)
            current = 6

        if current < 7:
            logger.info("Migrating database %s: v%d → v7", self.db_path, current)
            self._ensure_assemble_v7(conn)
            current = 7

        if current < 8:
            logger.info("Migrating database %s: v%d → v8", self.db_path, current)
            self._ensure_cleaner_v8(conn)
            current = 8

        if current < 9:
            logger.info("Migrating database %s: v%d → v9", self.db_path, current)
            self._ensure_export_v9(conn)
            current = 9

        if current < 10:
            logger.info("Migrating database %s: v%d → v10", self.db_path, current)
            self._ensure_quality_v10(conn)
            current = 10

        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.commit()

    def _ensure_ai_requests_v3(self, conn: sqlite3.Connection) -> None:
        """Add Phase 4 columns to ai_requests without dropping data."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(ai_requests)").fetchall()
        }
        additions = {
            "stage": "TEXT NOT NULL DEFAULT 'transcribe'",
            "model": "TEXT",
            "model_digest": "TEXT",
            "request_hash": "TEXT",
            "image_hash": "TEXT",
            "schema_version": "TEXT",
            "temperature": "REAL",
            "context_length": "INTEGER",
            "think": "INTEGER",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "total_duration_ns": "INTEGER",
            "load_duration_ns": "INTEGER",
            "prompt_eval_count": "INTEGER",
            "prompt_eval_duration_ns": "INTEGER",
            "eval_count": "INTEGER",
            "eval_duration_ns": "INTEGER",
            "artifact_path": "TEXT",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE ai_requests ADD COLUMN {name} {decl}")

    def _ensure_batch_tables_v4(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_pages TEXT NOT NULL,
                primary_model TEXT,
                primary_model_digest TEXT,
                fallback_model TEXT,
                fallback_model_digest TEXT,
                prompt_hash TEXT,
                validator_version TEXT,
                total_pages INTEGER DEFAULT 0,
                completed_pages INTEGER DEFAULT 0,
                review_pages INTEGER DEFAULT 0,
                failed_pages INTEGER DEFAULT 0,
                cached_pages INTEGER DEFAULT 0,
                skipped_pages INTEGER DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_items (
                run_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0,
                selected_model TEXT,
                selected_model_digest TEXT,
                request_hash TEXT,
                artifact_path TEXT,
                error_code TEXT,
                error_message TEXT,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, page_number)
            )
            """
        )

    def _ensure_figures_v5(self, conn: sqlite3.Connection) -> None:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(figures)").fetchall()
        }
        additions = {
            "figure_type": "TEXT",
            "caption": "TEXT",
            "marker": "TEXT",
            "ai_bbox_1000": "TEXT",
            "matched_bbox_1000": "TEXT",
            "resolved_bbox_1000": "TEXT",
            "pdf_bbox": "TEXT",
            "source_method": "TEXT",
            "xref": "INTEGER",
            "source_digest": "TEXT",
            "artifact_hash": "TEXT",
            "match_score": "REAL",
            "auto_resolved": "INTEGER DEFAULT 0",
            "manually_adjusted": "INTEGER DEFAULT 0",
            "warnings": "TEXT",
            "error_code": "TEXT",
            "error_message": "TEXT",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE figures ADD COLUMN {name} {decl}")

    def _ensure_figures_v6(self, conn: sqlite3.Connection) -> None:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(figures)").fetchall()
        }
        additions = {
            "marker_original": "TEXT",
            "marker_normalized": "TEXT",
            "marker_repair_type": "TEXT",
            "marker_repaired": "INTEGER DEFAULT 0",
            "manual_bbox_1000": "TEXT",
            "review_status": "TEXT",
            "review_action": "TEXT",
            "reviewed_at": "TEXT",
            "selected_candidate_id": "TEXT",
            "manually_removed_marker": "INTEGER DEFAULT 0",
            "manual_marker_offset": "INTEGER",
            "manual_marker_before_context": "TEXT",
            "manual_marker_after_context": "TEXT",
            "manually_inserted_marker": "INTEGER DEFAULT 0",
            "manual_marker_reassociation": "INTEGER DEFAULT 0",
            "marker_md_index": "INTEGER",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE figures ADD COLUMN {name} {decl}")

    def _ensure_assemble_v7(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS continuity_patches (
                left_page INTEGER NOT NULL,
                right_page INTEGER NOT NULL,
                action TEXT NOT NULL,
                custom_text TEXT,
                left_context TEXT,
                right_context TEXT,
                source_hash_left TEXT,
                source_hash_right TEXT,
                manually_reviewed INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (left_page, right_page)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assemble_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                assembly_hash TEXT,
                output_path TEXT,
                page_count INTEGER DEFAULT 0,
                resolved_source_count INTEGER DEFAULT 0,
                canonical_source_count INTEGER DEFAULT 0,
                continuity_candidates INTEGER DEFAULT 0,
                continuity_patches INTEGER DEFAULT 0,
                warning_count INTEGER DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL UNIQUE,
                path TEXT,
                hash TEXT,
                source_hash TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _ensure_cleaner_v8(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cleaner_reviews (
                page_number INTEGER PRIMARY KEY,
                source_hash TEXT,
                proposal_hash TEXT,
                status TEXT NOT NULL,
                blocking_issues_json TEXT,
                warnings_json TEXT,
                decision TEXT,
                acceptance_mode TEXT,
                manually_edited INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

    def _ensure_export_v9(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                project_name TEXT,
                source_final_hash TEXT,
                export_path TEXT,
                markdown_path TEXT,
                include_source_pdf INTEGER DEFAULT 1,
                figure_count INTEGER DEFAULT 0,
                export_hash TEXT,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    def _ensure_quality_v10(self, conn: sqlite3.Connection) -> None:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(figures)").fetchall()
        }
        additions = {
            "figure_label": "TEXT",
            "figure_group_id": "TEXT",
            "subfigures_json": "TEXT",
            "force_pdf_clip": "INTEGER DEFAULT 0",
        }
        for name, typ in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE figures ADD COLUMN {name} {typ}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_layout_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_number INTEGER NOT NULL,
                layout_path TEXT,
                pdf_hash TEXT,
                caption_count INTEGER DEFAULT 0,
                candidate_count INTEGER DEFAULT 0,
                group_count INTEGER DEFAULT 0,
                reconcile_status TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    def get_schema_version(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row["version"]) if row else 0

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

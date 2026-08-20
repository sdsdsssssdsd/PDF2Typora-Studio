"""Data access layer for project database."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import PageStatus, PipelineStage, StageStatus
from storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_project(
        self,
        name: str,
        source_path: str,
        page_count: int,
    ) -> int:
        conn = self._db.connect()
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO projects (name, source_path, page_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, source_path, page_count, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)

    def get_project(self) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM projects ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def init_pages(self, page_count: int) -> None:
        conn = self._db.connect()
        now = _now()
        for n in range(1, page_count + 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO pages
                    (page_number, status, retry_count, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (n, PageStatus.WAITING.value, now, now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO page_stage_states
                    (page_number, stage, status, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (n, PipelineStage.RENDER.value, StageStatus.WAITING.value, now),
            )
        conn.commit()

    def get_page(self, page_number: int) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM pages WHERE page_number = ?", (page_number,)
        ).fetchone()
        return dict(row) if row else None

    def list_pages(self) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM pages ORDER BY page_number"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_page_status(
        self,
        page_number: int,
        status: PageStatus,
        error_message: str | None = None,
        image_path: str | None = None,
        image_hash: str | None = None,
    ) -> None:
        conn = self._db.connect()
        fields = ["status = ?", "error_message = ?", "updated_at = ?"]
        values: list[Any] = [status.value, error_message, _now()]
        if image_path is not None:
            fields.append("image_path = ?")
            values.append(image_path)
        if image_hash is not None:
            fields.append("image_hash = ?")
            values.append(image_hash)
        values.append(page_number)
        conn.execute(
            f"UPDATE pages SET {', '.join(fields)} WHERE page_number = ?",
            values,
        )
        conn.commit()

    def ensure_render_stages(self, page_count: int) -> None:
        """Backfill waiting render stage rows for existing projects."""
        conn = self._db.connect()
        now = _now()
        for n in range(1, page_count + 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO page_stage_states
                    (page_number, stage, status, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (n, PipelineStage.RENDER.value, StageStatus.WAITING.value, now),
            )
        conn.commit()

    def count_pages_by_status(self) -> dict[str, int]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM pages GROUP BY status"
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    # ---- page_stage_states -------------------------------------------------

    def upsert_stage_state(
        self,
        page_number: int,
        stage: PipelineStage | str,
        status: StageStatus | str,
        *,
        artifact_path: str | None = None,
        settings_hash: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        stage_v = stage.value if isinstance(stage, PipelineStage) else stage
        status_v = status.value if isinstance(status, StageStatus) else status
        now = _now()
        conn = self._db.connect()
        existing = conn.execute(
            """
            SELECT started_at FROM page_stage_states
            WHERE page_number = ? AND stage = ?
            """,
            (page_number, stage_v),
        ).fetchone()
        start = started_at
        if start is None and existing:
            start = existing["started_at"]
        if start is None and status_v == StageStatus.RUNNING.value:
            start = now
        conn.execute(
            """
            INSERT INTO page_stage_states (
                page_number, stage, status, artifact_path, settings_hash,
                error_message, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_number, stage) DO UPDATE SET
                status = excluded.status,
                artifact_path = COALESCE(excluded.artifact_path, page_stage_states.artifact_path),
                settings_hash = COALESCE(excluded.settings_hash, page_stage_states.settings_hash),
                error_message = excluded.error_message,
                started_at = COALESCE(excluded.started_at, page_stage_states.started_at),
                finished_at = excluded.finished_at,
                updated_at = excluded.updated_at
            """,
            (
                page_number,
                stage_v,
                status_v,
                artifact_path,
                settings_hash,
                error_message,
                start,
                finished_at,
                now,
            ),
        )
        conn.commit()

    def get_stage_state(
        self,
        page_number: int,
        stage: PipelineStage | str,
    ) -> dict[str, Any] | None:
        stage_v = stage.value if isinstance(stage, PipelineStage) else stage
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT * FROM page_stage_states
            WHERE page_number = ? AND stage = ?
            """,
            (page_number, stage_v),
        ).fetchone()
        return dict(row) if row else None

    def list_stage_states(
        self, stage: PipelineStage | str | None = None
    ) -> list[dict[str, Any]]:
        conn = self._db.connect()
        if stage is None:
            rows = conn.execute(
                "SELECT * FROM page_stage_states ORDER BY page_number, stage"
            ).fetchall()
        else:
            stage_v = stage.value if isinstance(stage, PipelineStage) else stage
            rows = conn.execute(
                """
                SELECT * FROM page_stage_states
                WHERE stage = ?
                ORDER BY page_number
                """,
                (stage_v,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_stage_by_status(self, stage: PipelineStage | str) -> dict[str, int]:
        stage_v = stage.value if isinstance(stage, PipelineStage) else stage
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT status, COUNT(*) as cnt FROM page_stage_states
            WHERE stage = ?
            GROUP BY status
            """,
            (stage_v,),
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    def insert_ai_request(
        self,
        *,
        page_number: int,
        provider: str,
        model: str,
        status: str,
        model_digest: str | None = None,
        request_hash: str | None = None,
        prompt_hash: str | None = None,
        image_hash: str | None = None,
        schema_version: str | None = None,
        temperature: float | None = None,
        context_length: int | None = None,
        think: bool | None = None,
        artifact_path: str | None = None,
    ) -> int:
        conn = self._db.connect()
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO ai_requests (
                page_number, stage, provider, model, model_name, model_digest,
                request_hash, prompt_hash, image_hash, schema_version,
                temperature, context_length, think, status,
                started_at, artifact_path, created_at
            ) VALUES (?, 'transcribe', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_number,
                provider,
                model,
                model,
                model_digest,
                request_hash,
                prompt_hash,
                image_hash,
                schema_version,
                temperature,
                context_length,
                None if think is None else (1 if think else 0),
                status,
                now,
                artifact_path,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    def update_ai_request(self, request_id: int, **fields: Any) -> None:
        if not fields:
            return
        conn = self._db.connect()
        fields = dict(fields)
        fields["finished_at"] = fields.get("finished_at") or _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [request_id]
        conn.execute(f"UPDATE ai_requests SET {cols} WHERE id = ?", values)
        conn.commit()

    def insert_batch_run(
        self,
        *,
        status: str,
        requested_pages: str,
        primary_model: str | None = None,
        primary_model_digest: str | None = None,
        fallback_model: str | None = None,
        fallback_model_digest: str | None = None,
        prompt_hash: str | None = None,
        validator_version: str | None = None,
        total_pages: int = 0,
        skipped_pages: int = 0,
        stage: str = "transcribe",
    ) -> int:
        conn = self._db.connect()
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO batch_runs (
                stage, status, requested_pages,
                primary_model, primary_model_digest,
                fallback_model, fallback_model_digest,
                prompt_hash, validator_version,
                total_pages, skipped_pages,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stage,
                status,
                requested_pages,
                primary_model,
                primary_model_digest,
                fallback_model,
                fallback_model_digest,
                prompt_hash,
                validator_version,
                total_pages,
                skipped_pages,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    def update_batch_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        conn = self._db.connect()
        fields = dict(fields)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [run_id]
        conn.execute(f"UPDATE batch_runs SET {cols} WHERE id = ?", values)
        conn.commit()

    def get_batch_run(self, run_id: int) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM batch_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_unfinished_batch_run(self) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT * FROM batch_runs
            WHERE status IN ('RUNNING', 'PAUSED', 'INTERRUPTED',
                             'WARMING_MODEL', 'CREATED', 'CANCELLING')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def insert_batch_item(
        self,
        run_id: int,
        page_number: int,
        status: str = "WAITING",
    ) -> None:
        conn = self._db.connect()
        now = _now()
        conn.execute(
            """
            INSERT OR REPLACE INTO batch_items
                (run_id, page_number, status, attempt_count, updated_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (run_id, page_number, status, now),
        )
        conn.commit()

    def update_batch_item(
        self, run_id: int, page_number: int, **fields: Any
    ) -> None:
        if not fields:
            return
        conn = self._db.connect()
        fields = dict(fields)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [run_id, page_number]
        conn.execute(
            f"UPDATE batch_items SET {cols} WHERE run_id = ? AND page_number = ?",
            values,
        )
        conn.commit()

    def get_batch_item(self, run_id: int, page_number: int) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM batch_items WHERE run_id = ? AND page_number = ?",
            (run_id, page_number),
        ).fetchone()
        return dict(row) if row else None

    def list_batch_items(self, run_id: int) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM batch_items
            WHERE run_id = ?
            ORDER BY page_number
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def next_waiting_batch_item(self, run_id: int) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT * FROM batch_items
            WHERE run_id = ? AND status = 'WAITING'
            ORDER BY page_number
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def recover_interrupted_batches(self) -> int:
        """Stuck RUNNING workers become PAUSED / WAITING after crash."""
        conn = self._db.connect()
        now = _now()
        runs = conn.execute(
            """
            SELECT id FROM batch_runs
            WHERE status IN ('RUNNING', 'WARMING_MODEL', 'CANCELLING',
                             'QUALIFYING_MODEL')
            """
        ).fetchall()
        count = 0
        for row in runs:
            run_id = int(row["id"])
            conn.execute(
                """
                UPDATE batch_runs SET status = 'INTERRUPTED', updated_at = ?
                WHERE id = ?
                """,
                (now, run_id),
            )
            conn.execute(
                """
                UPDATE batch_items SET status = 'WAITING', updated_at = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (now, run_id),
            )
            count += 1
        conn.commit()
        return count

    def list_review_pages(self) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM page_stage_states
            WHERE stage = 'transcribe'
              AND status IN ('needs_review', 'failed')
            ORDER BY page_number
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_figure(
        self,
        *,
        page_number: int,
        figure_index: int,
        status: str,
        file_path: str | None = None,
        marker: str | None = None,
        figure_type: str | None = None,
        caption: str | None = None,
        ai_bbox_1000: tuple[int, int, int, int] | None = None,
        matched_bbox_1000: tuple[int, int, int, int] | None = None,
        resolved_bbox_1000: tuple[int, int, int, int] | None = None,
        manual_bbox_1000: tuple[int, int, int, int] | None = None,
        pdf_bbox: tuple[float, float, float, float] | None = None,
        source_method: str | None = None,
        xref: int | None = None,
        source_digest: str | None = None,
        artifact_hash: str | None = None,
        match_score: float | None = None,
        auto_resolved: bool = False,
        manually_adjusted: bool = False,
        marker_original: str | None = None,
        marker_normalized: str | None = None,
        marker_repair_type: str | None = None,
        marker_repaired: bool = False,
        selected_candidate_id: str | None = None,
        review_status: str | None = None,
        review_action: str | None = None,
        reviewed_at: str | None = None,
        manually_removed_marker: bool = False,
        manual_marker_offset: int | None = None,
        manual_marker_before_context: str | None = None,
        manual_marker_after_context: str | None = None,
        manually_inserted_marker: bool = False,
        manual_marker_reassociation: bool = False,
        marker_md_index: int | None = None,
        warnings: list[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        import json

        conn = self._db.connect()
        now = _now()

        def _bbox_json(b: tuple | None) -> str | None:
            return json.dumps(list(b)) if b else None

        existing = conn.execute(
            """
            SELECT id FROM figures
            WHERE page_number = ? AND figure_index = ?
            """,
            (page_number, figure_index),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE figures SET
                    status = ?, file_path = ?, bbox = ?, marker = ?,
                    figure_type = ?, caption = ?, ai_bbox_1000 = ?,
                    matched_bbox_1000 = ?, resolved_bbox_1000 = ?, manual_bbox_1000 = ?,
                    pdf_bbox = ?,
                    source_method = ?, xref = ?, source_digest = ?,
                    artifact_hash = ?, match_score = ?, auto_resolved = ?,
                    manually_adjusted = ?, marker_original = ?, marker_normalized = ?,
                    marker_repair_type = ?, marker_repaired = ?,
                    selected_candidate_id = ?, review_status = ?, review_action = ?,
                    reviewed_at = ?, manually_removed_marker = ?,
                    manual_marker_offset = ?, manual_marker_before_context = ?,
                    manual_marker_after_context = ?, manually_inserted_marker = ?,
                    manual_marker_reassociation = ?, marker_md_index = ?,
                    warnings = ?, error_message = ?,
                    updated_at = ?
                WHERE page_number = ? AND figure_index = ?
                """,
                (
                    status,
                    file_path,
                    _bbox_json(ai_bbox_1000),
                    marker,
                    figure_type,
                    caption,
                    _bbox_json(ai_bbox_1000),
                    _bbox_json(matched_bbox_1000),
                    _bbox_json(resolved_bbox_1000),
                    _bbox_json(manual_bbox_1000),
                    _bbox_json(pdf_bbox),
                    source_method,
                    xref,
                    source_digest,
                    artifact_hash,
                    match_score,
                    1 if auto_resolved else 0,
                    1 if manually_adjusted else 0,
                    marker_original,
                    marker_normalized,
                    marker_repair_type,
                    1 if marker_repaired else 0,
                    selected_candidate_id,
                    review_status,
                    review_action,
                    reviewed_at,
                    1 if manually_removed_marker else 0,
                    manual_marker_offset,
                    manual_marker_before_context,
                    manual_marker_after_context,
                    1 if manually_inserted_marker else 0,
                    1 if manual_marker_reassociation else 0,
                    marker_md_index,
                    json.dumps(warnings or []),
                    error_message,
                    now,
                    page_number,
                    figure_index,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO figures (
                    page_number, figure_index, file_path, bbox, status,
                    marker, figure_type, caption,
                    ai_bbox_1000, matched_bbox_1000, resolved_bbox_1000,
                    manual_bbox_1000, pdf_bbox,
                    source_method, xref, source_digest, artifact_hash, match_score,
                    auto_resolved, manually_adjusted,
                    marker_original, marker_normalized, marker_repair_type, marker_repaired,
                    selected_candidate_id, review_status, review_action, reviewed_at,
                    manually_removed_marker, manual_marker_offset,
                    manual_marker_before_context, manual_marker_after_context,
                    manually_inserted_marker, manual_marker_reassociation, marker_md_index,
                    warnings, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_number,
                    figure_index,
                    file_path,
                    _bbox_json(ai_bbox_1000),
                    status,
                    marker,
                    figure_type,
                    caption,
                    _bbox_json(ai_bbox_1000),
                    _bbox_json(matched_bbox_1000),
                    _bbox_json(resolved_bbox_1000),
                    _bbox_json(manual_bbox_1000),
                    _bbox_json(pdf_bbox),
                    source_method,
                    xref,
                    source_digest,
                    artifact_hash,
                    match_score,
                    1 if auto_resolved else 0,
                    1 if manually_adjusted else 0,
                    marker_original,
                    marker_normalized,
                    marker_repair_type,
                    1 if marker_repaired else 0,
                    selected_candidate_id,
                    review_status,
                    review_action,
                    reviewed_at,
                    1 if manually_removed_marker else 0,
                    manual_marker_offset,
                    manual_marker_before_context,
                    manual_marker_after_context,
                    1 if manually_inserted_marker else 0,
                    1 if manual_marker_reassociation else 0,
                    marker_md_index,
                    json.dumps(warnings or []),
                    error_message,
                    now,
                    now,
                ),
            )
        conn.commit()

    def list_figures(
        self, *, status: str | None = None, page_number: int | None = None
    ) -> list[dict[str, Any]]:
        conn = self._db.connect()
        q = "SELECT * FROM figures WHERE 1=1"
        args: list[Any] = []
        if status:
            q += " AND status = ?"
            args.append(status)
        if page_number is not None:
            q += " AND page_number = ?"
            args.append(page_number)
        q += " ORDER BY page_number, figure_index"
        rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def list_figure_review_items(self) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM figures
            WHERE status IN ('needs_review', 'failed')
            ORDER BY page_number, figure_index
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_continuity_patch(
        self,
        *,
        left_page: int,
        right_page: int,
        action: str,
        custom_text: str | None = None,
        left_context: str = "",
        right_context: str = "",
        source_hash_left: str = "",
        source_hash_right: str = "",
        manually_reviewed: bool = True,
    ) -> None:
        conn = self._db.connect()
        now = _now()
        conn.execute(
            """
            INSERT INTO continuity_patches (
                left_page, right_page, action, custom_text,
                left_context, right_context,
                source_hash_left, source_hash_right,
                manually_reviewed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(left_page, right_page) DO UPDATE SET
                action = excluded.action,
                custom_text = excluded.custom_text,
                left_context = excluded.left_context,
                right_context = excluded.right_context,
                source_hash_left = excluded.source_hash_left,
                source_hash_right = excluded.source_hash_right,
                manually_reviewed = excluded.manually_reviewed,
                updated_at = excluded.updated_at
            """,
            (
                left_page,
                right_page,
                action,
                custom_text,
                left_context,
                right_context,
                source_hash_left,
                source_hash_right,
                1 if manually_reviewed else 0,
                now,
                now,
            ),
        )
        conn.commit()

    def list_continuity_patches(self) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM continuity_patches
            ORDER BY left_page, right_page
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_continuity_patch(
        self, left_page: int, right_page: int
    ) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT * FROM continuity_patches
            WHERE left_page = ? AND right_page = ?
            """,
            (left_page, right_page),
        ).fetchone()
        return dict(row) if row else None

    def delete_continuity_patch(self, left_page: int, right_page: int) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            DELETE FROM continuity_patches
            WHERE left_page = ? AND right_page = ?
            """,
            (left_page, right_page),
        )
        conn.commit()

    def insert_assemble_run(
        self,
        *,
        status: str,
        assembly_hash: str | None = None,
        output_path: str | None = None,
        page_count: int = 0,
        resolved_source_count: int = 0,
        canonical_source_count: int = 0,
        continuity_candidates: int = 0,
        continuity_patches: int = 0,
        warning_count: int = 0,
    ) -> int:
        conn = self._db.connect()
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO assemble_runs (
                status, assembly_hash, output_path, page_count,
                resolved_source_count, canonical_source_count,
                continuity_candidates, continuity_patches, warning_count,
                started_at, finished_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                assembly_hash,
                output_path,
                page_count,
                resolved_source_count,
                canonical_source_count,
                continuity_candidates,
                continuity_patches,
                warning_count,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    def upsert_document_artifact(
        self,
        *,
        artifact_type: str,
        path: str | None,
        content_hash: str | None,
        source_hash: str | None,
        status: str,
    ) -> None:
        conn = self._db.connect()
        now = _now()
        existing = conn.execute(
            "SELECT id FROM document_artifacts WHERE type = ?",
            (artifact_type,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE document_artifacts SET
                    path = ?, hash = ?, source_hash = ?, status = ?, updated_at = ?
                WHERE type = ?
                """,
                (path, content_hash, source_hash, status, now, artifact_type),
            )
        else:
            conn.execute(
                """
                INSERT INTO document_artifacts (
                    type, path, hash, source_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_type, path, content_hash, source_hash, status, now, now),
            )
        conn.commit()

    def get_document_artifact(self, artifact_type: str) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM document_artifacts WHERE type = ?",
            (artifact_type,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_cleaner_review(
        self,
        *,
        page_number: int,
        status: str,
        source_hash: str = "",
        proposal_hash: str = "",
        blocking_issues: list[str] | None = None,
        warnings: list[str] | None = None,
        decision: str | None = None,
        acceptance_mode: str | None = None,
        manually_edited: bool = False,
    ) -> None:
        import json

        conn = self._db.connect()
        now = _now()
        conn.execute(
            """
            INSERT INTO cleaner_reviews (
                page_number, source_hash, proposal_hash, status,
                blocking_issues_json, warnings_json, decision,
                acceptance_mode, manually_edited, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_number) DO UPDATE SET
                source_hash = excluded.source_hash,
                proposal_hash = excluded.proposal_hash,
                status = excluded.status,
                blocking_issues_json = excluded.blocking_issues_json,
                warnings_json = excluded.warnings_json,
                decision = excluded.decision,
                acceptance_mode = excluded.acceptance_mode,
                manually_edited = excluded.manually_edited,
                updated_at = excluded.updated_at
            """,
            (
                page_number,
                source_hash,
                proposal_hash,
                status,
                json.dumps(blocking_issues or []),
                json.dumps(warnings or []),
                decision,
                acceptance_mode,
                1 if manually_edited else 0,
                now,
                now,
            ),
        )
        conn.commit()

    def list_cleaner_reviews(
        self, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        conn = self._db.connect()
        if status:
            rows = conn.execute(
                """
                SELECT * FROM cleaner_reviews
                WHERE status = ?
                ORDER BY page_number
                """,
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cleaner_reviews ORDER BY page_number"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_cleaner_review_items(self) -> list[dict[str, Any]]:
        return self.list_cleaner_reviews(status="needs_review")

    def insert_export_run(
        self,
        *,
        status: str,
        project_name: str | None = None,
        source_final_hash: str | None = None,
        export_path: str | None = None,
        markdown_path: str | None = None,
        include_source_pdf: bool = True,
        figure_count: int = 0,
        export_hash: str | None = None,
        error_message: str | None = None,
    ) -> int:
        conn = self._db.connect()
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO export_runs (
                status, project_name, source_final_hash, export_path,
                markdown_path, include_source_pdf, figure_count, export_hash,
                started_at, finished_at, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                project_name,
                source_final_hash,
                export_path,
                markdown_path,
                1 if include_source_pdf else 0,
                figure_count,
                export_hash,
                now,
                now,
                error_message,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    def get_latest_export_run(self) -> dict[str, Any] | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM export_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

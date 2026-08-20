"""Batch Vision transcription: queue, retry, auto-accept, cache, resume."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai.model_profiles import ModelProfileStore
from ai.schemas.transcription import VALIDATOR_VERSION
from core.models import (
    BatchItemStatus,
    BatchRunStatus,
    ModelQualification,
    PipelineStage,
    StageStatus,
    TranscriptionOptions,
)
from services.transcription_service import TranscriptionAttempt, TranscriptionService
from storage.database import Database
from storage.repository import ProjectRepository
from utils.logger import get_logger
from utils.page_range import parse_page_range
from utils.paths import ensure_dir

logger = get_logger("batch_transcription")

LEAK_RETRY_INSTRUCTION = (
    "Previous attempt incorrectly reproduced task instructions.\n"
    "Only transcribe text visible in the attached page image."
)
URL_RETRY_INSTRUCTION = (
    "Do not output markdown image syntax or image URLs. "
    "Use FIGURE markers only for visuals that appear on the page."
)

TECHNICAL_FAIL = {
    "TIMEOUT",
    "OOM",
    "CONTEXT_OVERFLOW",
    "CONTEXT_EXCEEDED",
    "INVALID_SCHEMA",
    "VALIDATION_FAILED",
    "OLLAMA_OFFLINE",
    "UNKNOWN_ERROR",
    "FAILED",
}


@dataclass
class BatchCreateResult:
    run_id: int
    queued_pages: list[int]
    skipped_unrendered: int


@dataclass
class PageProcessResult:
    page_number: int
    status: str
    cached: bool = False
    fallback_used: bool = False
    duration_s: float = 0.0
    vram: int | None = None
    error_code: str | None = None


class BatchTranscriptionService:
    def __init__(
        self,
        *,
        transcription: TranscriptionService,
        project_root: Path,
        db_path: Path,
        profiles: ModelProfileStore | None = None,
        page_count: int = 0,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.transcription = transcription
        self.project_root = project_root
        self.db_path = db_path
        self.profiles = profiles or ModelProfileStore()
        self.page_count = page_count
        cfg = config or {}
        self.config = cfg
        self.batch_cfg = cfg.get("batch_transcription") or {}
        self.auto_accept = bool(self.batch_cfg.get("auto_accept", True))
        self.use_cache = bool(self.batch_cfg.get("use_cache", True))
        self.unload_on_finish = bool(self.batch_cfg.get("unload_on_finish", True))
        self.max_timeout_retries = int(self.batch_cfg.get("max_timeout_retries", 1))
        self.max_quality_retries = int(self.batch_cfg.get("max_quality_retries", 1))
        self.keep_alive = self.batch_cfg.get("keep_alive", "5m")
        ctx = self.batch_cfg.get("context") or {}
        self.ctx_escalation = int(ctx.get("escalation", 8192))
        self.ctx_maximum = int(ctx.get("maximum", 8192))
        self.page_engine = str(
            (cfg.get("transcription") or {}).get("page_engine") or "vision_only"
        )
    def _repo(self) -> tuple[Database, ProjectRepository]:
        db = Database(self.db_path)
        db.initialize()
        return db, ProjectRepository(db)

    def recover_stale_runs(self) -> int:
        db, repo = self._repo()
        try:
            return repo.recover_interrupted_batches()
        finally:
            db.close()

    def rendered_pages(self, requested: list[int]) -> tuple[list[int], int]:
        db, repo = self._repo()
        try:
            queued: list[int] = []
            skipped = 0
            for page in requested:
                row = repo.get_stage_state(page, PipelineStage.RENDER)
                status = (row or {}).get("status")
                if status in {
                    StageStatus.SUCCESS.value,
                    StageStatus.CACHED.value,
                }:
                    queued.append(page)
                else:
                    skipped += 1
            return queued, skipped
        finally:
            db.close()

    def create_run(
        self,
        *,
        pages: list[int],
        primary_model: str,
        fallback_model: str | None = None,
        require_qualified: bool = True,
        mode: str = "custom",
    ) -> BatchCreateResult:
        digest = self.transcription.get_model_digest(primary_model)
        profile = self.profiles.get(primary_model, digest)
        if require_qualified and profile.qualification != ModelQualification.QUALIFIED:
            raise ValueError(
                f"Primary model is not QUALIFIED ({profile.qualification.value})"
            )
        fb_digest = None
        if fallback_model:
            fb_digest = self.transcription.get_model_digest(fallback_model)
            fb = self.profiles.get(fallback_model, fb_digest)
            if fb.qualification != ModelQualification.QUALIFIED:
                fallback_model = None
                fb_digest = None

        queued, skipped = self.rendered_pages(pages)
        db, repo = self._repo()
        try:
            run_id = repo.insert_batch_run(
                status=BatchRunStatus.CREATED.value,
                requested_pages=json.dumps({"mode": mode, "pages": pages}),
                primary_model=primary_model,
                primary_model_digest=digest,
                fallback_model=fallback_model,
                fallback_model_digest=fb_digest,
                validator_version=VALIDATOR_VERSION,
                total_pages=len(queued),
                skipped_pages=skipped,
            )
            for page in queued:
                repo.insert_batch_item(run_id, page, BatchItemStatus.WAITING.value)
            return BatchCreateResult(
                run_id=run_id,
                queued_pages=queued,
                skipped_unrendered=skipped,
            )
        finally:
            db.close()

    def mark_run(self, run_id: int, status: str, **extra: Any) -> None:
        db, repo = self._repo()
        try:
            if status == BatchRunStatus.RUNNING.value:
                extra.setdefault("started_at", datetime.now(timezone.utc).isoformat())
            if status in {
                BatchRunStatus.COMPLETED.value,
                BatchRunStatus.COMPLETED_WITH_REVIEW.value,
                BatchRunStatus.CANCELLED.value,
                BatchRunStatus.FAILED.value,
                BatchRunStatus.PAUSED.value,
            }:
                extra.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
            repo.update_batch_run(run_id, status=status, **extra)
        finally:
            db.close()

    def refresh_counts(self, run_id: int) -> dict[str, int]:
        db, repo = self._repo()
        try:
            items = repo.list_batch_items(run_id)
            counts = {
                "completed_pages": 0,
                "review_pages": 0,
                "failed_pages": 0,
                "cached_pages": 0,
            }
            for it in items:
                st = it["status"]
                if st == BatchItemStatus.AUTO_ACCEPTED.value:
                    counts["completed_pages"] += 1
                elif st == BatchItemStatus.CACHED.value:
                    counts["cached_pages"] += 1
                    counts["completed_pages"] += 1
                elif st == BatchItemStatus.NEEDS_REVIEW.value:
                    counts["review_pages"] += 1
                elif st == BatchItemStatus.FAILED.value:
                    counts["failed_pages"] += 1
            repo.update_batch_run(run_id, **counts)
            return counts
        finally:
            db.close()

    def next_waiting(self, run_id: int) -> int | None:
        db, repo = self._repo()
        try:
            row = repo.next_waiting_batch_item(run_id)
            return int(row["page_number"]) if row else None
        finally:
            db.close()

    def cancel_remaining(self, run_id: int) -> None:
        db, repo = self._repo()
        try:
            for it in repo.list_batch_items(run_id):
                if it["status"] == BatchItemStatus.WAITING.value:
                    repo.update_batch_item(
                        run_id,
                        int(it["page_number"]),
                        status=BatchItemStatus.CANCELLED.value,
                    )
            self.mark_run(run_id, BatchRunStatus.CANCELLED.value)
        finally:
            db.close()

    def _canonical_hash(self, page_number: int) -> str | None:
        path = self.project_root / "page_results" / f"page_{page_number:04d}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        prov = data.get("provenance") or {}
        return prov.get("request_hash")

    def _options_for_model(self, model: str, digest: str) -> TranscriptionOptions:
        profile = self.profiles.get(model, digest)
        return TranscriptionOptions(
            temperature=0.0,
            num_ctx=profile.preferred_context,
            think=False,
            keep_alive=self.keep_alive,
            use_cache=self.use_cache,
            force=False,
        )

    def process_page(
        self,
        run_id: int,
        page_number: int,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PageProcessResult:
        engine = self.page_engine or "hybrid_ocr_api"
        if engine != "vision_only":
            return self._process_hybrid_page(
                run_id, page_number, cancel_check=cancel_check
            )
        return self._process_vision_page(
            run_id, page_number, cancel_check=cancel_check
        )

    def _process_hybrid_page(
        self,
        run_id: int,
        page_number: int,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PageProcessResult:
        from core.project import Project
        from services.hybrid_transcription_service import HybridTranscriptionService

        started = time.perf_counter()
        db, repo = self._repo()
        try:
            run = repo.get_batch_run(run_id)
            if run is None:
                raise ValueError(f"Unknown batch run {run_id}")
            primary = str(run["primary_model"])
            repo.update_batch_item(
                run_id,
                page_number,
                status=BatchItemStatus.RUNNING.value,
                started_at=datetime.now(timezone.utc).isoformat(),
                selected_model=primary,
            )
        finally:
            db.close()

        if cancel_check and cancel_check():
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.CANCELLED.value,
                model=primary,
                digest="",
                request_hash="",
                error_code="CANCELLED",
            )
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.CANCELLED.value,
                duration_s=time.perf_counter() - started,
                error_code="CANCELLED",
            )

        project = Project.load_from_directory(self.project_root)
        hybrid = HybridTranscriptionService(project, config=self.config or None)
        try:
            result = hybrid.transcribe_page(
                page_number, run_ocr=True, model=primary
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("hybrid batch page failed")
            duration = time.perf_counter() - started
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.FAILED.value,
                model=primary,
                digest="",
                request_hash="",
                error_code="HYBRID_ERROR",
                error_message=str(exc),
            )
            db, repo = self._repo()
            try:
                repo.upsert_stage_state(
                    page_number,
                    PipelineStage.TRANSCRIBE,
                    StageStatus.FAILED,
                    error_message=str(exc),
                )
            finally:
                db.close()
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.FAILED.value,
                duration_s=duration,
                error_code="HYBRID_ERROR",
            )

        duration = time.perf_counter() - started
        if not result.ok or not (result.markdown or "").strip():
            err = result.error or "hybrid_empty_or_failed"
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.FAILED.value,
                model=primary,
                digest="",
                request_hash="",
                artifact=result.evidence_path or None,
                error_code="HYBRID_FAILED",
                error_message=err,
            )
            db, repo = self._repo()
            try:
                repo.upsert_stage_state(
                    page_number,
                    PipelineStage.TRANSCRIBE,
                    StageStatus.FAILED,
                    artifact_path=result.evidence_path or None,
                    error_message=err,
                )
            finally:
                db.close()
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.FAILED.value,
                duration_s=duration,
                error_code="HYBRID_FAILED",
            )

        try:
            path = hybrid.accept_canonical(
                page_number=page_number,
                result=result,
                model=primary,
                batch_run_id=run_id,
                acceptance_mode="auto" if self.auto_accept else "batch_pending",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("hybrid accept failed")
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.FAILED.value,
                model=primary,
                digest="",
                request_hash="",
                error_code="HYBRID_ACCEPT_FAILED",
                error_message=str(exc),
            )
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.FAILED.value,
                duration_s=duration,
                error_code="HYBRID_ACCEPT_FAILED",
            )

        status = (
            BatchItemStatus.AUTO_ACCEPTED.value
            if self.auto_accept
            else BatchItemStatus.NEEDS_REVIEW.value
        )
        # accept_canonical already marks SUCCESS; if not auto_accept keep needs_review note
        if not self.auto_accept or result.needs_review:
            db, repo = self._repo()
            try:
                if not self.auto_accept:
                    repo.upsert_stage_state(
                        page_number,
                        PipelineStage.TRANSCRIBE,
                        StageStatus.NEEDS_REVIEW,
                        artifact_path=str(path),
                    )
                    status = BatchItemStatus.NEEDS_REVIEW.value
            finally:
                db.close()

        self._finish_item(
            run_id,
            page_number,
            status,
            model=primary,
            digest="",
            request_hash=f"hybrid:{primary}:{page_number}",
            artifact=str(path),
        )
        return PageProcessResult(
            page_number=page_number,
            status=status,
            duration_s=duration,
        )

    def _process_vision_page(
        self,
        run_id: int,
        page_number: int,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PageProcessResult:
        started = time.perf_counter()
        db, repo = self._repo()
        try:
            run = repo.get_batch_run(run_id)
            if run is None:
                raise ValueError(f"Unknown batch run {run_id}")
            primary = str(run["primary_model"])
            fallback = run.get("fallback_model") or None
            repo.update_batch_item(
                run_id,
                page_number,
                status=BatchItemStatus.RUNNING.value,
                started_at=datetime.now(timezone.utc).isoformat(),
                selected_model=primary,
            )
        finally:
            db.close()

        image_path = self.project_root / "pages" / f"page_{page_number:04d}.png"
        digest = self.transcription.get_model_digest(primary)
        opts = self._options_for_model(primary, digest)

        # Canonical cache
        prompt = self.transcription.build_system_prompt(page_number, {})
        from utils.hashing import file_sha256, text_sha256, transcription_request_hash
        from ai.schemas.transcription import (
            TRANSCRIPTION_PIPELINE_VERSION,
            TRANSCRIPTION_SCHEMA_VERSION,
        )

        request_hash = transcription_request_hash(
            image_hash=file_sha256(image_path) if image_path.exists() else "",
            page_number=page_number,
            model_name=primary,
            model_digest=digest,
            prompt_hash=text_sha256(prompt),
            schema_version=TRANSCRIPTION_SCHEMA_VERSION,
            options=opts,
            pipeline_version=TRANSCRIPTION_PIPELINE_VERSION,
        )
        canon = self._canonical_hash(page_number)
        if self.use_cache and canon and canon == request_hash:
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.CACHED.value,
                model=primary,
                digest=digest,
                request_hash=request_hash,
            )
            db, repo = self._repo()
            try:
                repo.upsert_stage_state(
                    page_number,
                    PipelineStage.TRANSCRIBE,
                    StageStatus.CACHED,
                    settings_hash=request_hash,
                )
            finally:
                db.close()
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.CACHED.value,
                cached=True,
                duration_s=time.perf_counter() - started,
            )

        attempt, fallback_used = self._run_with_retry(
            page_number=page_number,
            image_path=image_path,
            primary=primary,
            fallback=str(fallback) if fallback else None,
            opts=opts,
            cancel_check=cancel_check,
        )

        vram = (attempt.metrics or {}).get("size_vram")
        duration = time.perf_counter() - started

        if attempt.status == "CANCELLED":
            self._finish_item(
                run_id,
                page_number,
                BatchItemStatus.CANCELLED.value,
                model=attempt.model,
                digest=attempt.model_digest,
                request_hash=attempt.request_hash,
                error_code="CANCELLED",
            )
            return PageProcessResult(
                page_number=page_number,
                status=BatchItemStatus.CANCELLED.value,
                duration_s=duration,
                error_code="CANCELLED",
            )

        if attempt.status == "SUCCESS" and attempt.result is not None:
            if self.auto_accept and attempt.can_auto_accept:
                self.transcription.accept_result(
                    page_number=page_number,
                    attempt=attempt,
                    acceptance_mode="auto",
                    batch_run_id=run_id,
                )
                status = BatchItemStatus.AUTO_ACCEPTED.value
                stage = StageStatus.SUCCESS
            else:
                status = BatchItemStatus.NEEDS_REVIEW.value
                stage = StageStatus.NEEDS_REVIEW
                db, repo = self._repo()
                try:
                    repo.upsert_stage_state(
                        page_number,
                        PipelineStage.TRANSCRIBE,
                        stage,
                        artifact_path=str(attempt.attempt_dir),
                        settings_hash=attempt.request_hash,
                        error_message=";".join(attempt.blocking_codes)
                        or ";".join(attempt.validation_warnings),
                    )
                finally:
                    db.close()
            self._finish_item(
                run_id,
                page_number,
                status,
                model=attempt.model,
                digest=attempt.model_digest,
                request_hash=attempt.request_hash,
                artifact=str(attempt.attempt_dir),
                error_code=";".join(attempt.blocking_codes) or None,
            )
            return PageProcessResult(
                page_number=page_number,
                status=status,
                cached=attempt.cached,
                fallback_used=fallback_used,
                duration_s=duration,
                vram=vram,
            )

        # failed
        self._finish_item(
            run_id,
            page_number,
            BatchItemStatus.FAILED.value,
            model=attempt.model,
            digest=attempt.model_digest,
            request_hash=attempt.request_hash,
            artifact=str(attempt.attempt_dir),
            error_code=attempt.error_code,
            error_message=attempt.error,
        )
        db, repo = self._repo()
        try:
            repo.upsert_stage_state(
                page_number,
                PipelineStage.TRANSCRIBE,
                StageStatus.FAILED,
                artifact_path=str(attempt.attempt_dir),
                settings_hash=attempt.request_hash,
                error_message=attempt.error,
            )
        finally:
            db.close()
        return PageProcessResult(
            page_number=page_number,
            status=BatchItemStatus.FAILED.value,
            fallback_used=fallback_used,
            duration_s=duration,
            vram=vram,
            error_code=attempt.error_code,
        )

    def _run_with_retry(
        self,
        *,
        page_number: int,
        image_path: Path,
        primary: str,
        fallback: str | None,
        opts: TranscriptionOptions,
        cancel_check: Callable[[], bool] | None,
    ) -> tuple[TranscriptionAttempt, bool]:
        extra: str | None = None
        quality_left = self.max_quality_retries
        timeout_left = self.max_timeout_retries
        model = primary
        fallback_used = False
        last: TranscriptionAttempt | None = None

        while True:
            last = self.transcription.transcribe_page(
                page_number=page_number,
                image_path=image_path,
                model=model,
                options=opts,
                extra_user=extra,
                cancel_check=cancel_check,
            )
            extra = None
            if last.status == "CANCELLED":
                return last, fallback_used
            if last.status == "SUCCESS":
                blocking = set(last.blocking_codes)
                if quality_left > 0 and (
                    "prompt_leak_detected" in blocking
                    or "invented_image_reference" in blocking
                ):
                    quality_left -= 1
                    extra = (
                        LEAK_RETRY_INSTRUCTION
                        if "prompt_leak_detected" in blocking
                        else URL_RETRY_INSTRUCTION
                    )
                    opts = TranscriptionOptions(
                        temperature=opts.temperature,
                        num_ctx=opts.num_ctx,
                        think=opts.think,
                        keep_alive=opts.keep_alive,
                        use_cache=False,
                        force=True,
                    )
                    continue
                last.fallback_used = fallback_used
                return last, fallback_used

            code = last.error_code or last.status
            if code == "TIMEOUT" and timeout_left > 0:
                timeout_left -= 1
                continue
            if (
                code in TECHNICAL_FAIL
                and fallback
                and not fallback_used
                and model == primary
            ):
                fb_digest = self.transcription.get_model_digest(fallback)
                fb = self.profiles.get(fallback, fb_digest)
                if fb.qualification == ModelQualification.QUALIFIED:
                    fallback_used = True
                    model = fallback
                    opts = self._options_for_model(fallback, fb_digest)
                    opts = TranscriptionOptions(
                        temperature=opts.temperature,
                        num_ctx=opts.num_ctx,
                        think=False,
                        keep_alive=self.keep_alive,
                        use_cache=False,
                        force=True,
                    )
                    continue
            return last, fallback_used

        assert last is not None
        return last, fallback_used

    def _finish_item(
        self,
        run_id: int,
        page_number: int,
        status: str,
        *,
        model: str,
        digest: str,
        request_hash: str,
        artifact: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        db, repo = self._repo()
        try:
            item = repo.get_batch_item(run_id, page_number)
            attempts = int((item or {}).get("attempt_count") or 0) + 1
            repo.update_batch_item(
                run_id,
                page_number,
                status=status,
                selected_model=model,
                selected_model_digest=digest,
                request_hash=request_hash,
                artifact_path=artifact,
                error_code=error_code,
                error_message=error_message,
                attempt_count=attempts,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            db.close()

    def finalize_run(self, run_id: int, cancelled: bool = False) -> dict[str, Any]:
        counts = self.refresh_counts(run_id)
        if cancelled:
            status = BatchRunStatus.CANCELLED.value
        elif counts["failed_pages"] and counts["completed_pages"] == 0:
            status = BatchRunStatus.FAILED.value
        elif counts["review_pages"]:
            status = BatchRunStatus.COMPLETED_WITH_REVIEW.value
        else:
            status = BatchRunStatus.COMPLETED.value
        self.mark_run(run_id, status)
        report = self.write_report(run_id)
        if self.unload_on_finish:
            try:
                db, repo = self._repo()
                try:
                    run = repo.get_batch_run(run_id)
                    model = (run or {}).get("primary_model")
                finally:
                    db.close()
                if model:
                    self.transcription.provider._client.unload_model(str(model))
            except Exception:  # noqa: BLE001
                logger.debug("unload on finish failed", exc_info=True)
        return report

    def write_report(self, run_id: int) -> dict[str, Any]:
        db, repo = self._repo()
        try:
            run = repo.get_batch_run(run_id) or {}
            items = repo.list_batch_items(run_id)
        finally:
            db.close()

        error_counts: dict[str, int] = {}
        durations: list[float] = []
        peak_vram = 0
        continuity: list[str] = []
        prev_to_next = False
        for it in items:
            code = it.get("error_code") or ""
            if code:
                error_counts[code] = error_counts.get(code, 0) + 1
            art = it.get("artifact_path")
            if art:
                mp = Path(art) / "metrics.json"
                if mp.exists():
                    m = json.loads(mp.read_text(encoding="utf-8"))
                    ns = m.get("total_duration_ns") or 0
                    if ns:
                        durations.append(ns / 1e9)
                    v = m.get("size_vram") or 0
                    if v and v > peak_vram:
                        peak_vram = v
                rp = Path(art) / "response.json"
                if rp.exists():
                    try:
                        res = json.loads(rp.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        res = {}
                    page = int(it["page_number"])
                    if prev_to_next or res.get("continues_from_previous"):
                        continuity.append(f"{page - 1} → {page}")
                    prev_to_next = bool(res.get("continues_to_next"))

        report = {
            "run_id": run_id,
            "pages_requested": run.get("total_pages"),
            "skipped_unrendered": run.get("skipped_pages"),
            "auto_accepted": run.get("completed_pages"),
            "needs_review": run.get("review_pages"),
            "failed": run.get("failed_pages"),
            "cached": run.get("cached_pages"),
            "models": {
                "primary": run.get("primary_model"),
                "fallback": run.get("fallback_model"),
            },
            "average_duration_seconds": (
                sum(durations) / len(durations) if durations else 0
            ),
            "peak_observed_vram_bytes": peak_vram,
            "error_counts": error_counts,
            "continuity_candidates": continuity,
            "status": run.get("status"),
        }
        out_dir = ensure_dir(self.project_root / "reports")
        path = out_dir / f"transcription_batch_{run_id:04d}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def qualify_pages(
        self,
        *,
        model: str,
        pages: list[int],
        num_ctx: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Phase 5A: 3-page schema/leak/URL gate. Digest-bound."""
        digest = self.transcription.get_model_digest(model)
        profile = self.profiles.get(model, digest)
        results: list[dict[str, Any]] = []
        schema_ok = 0
        leak = 0
        url = 0
        opts = TranscriptionOptions(
            temperature=0.0,
            num_ctx=num_ctx if num_ctx is not None else profile.preferred_context,
            think=False,
            keep_alive=self.keep_alive,
            use_cache=False,
            force=True,
        )
        for page in pages:
            image_path = self.project_root / "pages" / f"page_{page:04d}.png"
            attempt = self.transcription.transcribe_page(
                page_number=page,
                image_path=image_path,
                model=model,
                options=opts,
                cancel_check=cancel_check,
            )
            if attempt.status == "SUCCESS" and (
                "prompt_leak_detected" in attempt.blocking_codes
                or "invented_image_reference" in attempt.blocking_codes
            ):
                extra = (
                    LEAK_RETRY_INSTRUCTION
                    if "prompt_leak_detected" in attempt.blocking_codes
                    else URL_RETRY_INSTRUCTION
                )
                retry_opts = TranscriptionOptions(
                    temperature=0.0,
                    num_ctx=opts.num_ctx,
                    think=False,
                    keep_alive=self.keep_alive,
                    use_cache=False,
                    force=True,
                )
                attempt = self.transcription.transcribe_page(
                    page_number=page,
                    image_path=image_path,
                    model=model,
                    options=retry_opts,
                    extra_user=extra,
                    cancel_check=cancel_check,
                )
            entry = {
                "page": page,
                "status": attempt.status,
                "blocking": attempt.blocking_codes,
                "can_auto_accept": attempt.can_auto_accept,
                "duration_s": (attempt.metrics or {}).get("total_duration_ns"),
                "vram": (attempt.metrics or {}).get("size_vram"),
            }
            results.append(entry)
            if attempt.status == "SUCCESS":
                schema_ok += 1
            if "prompt_leak_detected" in attempt.blocking_codes:
                leak += 1
            if "invented_image_reference" in attempt.blocking_codes:
                url += 1

        notes = list(profile.notes)
        if schema_ok == len(pages) and leak == 0 and url == 0:
            profile.qualification = ModelQualification.QUALIFIED
            notes.append(f"Phase 5A QUALIFIED on pages {pages}")
            if num_ctx:
                profile.preferred_context = num_ctx
            profile.successful_pages += schema_ok
        elif schema_ok > 0:
            profile.qualification = ModelQualification.LIMITED
            notes.append(f"Phase 5A LIMITED schema_ok={schema_ok}/{len(pages)}")
            profile.failed_pages += len(pages) - schema_ok
        else:
            profile.qualification = ModelQualification.DISABLED
            notes.append("Phase 5A: no schema-valid pages")
            profile.failed_pages += len(pages)
        profile.notes = notes
        profile.last_tested_at = datetime.now(timezone.utc).isoformat()
        self.profiles.upsert(profile)
        try:
            self.transcription.provider._client.unload_model(model)
        except Exception:  # noqa: BLE001
            logger.debug("qualify unload failed", exc_info=True)
        return {
            "model": model,
            "digest": digest,
            "qualification": profile.qualification.value,
            "schema_ok": schema_ok,
            "leak": leak,
            "url": url,
            "pages": results,
        }


def parse_batch_pages(
    expression: str | None,
    page_count: int,
    *,
    all_untranscribed: bool,
    repo: ProjectRepository,
) -> list[int]:
    if expression:
        return parse_page_range(expression, page_count)
    if all_untranscribed:
        pages: list[int] = []
        for n in range(1, page_count + 1):
            row = repo.get_stage_state(n, PipelineStage.TRANSCRIBE)
            st = (row or {}).get("status")
            if st not in {StageStatus.SUCCESS.value, StageStatus.CACHED.value}:
                pages.append(n)
        return pages
    return list(range(1, page_count + 1))

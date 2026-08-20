"""Freeze clean.md → final.md without rewriting bytes."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.final_models import FINAL_PIPELINE_VERSION, FINAL_VALIDATOR_VERSION
from services.final_readiness_service import FinalReadinessService
from services.final_validator import FinalValidationResult, FinalValidator
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256
from utils.logger import get_logger

logger = get_logger("final_freeze")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class FinalFreezeResult:
    success: bool
    status: str
    cached: bool = False
    clean_sha256: str = ""
    final_sha256: str = ""
    byte_identical: bool = False
    final_path: Path | None = None
    report_path: Path | None = None
    validation: FinalValidationResult | None = None
    readiness: dict[str, Any] = field(default_factory=dict)
    release_warnings: list[str] = field(default_factory=list)
    error: str | None = None
    finalization_hash: str = ""


class FinalFreezeService:
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config or {}
        self.validator = FinalValidator(self.config)
        self.readiness = FinalReadinessService(
            project_root=project_root, db_path=db_path, config=self.config
        )

    def freeze(
        self,
        *,
        force: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> FinalFreezeResult:
        def prog(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        clean_path = self.project_root / "intermediate" / "clean.md"
        final_path = self.project_root / "final.md"
        readiness = self.readiness.summarize()
        if not readiness.get("ready"):
            return FinalFreezeResult(
                success=False,
                status="NOT_READY",
                readiness=readiness,
                error="final_not_ready:" + ",".join(readiness.get("blocking") or []),
            )

        prog("validating clean.md")
        validation = self.validator.validate(
            project_root=self.project_root, clean_path=clean_path
        )
        if not validation.ok:
            return FinalFreezeResult(
                success=False,
                status="VALIDATION_FAILED",
                readiness=readiness,
                validation=validation,
                release_warnings=list(validation.release_warnings),
                error="validation_failed:" + ",".join(validation.blocking),
            )

        clean_sha = file_sha256(clean_path)
        finalization_hash = text_sha256(
            f"{clean_sha}|{FINAL_VALIDATOR_VERSION}|{FINAL_PIPELINE_VERSION}"
        )

        if final_path.exists() and not force:
            existing = file_sha256(final_path)
            if existing == clean_sha:
                art = self._get_artifact()
                if art and art.get("hash") == clean_sha and art.get("status") == "ready":
                    prog("final up-to-date")
                    report = self._write_report(
                        validation=validation,
                        clean_sha=clean_sha,
                        final_sha=existing,
                        byte_identical=True,
                        status="pass",
                        cached=True,
                    )
                    return FinalFreezeResult(
                        success=True,
                        status="UP_TO_DATE",
                        cached=True,
                        clean_sha256=clean_sha,
                        final_sha256=existing,
                        byte_identical=True,
                        final_path=final_path,
                        report_path=report,
                        validation=validation,
                        readiness=readiness,
                        release_warnings=list(validation.release_warnings),
                        finalization_hash=finalization_hash,
                    )

        # Archive previous final before replace
        if final_path.exists():
            prog("archiving previous final.md")
            hist = self.project_root / "history" / "final"
            hist.mkdir(parents=True, exist_ok=True)
            stamp = _stamp()
            shutil.copy2(final_path, hist / f"{stamp}_final.md")

        tmp = self.project_root / "final.md.tmp"
        try:
            prog("copying clean.md → final.md.tmp")
            if tmp.exists():
                tmp.unlink()
            shutil.copyfile(clean_path, tmp)
            final_sha = file_sha256(tmp)
            if final_sha != clean_sha:
                tmp.unlink(missing_ok=True)
                return FinalFreezeResult(
                    success=False,
                    status="FINALIZATION_FAILED",
                    clean_sha256=clean_sha,
                    final_sha256=final_sha,
                    byte_identical=False,
                    readiness=readiness,
                    validation=validation,
                    error="byte_mismatch_after_copy",
                )
            # Re-validate artifact path context (same bytes; images still under project)
            art_val = self.validator.validate(
                project_root=self.project_root, clean_path=tmp
            )
            if not art_val.ok:
                tmp.unlink(missing_ok=True)
                return FinalFreezeResult(
                    success=False,
                    status="FINALIZATION_FAILED",
                    clean_sha256=clean_sha,
                    readiness=readiness,
                    validation=art_val,
                    error="artifact_validation_failed",
                )
            prog("atomic replace final.md")
            tmp.replace(final_path)
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            logger.exception("final freeze failed")
            return FinalFreezeResult(
                success=False,
                status="FINALIZATION_FAILED",
                clean_sha256=clean_sha,
                readiness=readiness,
                validation=validation,
                error=str(exc),
            )

        final_sha = file_sha256(final_path)
        byte_identical = final_sha == clean_sha
        if not byte_identical:
            return FinalFreezeResult(
                success=False,
                status="FINALIZATION_FAILED",
                clean_sha256=clean_sha,
                final_sha256=final_sha,
                byte_identical=False,
                readiness=readiness,
                validation=validation,
                error="post_replace_hash_mismatch",
            )

        self._record_artifact(
            path=str(final_path.relative_to(self.project_root)),
            content_hash=final_sha,
            source_hash=clean_sha,
            status="ready",
        )
        report = self._write_report(
            validation=validation,
            clean_sha=clean_sha,
            final_sha=final_sha,
            byte_identical=True,
            status="pass",
            cached=False,
        )
        # archive report copy into history if we archived
        hist = self.project_root / "history" / "final"
        if hist.exists() and report:
            try:
                shutil.copy2(report, hist / f"{_stamp()}_validation.json")
            except OSError:
                pass

        return FinalFreezeResult(
            success=True,
            status="FINAL_READY",
            cached=False,
            clean_sha256=clean_sha,
            final_sha256=final_sha,
            byte_identical=True,
            final_path=final_path,
            report_path=report,
            validation=validation,
            readiness=readiness,
            release_warnings=list(validation.release_warnings),
            finalization_hash=finalization_hash,
        )

    def validate_only(self) -> FinalFreezeResult:
        readiness = self.readiness.summarize()
        clean_path = self.project_root / "intermediate" / "clean.md"
        validation = self.validator.validate(
            project_root=self.project_root, clean_path=clean_path
        )
        clean_sha = file_sha256(clean_path) if clean_path.exists() else ""
        status = "READY_FOR_FINAL" if validation.ok and readiness.get("ready") else (
            "VALIDATION_FAILED" if not validation.ok else "NOT_READY"
        )
        report = None
        if clean_path.exists():
            report = self._write_report(
                validation=validation,
                clean_sha=clean_sha,
                final_sha="",
                byte_identical=False,
                status=validation.status,
                cached=False,
            )
        return FinalFreezeResult(
            success=validation.ok and bool(readiness.get("ready")),
            status=status,
            clean_sha256=clean_sha,
            validation=validation,
            readiness=readiness,
            release_warnings=list(validation.release_warnings),
            report_path=report,
            error=None if validation.ok else ",".join(validation.blocking),
        )

    def is_final_stale(self) -> bool:
        clean = self.project_root / "intermediate" / "clean.md"
        final = self.project_root / "final.md"
        if not clean.exists() or not final.exists():
            return True
        return file_sha256(clean) != file_sha256(final)

    def _record_artifact(
        self, *, path: str, content_hash: str, source_hash: str, status: str
    ) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_document_artifact(
                artifact_type="final",
                path=path,
                content_hash=content_hash,
                source_hash=source_hash,
                status=status,
            )
        finally:
            db.close()

    def _get_artifact(self) -> dict[str, Any] | None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return repo.get_document_artifact("final")
        finally:
            db.close()

    def _write_report(
        self,
        *,
        validation: FinalValidationResult,
        clean_sha: str,
        final_sha: str,
        byte_identical: bool,
        status: str,
        cached: bool,
    ) -> Path:
        reports = self.project_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"final_validation_{_stamp()}.json"
        payload = {
            "status": status,
            "cached": cached,
            "clean_sha256": clean_sha,
            "final_sha256": final_sha,
            "byte_identical": byte_identical,
            "page_markers": validation.page_markers,
            "figure_markers": validation.figure_markers,
            "image_links_total": validation.image_links_total,
            "image_links_valid": validation.image_links_valid,
            "image_links_missing": validation.image_links_missing,
            "absolute_paths": validation.absolute_paths,
            "unsafe_paths": validation.unsafe_paths,
            "horizontal_rules": validation.horizontal_rules,
            "math_warnings": validation.math_warnings,
            "table_warnings": validation.table_warnings,
            "release_warnings": validation.release_warnings,
            "blocking": validation.blocking,
            "validator_version": validation.validator_version,
            "pipeline_version": FINAL_PIPELINE_VERSION,
            "created_at": _now(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

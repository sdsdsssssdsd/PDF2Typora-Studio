"""Batch Markdown cleaner — rules first, optional AI (Phase 8)."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.config_manager import load_config, project_root
from core.cleaner_models import (
    CLEANER_PIPELINE_VERSION,
    CleanAcceptanceMode,
    CleanerMode,
    PageCleanResult,
)
from core.models import PipelineStage, StageStatus
from services.clean_document_builder import CleanDocumentBuilder
from services.clean_document_validator import CleanDocumentValidator
from services.cleaner_validator import CleanerValidator
from services.cleaning_need_analyzer import CleaningNeedAnalyzer
from services.deterministic_cleaner import DeterministicCleaner
from services.raw_page_splitter import RawPageSplitter
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("batch_cleaner")


class BatchCleanerService:
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        config: dict[str, Any] | None = None,
        text_provider: Any | None = None,
    ) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config or load_config()
        self.cfg = self.config.get("cleaner") or {}
        self.mode = CleanerMode(str(self.cfg.get("mode", "smart")).lower())
        self.use_cache = bool(self.cfg.get("use_cache", True))
        self.splitter = RawPageSplitter()
        self.det = DeterministicCleaner(self.config)
        self.need = CleaningNeedAnalyzer(self.config)
        self.validator = CleanerValidator(self.config)
        self.doc_builder = CleanDocumentBuilder(project_root)
        self.doc_validator = CleanDocumentValidator()
        self.text_provider = text_provider
        self.clean_pages_dir = ensure_dir(project_root / "clean_pages")
        self.raw_path = project_root / "intermediate" / "raw.md"
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "cleanup.txt"
        self.cleanup_prompt = (
            prompt_path.read_text(encoding="utf-8")
            if prompt_path.exists()
            else "Format-only cleanup."
        )
        self.prompt_hash = text_sha256(self.cleanup_prompt)

    def analyze(self, pages: list[int] | None = None) -> dict[str, Any]:
        fragments = self._load_fragments()
        if pages:
            page_set = set(pages)
            fragments = [f for f in fragments if f.page_number in page_set]
        already = 0
        rule_fix = 0
        needs_ai = 0
        details = []
        for frag in fragments:
            label = DeterministicCleaner.load_printed_label(
                self.project_root, frag.page_number
            )
            det = self.det.clean(
                page_number=frag.page_number,
                body=frag.body,
                printed_page_label=label,
            )
            report = self.need.analyze(
                page_number=frag.page_number,
                cleaned_body=det.cleaned,
                deterministic_issues=det.issues,
                project_root=self.project_root,
            )
            if report.already_clean and not det.actions:
                already += 1
                kind = "already_clean"
            elif report.needs_ai:
                needs_ai += 1
                kind = "needs_ai"
            else:
                rule_fix += 1
                kind = "needs_rule_fix"
            details.append(
                {
                    "page": frag.page_number,
                    "kind": kind,
                    "reasons": report.reasons,
                    "actions": [a.get("action") for a in det.actions],
                }
            )
        return {
            "pages": len(fragments),
            "already_clean": already,
            "needs_rule_fix": rule_fix,
            "needs_ai": needs_ai,
            "details": details,
        }

    def process_pages(
        self,
        pages: list[int] | None = None,
        *,
        force: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        pause_check: Callable[[], bool] | None = None,
        on_page: Callable[[PageCleanResult], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self.raw_path.exists():
            return {"success": False, "error": "raw.md missing"}

        fragments = self._load_fragments()
        if pages:
            page_set = set(pages)
            fragments = [f for f in fragments if f.page_number in page_set]

        summary = {
            "pages_requested": len(fragments),
            "rule_cleaned": 0,
            "ai_cleaned": 0,
            "ai_called": 0,
            "needs_review": 0,
            "keep_source": 0,
            "manual": 0,
            "failed": 0,
            "cached": 0,
            "success": True,
        }

        for frag in fragments:
            if cancel_check and cancel_check():
                summary["cancelled"] = True
                break
            while pause_check and pause_check():
                import time

                time.sleep(0.2)
                if cancel_check and cancel_check():
                    break

            if on_progress:
                on_progress(f"Cleaning page {frag.page_number:04d}")

            result = self._clean_one(frag, force=force)
            if result.cached:
                summary["cached"] += 1
            if result.acceptance_mode == CleanAcceptanceMode.RULES.value:
                summary["rule_cleaned"] += 1
            elif result.acceptance_mode == CleanAcceptanceMode.AI.value:
                summary["ai_cleaned"] += 1
            elif result.acceptance_mode == CleanAcceptanceMode.KEEP_SOURCE.value:
                summary["keep_source"] += 1
            elif result.acceptance_mode == CleanAcceptanceMode.MANUAL.value:
                summary["manual"] += 1
            if result.ai_called:
                summary["ai_called"] += 1
            if result.stage_status == StageStatus.NEEDS_REVIEW.value:
                summary["needs_review"] += 1
            if result.stage_status == StageStatus.FAILED.value:
                summary["failed"] += 1
            if on_page:
                on_page(result)

        # Build document if all target pages have clean files
        page_nums = [f.page_number for f in fragments]
        ready_pages = [
            p
            for p in page_nums
            if (self.clean_pages_dir / f"page_{p:04d}.md").exists()
        ]
        doc_result = None
        if len(ready_pages) == len(page_nums) and page_nums:
            doc_result = self.doc_builder.build(ready_pages, force=force)
            if doc_result.success:
                v = self.doc_validator.validate(
                    project_root=self.project_root,
                    expected_pages=page_nums,
                )
                if not v.ok:
                    # keep files but report
                    summary["document_blocking"] = v.blocking
                    summary["success"] = False
                else:
                    summary["document_cached"] = doc_result.cached
                    self._record_document_artifacts(doc_result)
            else:
                summary["document_error"] = doc_result.error
                summary["success"] = False

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = ensure_dir(self.project_root / "reports") / f"cleaner_batch_{run_id}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["report_path"] = str(out)
        summary["analyze"] = self.analyze(page_nums)
        return summary

    def accept_keep_source(self, page_number: int) -> PageCleanResult:
        frag = self._fragment(page_number)
        label = DeterministicCleaner.load_printed_label(self.project_root, page_number)
        det = self.det.clean(
            page_number=page_number, body=frag.body, printed_page_label=label
        )
        path = self._write_clean_page(page_number, det.cleaned)
        self._set_stage(page_number, StageStatus.SUCCESS, str(path))
        self._upsert_review(
            page_number,
            status="accepted",
            decision="keep_source",
            acceptance_mode=CleanAcceptanceMode.KEEP_SOURCE.value,
            source_hash=frag.source_hash,
            proposal_hash=text_sha256(det.cleaned),
        )
        return PageCleanResult(
            page_number=page_number,
            stage_status=StageStatus.SUCCESS.value,
            acceptance_mode=CleanAcceptanceMode.KEEP_SOURCE.value,
            cleaned_path=str(path),
        )

    def accept_cleaned(
        self,
        page_number: int,
        cleaned_markdown: str,
        *,
        manually_edited: bool = False,
        allow_content_change: bool = False,
    ) -> PageCleanResult:
        frag = self._fragment(page_number)
        label = DeterministicCleaner.load_printed_label(self.project_root, page_number)
        det = self.det.clean(
            page_number=page_number, body=frag.body, printed_page_label=label
        )
        preservation = self.validator.validate(source=det.cleaned, cleaned=cleaned_markdown)
        if preservation.blocking and not allow_content_change:
            return PageCleanResult(
                page_number=page_number,
                stage_status=StageStatus.NEEDS_REVIEW.value,
                blocking=[i.code for i in preservation.blocking],
                error="content_preservation_failed",
            )
        path = self._write_clean_page(page_number, cleaned_markdown)
        mode = (
            CleanAcceptanceMode.MANUAL.value
            if manually_edited
            else CleanAcceptanceMode.AI.value
        )
        self._set_stage(page_number, StageStatus.SUCCESS, str(path))
        self._upsert_review(
            page_number,
            status="accepted",
            decision="accept_cleaned",
            acceptance_mode=mode,
            source_hash=frag.source_hash,
            proposal_hash=text_sha256(cleaned_markdown),
            manually_edited=manually_edited,
            blocking=[i.code for i in preservation.blocking],
            warnings=[i.code for i in preservation.issues if i.severity == "WARNING"],
        )
        return PageCleanResult(
            page_number=page_number,
            stage_status=StageStatus.SUCCESS.value,
            acceptance_mode=mode,
            cleaned_path=str(path),
        )

    def _clean_one(self, frag, *, force: bool) -> PageCleanResult:
        page = frag.page_number
        out_path = self.clean_pages_dir / f"page_{page:04d}.md"
        label = DeterministicCleaner.load_printed_label(self.project_root, page)
        det = self.det.clean(
            page_number=page, body=frag.body, printed_page_label=label
        )
        report = self.need.analyze(
            page_number=page,
            cleaned_body=det.cleaned,
            deterministic_issues=det.issues,
            project_root=self.project_root,
        )

        cache_key = self._rules_cache_key(frag.source_hash, det.cleaned)
        if (
            self.use_cache
            and not force
            and out_path.exists()
            and self._page_cache_matches(page, cache_key)
        ):
            self._set_stage(page, StageStatus.CACHED, str(out_path))
            return PageCleanResult(
                page_number=page,
                stage_status=StageStatus.CACHED.value,
                acceptance_mode=CleanAcceptanceMode.CACHED.value,
                cleaned_path=str(out_path),
                cached=True,
                needs_ai=report.needs_ai,
            )

        self._set_stage(page, StageStatus.RUNNING)

        use_ai = False
        if self.mode == CleanerMode.FULL_AI:
            use_ai = True
        elif self.mode == CleanerMode.SMART and report.needs_ai:
            use_ai = True
        elif self.mode == CleanerMode.SAFE_RULES_ONLY:
            use_ai = False

        if not use_ai:
            # rules path — validate against original body for structural safety
            # Deterministic changes should preserve content; validate cleaned vs source body
            # For printed label / HR removal, numeric/prose may change intentionally.
            # Validate math/images against det input after only math conversion...
            # Use source=frag.body with relaxed checks via comparing images always.
            img_ok = self.validator.validate(source=frag.body, cleaned=det.cleaned)
            allowed_soft: set[str] = set()
            actions = {a.get("action") for a in det.actions}
            if "remove_printed_page_label" in actions:
                allowed_soft.update({"numeric_content_changed", "visible_prose_changed"})
            if "remove_horizontal_rule" in actions:
                allowed_soft.add("visible_prose_changed")
            if "remove_outer_markdown_fence" in actions:
                allowed_soft.update({"code_content_changed", "visible_prose_changed"})
            if {
                "convert_parenthesis_math",
                "convert_bracket_math",
            } & actions:
                # format-only math delimiter changes should pass math payload check;
                # if not, keep as blocking
                pass
            hard = [i for i in img_ok.blocking if i.code not in allowed_soft]
            if hard:
                self._set_stage(page, StageStatus.NEEDS_REVIEW)
                self._upsert_review(
                    page,
                    status="needs_review",
                    decision="rules_failed",
                    source_hash=frag.source_hash,
                    proposal_hash=text_sha256(det.cleaned),
                    blocking=[i.code for i in hard],
                )
                return PageCleanResult(
                    page_number=page,
                    stage_status=StageStatus.NEEDS_REVIEW.value,
                    needs_ai=report.needs_ai,
                    blocking=[i.code for i in hard],
                )

            path = self._write_clean_page(page, det.cleaned)
            self._set_stage(page, StageStatus.SUCCESS, str(path))
            self._write_page_cache(page, cache_key)
            self._upsert_review(
                page,
                status="accepted",
                decision="auto_accept_rules",
                acceptance_mode=CleanAcceptanceMode.RULES.value,
                source_hash=frag.source_hash,
                proposal_hash=text_sha256(det.cleaned),
            )
            return PageCleanResult(
                page_number=page,
                stage_status=StageStatus.SUCCESS.value,
                acceptance_mode=CleanAcceptanceMode.RULES.value,
                cleaned_path=str(path),
                needs_ai=report.needs_ai,
                warnings=report.reasons,
            )

        # AI path
        if self.text_provider is None:
            # No provider: keep deterministic source as review or auto keep in SMART
            path = self._write_clean_page(page, det.cleaned)
            self._set_stage(page, StageStatus.NEEDS_REVIEW, str(path))
            self._upsert_review(
                page,
                status="needs_review",
                decision="ai_unavailable",
                source_hash=frag.source_hash,
                proposal_hash=text_sha256(det.cleaned),
                warnings=report.reasons,
            )
            # Still write deterministic clean so document can proceed via keep_source later
            return PageCleanResult(
                page_number=page,
                stage_status=StageStatus.NEEDS_REVIEW.value,
                cleaned_path=str(path),
                needs_ai=True,
                ai_called=False,
                warnings=["ai_provider_unavailable"] + report.reasons,
            )

        ai_text, ai_meta = self._call_ai_cleaner(page, det.cleaned)
        if ai_text is None:
            self._set_stage(page, StageStatus.NEEDS_REVIEW)
            return PageCleanResult(
                page_number=page,
                stage_status=StageStatus.NEEDS_REVIEW.value,
                needs_ai=True,
                ai_called=True,
                error=ai_meta.get("error"),
            )

        preservation = self.validator.validate(source=det.cleaned, cleaned=ai_text)
        if preservation.blocking or ai_meta.get("needs_review"):
            # store proposal but do not accept
            prop_dir = ensure_dir(
                self.project_root / "experiments" / "cleaner" / f"page_{page:04d}"
            )
            (prop_dir / "proposal.md").write_text(ai_text, encoding="utf-8")
            self._set_stage(page, StageStatus.NEEDS_REVIEW)
            self._upsert_review(
                page,
                status="needs_review",
                decision="ai_blocked",
                source_hash=frag.source_hash,
                proposal_hash=text_sha256(ai_text),
                blocking=[i.code for i in preservation.blocking],
            )
            return PageCleanResult(
                page_number=page,
                stage_status=StageStatus.NEEDS_REVIEW.value,
                needs_ai=True,
                ai_called=True,
                blocking=[i.code for i in preservation.blocking],
            )

        path = self._write_clean_page(page, ai_text)
        self._set_stage(page, StageStatus.SUCCESS, str(path))
        self._write_page_cache(page, cache_key)
        self._upsert_review(
            page,
            status="accepted",
            decision="auto_accept_ai",
            acceptance_mode=CleanAcceptanceMode.AI.value,
            source_hash=frag.source_hash,
            proposal_hash=text_sha256(ai_text),
        )
        return PageCleanResult(
            page_number=page,
            stage_status=StageStatus.SUCCESS.value,
            acceptance_mode=CleanAcceptanceMode.AI.value,
            cleaned_path=str(path),
            needs_ai=True,
            ai_called=True,
        )

    def _call_ai_cleaner(self, page: int, markdown: str) -> tuple[str | None, dict]:
        """Optional AI call — returns (cleaned, meta)."""
        try:
            from ai.schemas.cleaner import CLEAN_PAGE_JSON_SCHEMA, CleanPageResult

            # Prefer structured chat if provider supports it
            provider = self.text_provider
            if hasattr(provider, "clean_markdown"):
                raw = provider.clean_markdown(
                    markdown=markdown,
                    page_number=page,
                    prompt=self.cleanup_prompt,
                    schema=CLEAN_PAGE_JSON_SCHEMA,
                )
            elif hasattr(provider, "transcribe_page_structured"):
                # reuse structured chat without image if available — skip
                return None, {"error": "no_text_cleaner_method"}
            else:
                raw = provider.complete(self.cleanup_prompt, markdown)

            if isinstance(raw, dict):
                data = raw
            else:
                data = json.loads(str(raw))
            result = CleanPageResult.model_validate(data)
            return result.cleaned_markdown, {
                "needs_review": result.needs_review,
                "warnings": result.warnings,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI cleaner failed page %s", page)
            return None, {"error": str(exc)}

    def _load_fragments(self):
        return self.splitter.split_file(self.raw_path)

    def _fragment(self, page: int):
        for f in self._load_fragments():
            if f.page_number == page:
                return f
        raise ValueError(f"page {page} not in raw.md")

    def _write_clean_page(self, page: int, content: str) -> Path:
        path = self.clean_pages_dir / f"page_{page:04d}.md"
        if path.exists():
            hist = ensure_dir(
                self.project_root / "history" / "cleaner" / f"page_{page:04d}"
            )
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, hist / f"{stamp}.md")
        tmp = path.with_suffix(".md.tmp")
        body = content.replace("\r\n", "\n").replace("\r", "\n")
        # ensure no PAGE markers in clean_pages
        from services.assembled_markdown_validator import PAGE_MARKER_RE

        body = PAGE_MARKER_RE.sub("", body).strip("\n") + "\n"
        tmp.write_text(body, encoding="utf-8", newline="\n")
        tmp.replace(path)
        return path

    def _rules_cache_key(self, source_hash: str, cleaned: str) -> str:
        return hashlib.sha256(
            f"{source_hash}|{self.det.version}|{text_sha256(cleaned)}|{CLEANER_PIPELINE_VERSION}".encode()
        ).hexdigest()

    def _cache_path(self, page: int) -> Path:
        return ensure_dir(self.project_root / ".cache" / "cleaner") / f"page_{page:04d}.hash"

    def _page_cache_matches(self, page: int, key: str) -> bool:
        p = self._cache_path(page)
        return p.exists() and p.read_text(encoding="utf-8").strip() == key

    def _write_page_cache(self, page: int, key: str) -> None:
        self._cache_path(page).write_text(key + "\n", encoding="utf-8", newline="\n")

    def _set_stage(
        self, page: int, status: StageStatus, artifact: str | None = None
    ) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_stage_state(
                page,
                PipelineStage.CLEAN,
                status,
                artifact_path=artifact,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            db.close()

    def _upsert_review(
        self,
        page: int,
        *,
        status: str,
        decision: str,
        source_hash: str = "",
        proposal_hash: str = "",
        acceptance_mode: str | None = None,
        manually_edited: bool = False,
        blocking: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_cleaner_review(
                page_number=page,
                source_hash=source_hash,
                proposal_hash=proposal_hash,
                status=status,
                blocking_issues=blocking or [],
                warnings=warnings or [],
                decision=decision,
                acceptance_mode=acceptance_mode,
                manually_edited=manually_edited,
            )
        finally:
            db.close()

    def _record_document_artifacts(self, doc_result) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            if doc_result.clean_traced_path and doc_result.clean_traced_path.exists():
                repo.upsert_document_artifact(
                    artifact_type="clean_traced",
                    path="intermediate/clean_traced.md",
                    content_hash=file_sha256(doc_result.clean_traced_path),
                    source_hash=doc_result.document_hash,
                    status="SUCCESS",
                )
            if doc_result.clean_path and doc_result.clean_path.exists():
                repo.upsert_document_artifact(
                    artifact_type="clean",
                    path="intermediate/clean.md",
                    content_hash=file_sha256(doc_result.clean_path),
                    source_hash=doc_result.document_hash,
                    status="SUCCESS",
                )
        finally:
            db.close()

"""Deterministic Markdown assembler → intermediate/raw.md (Phase 7)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.config_manager import load_config
from core.assemble_models import (
    ASSEMBLER_VERSION,
    AssemblyRequest,
    AssemblyResult,
    ContinuityPatchAction,
    PageSourceEntry,
)
from services.assemble_readiness_service import AssembleReadinessService
from services.assembled_markdown_validator import AssembledMarkdownValidator, PAGE_MARKER_RE
from services.continuity_analyzer import ContinuityAnalyzer
from services.page_source_resolver import PageSourceResolver
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("markdown_assembler")


def assembly_hash(
    *,
    page_entries: list[PageSourceEntry],
    patch_digests: list[str],
    assembler_version: str,
    preserve_page_markers: bool,
    allow_unresolved: bool,
) -> str:
    parts = [
        assembler_version,
        f"preserve={int(preserve_page_markers)}",
        f"unresolved={int(allow_unresolved)}",
    ]
    for e in page_entries:
        parts.append(f"{e.page}:{e.source_type}:{e.sha256}")
    parts.extend(sorted(patch_digests))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class MarkdownAssembler:
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config or load_config()
        self.assemble_cfg = self.config.get("assemble") or {}
        self.resolver = PageSourceResolver(project_root=project_root, db_path=db_path)
        self.readiness = AssembleReadinessService(
            project_root=project_root, db_path=db_path, config=self.config
        )
        self.analyzer = ContinuityAnalyzer(self.config)
        self.validator = AssembledMarkdownValidator()
        self.intermediate = ensure_dir(project_root / "intermediate")
        self.raw_path = self.intermediate / "raw.md"
        self.manifest_path = self.intermediate / "assemble_manifest.json"
        self.use_cache = bool(self.assemble_cfg.get("use_cache", True))
        self.archive_previous = bool(
            self.assemble_cfg.get("archive_previous_raw", True)
        )

    def assemble(
        self,
        request: AssemblyRequest | None = None,
        *,
        cancel_check: Callable[[], bool] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> AssemblyResult:
        pages = list(
            request.page_numbers
            if request and request.page_numbers
            else self._all_pages()
        )
        preserve = (
            request.preserve_page_markers
            if request
            else bool(self.assemble_cfg.get("preserve_page_markers", True))
        )
        allow_unresolved = (
            request.allow_unresolved_figures
            if request
            else bool(self.assemble_cfg.get("allow_unresolved_figures", False))
        )
        apply_patches = (
            request.apply_continuity_patches if request else True
        )
        force = request.force if request else False

        def prog(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        cfg = dict(self.config)
        assemble_cfg = dict(cfg.get("assemble") or {})
        assemble_cfg["allow_unresolved_figures"] = allow_unresolved
        cfg["assemble"] = assemble_cfg
        if allow_unresolved:
            figures = dict(cfg.get("figures") or {})
            readiness_cfg = dict(figures.get("readiness") or {})
            readiness_cfg["allow_unresolved_override"] = True
            readiness_cfg["require_all_resolved"] = False
            figures["readiness"] = readiness_cfg
            cfg["figures"] = figures

        readiness_svc = AssembleReadinessService(
            project_root=self.project_root, db_path=self.db_path, config=cfg
        )
        readiness = readiness_svc.summarize(pages)
        if not readiness["ready"]:
            return AssemblyResult(
                success=False,
                error="assemble_not_ready:"
                + ";".join(readiness.get("blocking") or ["unknown"]),
                warnings=list(readiness.get("blocking") or []),
            )

        prog(f"Resolving page sources 0/{len(pages)}")
        entries, errors = self.resolver.resolve_pages(
            pages, allow_unresolved_figures=allow_unresolved
        )
        if errors:
            return AssemblyResult(
                success=False,
                error=";".join(errors),
                warnings=errors,
            )
        if cancel_check and cancel_check():
            return AssemblyResult(success=False, error="cancelled")

        candidates = self.analyzer.analyze_project(
            project_root=self.project_root, page_numbers=pages
        )
        patches = self._load_patches() if apply_patches else []
        patch_digests: list[str] = []
        applied = 0
        stale_warnings: list[str] = []
        unreviewed = 0

        # Map patches by boundary
        patch_map = {(p["left_page"], p["right_page"]): p for p in patches}
        for cand in candidates:
            key = (cand.left_page, cand.right_page)
            if key not in patch_map:
                unreviewed += 1

        if self.assemble_cfg.get("require_continuity_review") and unreviewed > 0:
            return AssemblyResult(
                success=False,
                error=f"continuity_review_required:{unreviewed}",
                continuity_candidates=len(candidates),
                unreviewed_continuity_candidates=unreviewed,
            )

        for p in patches:
            left_hash = self._source_hash_for(entries, int(p["left_page"]))
            right_hash = self._source_hash_for(entries, int(p["right_page"]))
            if p.get("source_hash_left") and left_hash and p["source_hash_left"] != left_hash:
                stale_warnings.append(
                    f"stale_patch:{p['left_page']}→{p['right_page']}"
                )
                continue
            if p.get("source_hash_right") and right_hash and p["source_hash_right"] != right_hash:
                stale_warnings.append(
                    f"stale_patch:{p['left_page']}→{p['right_page']}"
                )
                continue
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "l": p["left_page"],
                        "r": p["right_page"],
                        "a": p["action"],
                        "c": p.get("custom_text") or "",
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            patch_digests.append(digest)

        ahash = assembly_hash(
            page_entries=entries,
            patch_digests=patch_digests,
            assembler_version=ASSEMBLER_VERSION,
            preserve_page_markers=preserve,
            allow_unresolved=allow_unresolved,
        )

        if (
            self.use_cache
            and not force
            and self.raw_path.exists()
            and self._cached_hash() == ahash
        ):
            # quick validate existing
            raw = self.raw_path.read_text(encoding="utf-8")
            v = self.validator.validate(
                raw_md=raw,
                project_root=self.project_root,
                expected_pages=pages,
                allow_unresolved_figures=allow_unresolved,
            )
            if v.ok:
                prog("CACHED")
                result = AssemblyResult(
                    success=True,
                    output_path=self.raw_path,
                    total_pages=len(pages),
                    resolved_sources=sum(1 for e in entries if e.source_type == "resolved"),
                    canonical_sources=sum(1 for e in entries if e.source_type == "canonical"),
                    continuity_candidates=len(candidates),
                    continuity_patches_applied=0,
                    unreviewed_continuity_candidates=unreviewed,
                    warnings=list(v.warnings) + stale_warnings,
                    assembly_hash=ahash,
                    cached=True,
                    manifest_path=str(self.manifest_path),
                )
                self._record_run(result, status="CACHED")
                return result

        prog("Building fragments")
        fragments: list[str] = []
        repairs: list[str] = []
        for i, entry in enumerate(entries):
            if cancel_check and cancel_check():
                return AssemblyResult(success=False, error="cancelled")
            prog(f"Resolving page sources {i + 1}/{len(entries)}")
            path = self.project_root / entry.source
            text = path.read_text(encoding="utf-8")
            frag, repair_notes = self._normalize_page_fragment(
                page=entry.page, text=text, preserve=preserve
            )
            repairs.extend(repair_notes)
            fragments.append(frag)

        # Apply continuity patches at boundaries
        for i in range(len(entries) - 1):
            left_p = entries[i].page
            right_p = entries[i + 1].page
            patch = patch_map.get((left_p, right_p))
            if not patch:
                continue
            if f"stale_patch:{left_p}→{right_p}" in stale_warnings:
                continue
            action = patch.get("action") or ContinuityPatchAction.NO_ACTION.value
            if action == ContinuityPatchAction.NO_ACTION.value:
                continue
            left_hash = self._source_hash_for(entries, left_p)
            right_hash = self._source_hash_for(entries, right_p)
            if patch.get("source_hash_left") and left_hash != patch["source_hash_left"]:
                continue
            if patch.get("source_hash_right") and right_hash != patch["source_hash_right"]:
                continue
            fragments[i], fragments[i + 1] = self._apply_join(
                fragments[i],
                fragments[i + 1],
                action=action,
                custom=patch.get("custom_text"),
            )
            applied += 1

        raw = "\n\n".join(f for f in fragments if f.strip())
        if not raw.endswith("\n"):
            raw += "\n"

        prog("Validating")
        validation = self.validator.validate(
            raw_md=raw,
            project_root=self.project_root,
            expected_pages=pages,
            allow_unresolved_figures=allow_unresolved,
        )
        warnings = list(validation.warnings) + repairs + stale_warnings
        if unreviewed:
            warnings.append(f"unreviewed_continuity_candidates:{unreviewed}")
        if allow_unresolved:
            warnings.append("assembled_with_unresolved_figures=true")

        if not validation.ok:
            return AssemblyResult(
                success=False,
                error=";".join(validation.blocking),
                warnings=warnings + validation.blocking,
                assembly_hash=ahash,
                continuity_candidates=len(candidates),
                unreviewed_continuity_candidates=unreviewed,
            )

        # Archive previous raw if replacing
        if self.raw_path.exists() and self.archive_previous:
            self._archive_raw()

        prog("Writing raw.md")
        tmp = self.raw_path.with_suffix(".md.tmp")
        tmp.write_text(raw, encoding="utf-8", newline="\n")
        # re-validate written content
        written = tmp.read_text(encoding="utf-8")
        v2 = self.validator.validate(
            raw_md=written,
            project_root=self.project_root,
            expected_pages=pages,
            allow_unresolved_figures=allow_unresolved,
        )
        if not v2.ok:
            tmp.unlink(missing_ok=True)
            return AssemblyResult(
                success=False,
                error=";".join(v2.blocking),
                warnings=warnings,
                assembly_hash=ahash,
            )
        tmp.replace(self.raw_path)

        self.resolver.write_manifest(entries, self.manifest_path)
        self._write_hash_sidecar(ahash)

        report_path = self._write_report(
            ahash=ahash,
            entries=entries,
            candidates=len(candidates),
            applied=applied,
            unreviewed=unreviewed,
            warnings=warnings,
            validation=validation,
            allow_unresolved=allow_unresolved,
        )

        result = AssemblyResult(
            success=True,
            output_path=self.raw_path,
            total_pages=len(pages),
            resolved_sources=sum(1 for e in entries if e.source_type == "resolved"),
            canonical_sources=sum(1 for e in entries if e.source_type == "canonical"),
            continuity_candidates=len(candidates),
            continuity_patches_applied=applied,
            unreviewed_continuity_candidates=unreviewed,
            warnings=warnings,
            assembly_hash=ahash,
            cached=False,
            report_path=str(report_path),
            manifest_path=str(self.manifest_path),
        )
        self._record_run(result, status="SUCCESS")
        self._upsert_document_artifact(ahash)
        return result

    def _normalize_page_fragment(
        self, *, page: int, text: str, preserve: bool
    ) -> tuple[str, list[str]]:
        notes: list[str] = []
        content = text.replace("\r\n", "\n").replace("\r", "\n")
        markers = list(PAGE_MARKER_RE.finditer(content))
        standard = f"<!-- PAGE: {page:04d} -->"

        if not preserve:
            # still keep markers per Phase 7 default contract — ignore flag for deletion
            pass

        if not markers:
            body = content.lstrip("\n")
            notes.append(f"page_marker_inserted:{page}")
            return f"{standard}\n\n{body}".rstrip() + "\n", notes

        # Keep first matching page marker as standard; drop extras of same page
        first = markers[0]
        # Replace first marker with standard form
        before = content[: first.start()].rstrip()
        after = content[first.end() :].lstrip("\n")
        # Remove subsequent PAGE markers for this page number
        removed = 0

        def _drop_dup(m: re.Match[str]) -> str:
            nonlocal removed
            if int(m.group(1)) == page:
                removed += 1
                return ""
            return m.group(0)

        after = PAGE_MARKER_RE.sub(_drop_dup, after)
        if removed:
            notes.append(f"duplicate_page_marker_repaired:{page}")
        body = after.lstrip("\n")
        if before:
            # unusual: content before PAGE marker — keep it after standard marker
            frag = f"{standard}\n\n{before}\n\n{body}".rstrip() + "\n"
        else:
            frag = f"{standard}\n\n{body}".rstrip() + "\n"
        return frag, notes

    def _apply_join(
        self,
        left: str,
        right: str,
        *,
        action: str,
        custom: str | None,
    ) -> tuple[str, str]:
        """Adjust boundary around PAGE marker of the right fragment."""
        # right starts with <!-- PAGE: N -->
        m = PAGE_MARKER_RE.match(right.lstrip())
        if not m:
            return left, right
        # Work on stripped versions
        left_body = left.rstrip()
        right_stripped = right.lstrip()
        marker_end = PAGE_MARKER_RE.match(right_stripped)
        assert marker_end
        marker = marker_end.group(0)
        rest = right_stripped[marker_end.end() :].lstrip("\n")

        if action == ContinuityPatchAction.CUSTOM_REPLACEMENT.value and custom is not None:
            # Keep left body; right becomes PAGE marker + custom boundary text
            return left_body + "\n", marker + "\n" + custom.lstrip() + "\n"

        # Trim trailing/leading blank lines for join styles
        left_core = left_body.rstrip("\n")
        if action == ContinuityPatchAction.JOIN_WITH_SPACE.value:
            # "word\n<!-- PAGE -->\nnext"
            if left_core.endswith("-"):
                left_core = left_core  # keep hyphen unless WITHOUT_SPACE
            new_left = left_core + "\n" + marker + "\n"
            new_right = rest
            # ensure single space boundary if needed
            if new_left.rstrip().endswith(marker) and rest and not rest[0].isspace():
                # space after marker newline is fine; insert space only if last char alnum
                if left_core and left_core[-1].isalnum() and rest[0].isalnum():
                    new_right = " " + rest
            return new_left.rstrip() + "\n", new_right
        if action == ContinuityPatchAction.JOIN_WITHOUT_SPACE.value:
            new_left = left_core + "\n" + marker + "\n"
            return new_left, rest.lstrip()
        if action == ContinuityPatchAction.JOIN_WITH_NEWLINE.value:
            return left_core + "\n" + marker + "\n", rest
        return left, right

    def _source_hash_for(
        self, entries: list[PageSourceEntry], page: int
    ) -> str | None:
        for e in entries:
            if e.page == page:
                return e.sha256
        return None

    def _load_patches(self) -> list[dict[str, Any]]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return repo.list_continuity_patches()
        finally:
            db.close()

    def _all_pages(self) -> list[int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return [int(p["page_number"]) for p in repo.list_pages()]
        finally:
            db.close()

    def _cached_hash(self) -> str | None:
        side = self.intermediate / "assembly_hash.txt"
        if side.exists():
            return side.read_text(encoding="utf-8").strip() or None
        return None

    def _write_hash_sidecar(self, ahash: str) -> None:
        path = self.intermediate / "assembly_hash.txt"
        path.write_text(ahash + "\n", encoding="utf-8", newline="\n")

    def _archive_raw(self) -> None:
        hist = ensure_dir(self.project_root / "history" / "assemble")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = hist / f"{stamp}_raw.md"
        shutil.copy2(self.raw_path, dest)
        if self.manifest_path.exists():
            shutil.copy2(
                self.manifest_path, hist / f"{stamp}_assemble_manifest.json"
            )

    def _write_report(self, **kwargs: Any) -> Path:
        reports = ensure_dir(self.project_root / "reports")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = reports / f"assemble_{stamp}.json"
        entries: list[PageSourceEntry] = kwargs["entries"]
        validation = kwargs["validation"]
        payload = {
            "status": "success",
            "output": "intermediate/raw.md",
            "pages": len(entries),
            "resolved_sources": sum(1 for e in entries if e.source_type == "resolved"),
            "canonical_sources": sum(1 for e in entries if e.source_type == "canonical"),
            "figures": sum(e.figure_count for e in entries),
            "continuity_candidates": kwargs["candidates"],
            "continuity_patches": kwargs["applied"],
            "unreviewed_continuity_candidates": kwargs["unreviewed"],
            "warnings": kwargs["warnings"],
            "assembly_hash": kwargs["ahash"],
            "assembled_with_unresolved_figures": kwargs["allow_unresolved"],
            "page_markers": validation.page_markers,
            "unresolved_figure_markers": validation.unresolved_figure_markers,
            "figure_links": validation.figure_links,
            "assembler_version": ASSEMBLER_VERSION,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def _record_run(self, result: AssemblyResult, *, status: str) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.insert_assemble_run(
                status=status,
                assembly_hash=result.assembly_hash,
                output_path=str(result.output_path) if result.output_path else None,
                page_count=result.total_pages,
                resolved_source_count=result.resolved_sources,
                canonical_source_count=result.canonical_sources,
                continuity_candidates=result.continuity_candidates,
                continuity_patches=result.continuity_patches_applied,
                warning_count=len(result.warnings),
            )
        finally:
            db.close()

    def _upsert_document_artifact(self, ahash: str) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_document_artifact(
                artifact_type="raw",
                path=str(self.raw_path.relative_to(self.project_root)).replace("\\", "/"),
                content_hash=file_sha256(self.raw_path),
                source_hash=ahash,
                status="SUCCESS",
            )
        finally:
            db.close()

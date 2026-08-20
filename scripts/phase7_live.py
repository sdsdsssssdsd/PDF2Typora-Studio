"""Phase 7 live assemble pilot — 8-page Kuzilek project."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from core.assemble_models import AssemblyRequest
from core.models import PipelineStage, StageStatus
from services.assemble_readiness_service import AssembleReadinessService
from services.assembled_markdown_validator import AssembledMarkdownValidator, PAGE_MARKER_RE
from services.continuity_analyzer import ContinuityAnalyzer
from services.figure_review_service import FigureReviewService
from services.markdown_assembler import MarkdownAssembler
from services.transcription_validator import FIGURE_MARKER_RE
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256

PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase7_live_report.json"


def _dir_hashes(folder: Path, pattern: str) -> dict[str, str]:
    out = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob(pattern)):
        if p.is_file():
            out[p.name] = file_sha256(p)
    return out


def _promote_experiment(page: int) -> bool:
    exp_root = PROJECT / "experiments" / "transcription" / f"page_{page:04d}"
    if not exp_root.is_dir():
        return False
    attempts = sorted(exp_root.iterdir(), reverse=True)
    for latest in attempts:
        resp_path = latest / "response.json"
        md_path = latest / "markdown.md"
        if not resp_path.exists():
            continue
        result = json.loads(resp_path.read_text(encoding="utf-8"))
        md = (
            md_path.read_text(encoding="utf-8")
            if md_path.exists()
            else result.get("markdown", "")
        )
        canon_md = PROJECT / "markdown_pages" / f"page_{page:04d}.md"
        canon_json = PROJECT / "page_results" / f"page_{page:04d}.json"
        canon_md.parent.mkdir(parents=True, exist_ok=True)
        canon_json.parent.mkdir(parents=True, exist_ok=True)
        canon_md.write_text(f"<!-- PAGE: {page:04d} -->\n\n{md}", encoding="utf-8")
        payload = {
            "result": result,
            "provenance": {"mode": "phase7_promote", "attempt_dir": str(latest)},
            "acceptance": {"mode": "pilot"},
        }
        canon_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    return False


def _ensure_resolved_no_figures(page: int) -> None:
    """Copy canonical → resolved for pages without figures."""
    js = PROJECT / "page_results" / f"page_{page:04d}.json"
    if not js.exists():
        return
    payload = json.loads(js.read_text(encoding="utf-8"))
    figs = (payload.get("result") or {}).get("figures") or []
    if figs:
        return
    canon = PROJECT / "markdown_pages" / f"page_{page:04d}.md"
    resolved = PROJECT / "resolved_pages" / f"page_{page:04d}.md"
    if canon.exists():
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(canon.read_text(encoding="utf-8"), encoding="utf-8")


def _rebuild_figure_pages(cfg: dict) -> list[dict]:
    """Re-apply marker placement + rebuild resolved for accepted figures."""
    review = FigureReviewService(
        project_root=PROJECT,
        pdf_path=PROJECT / "source.pdf",
        db_path=PROJECT / "project.db",
        pdf_hash=file_sha256(PROJECT / "source.pdf"),
        config=cfg,
    )
    db = Database(PROJECT / "project.db")
    db.initialize()
    repo = ProjectRepository(db)
    figures = repo.list_figures()
    db.close()

    notes = []
    pages_done: set[int] = set()
    for fig in figures:
        if fig.get("status") not in {"resolved", "cached"}:
            continue
        page = int(fig["page_number"])
        idx = int(fig["figure_index"])
        canon = (PROJECT / "markdown_pages" / f"page_{page:04d}.md").read_text(
            encoding="utf-8"
        )
        # Prefer caption-ish insertion after first paragraph
        offset = min(len(canon), max(80, len(canon) // 3))
        review.confirm_marker_placement(
            page_number=page,
            figure_index=idx,
            char_offset=offset + idx * 3,  # keep distinct offsets
            before_context=canon[max(0, offset - 30) : offset],
            after_context=canon[offset : offset + 30],
        )
        # Re-persist resolved status without wiping placement
        db = Database(PROJECT / "project.db")
        db.initialize()
        repo = ProjectRepository(db)
        repo.upsert_figure(
            page_number=page,
            figure_index=idx,
            status="resolved",
            file_path=fig.get("file_path"),
            artifact_hash=fig.get("artifact_hash"),
            manually_inserted_marker=True,
            manual_marker_offset=offset + idx * 3,
            manual_marker_before_context=canon[max(0, offset - 30) : offset],
            manual_marker_after_context=canon[offset : offset + 30],
            review_action="phase7_rebuild_placement",
        )
        db.close()
        notes.append({"page": page, "figure_index": idx, "offset": offset + idx * 3})
        pages_done.add(page)

    for page in sorted(pages_done):
        review.rebuild_resolved_page(page)
        db = Database(PROJECT / "project.db")
        db.initialize()
        ProjectRepository(db).upsert_stage_state(
            page, PipelineStage.FIGURES, StageStatus.SUCCESS
        )
        db.close()
    return notes


def main() -> int:
    if not PROJECT.exists():
        print("project missing", PROJECT)
        return 1

    cfg = load_config()
    db_path = PROJECT / "project.db"
    pages = list(range(1, 9))

    # Promote missing review pages 1 & 8 into canonical
    promoted = []
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    for p in (1, 8):
        if _promote_experiment(p):
            promoted.append(p)
            repo.upsert_stage_state(p, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
            repo.upsert_stage_state(p, PipelineStage.RENDER, StageStatus.SUCCESS)
    db.close()

    for p in pages:
        _ensure_resolved_no_figures(p)
        db = Database(db_path)
        db.initialize()
        repo = ProjectRepository(db)
        # mark figures stage success for no-figure pages
        js = PROJECT / "page_results" / f"page_{p:04d}.json"
        if js.exists():
            figs = (json.loads(js.read_text(encoding="utf-8")).get("result") or {}).get(
                "figures"
            ) or []
            if not figs:
                repo.upsert_stage_state(p, PipelineStage.FIGURES, StageStatus.SUCCESS)
        db.close()

    rebuild_notes = _rebuild_figure_pages(cfg)

    # Snapshot hashes AFTER promotions/rebuild of derived assets only;
    # measure immutability of upstream during assemble itself.
    before = {
        "markdown_pages": _dir_hashes(PROJECT / "markdown_pages", "*.md"),
        "resolved_pages": _dir_hashes(PROJECT / "resolved_pages", "*.md"),
        "figures": _dir_hashes(PROJECT / "figures", "*"),
    }

    readiness = AssembleReadinessService(
        project_root=PROJECT, db_path=db_path, config=cfg
    ).summarize(pages)

    analyzer = ContinuityAnalyzer(cfg)
    candidates = analyzer.analyze_project(project_root=PROJECT, page_numbers=pages)

    asm = MarkdownAssembler(project_root=PROJECT, db_path=db_path, config=cfg)
    r1 = asm.assemble(
        AssemblyRequest(project_root=PROJECT, page_numbers=tuple(pages), force=True)
    )
    r2 = asm.assemble(
        AssemblyRequest(project_root=PROJECT, page_numbers=tuple(pages), force=False)
    )

    raw_path = PROJECT / "intermediate" / "raw.md"
    raw = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    markers = [int(m) for m in PAGE_MARKER_RE.findall(raw)]
    unresolved = FIGURE_MARKER_RE.findall(raw)
    loose = re.findall(
        r"(?:<!--\s*)?FIGURE\s+page\s*=\s*\d+\s+index\s*=\s*\d+",
        raw,
        flags=re.IGNORECASE,
    )
    fig_links = re.findall(r"!\[[^\]]*]\((figures/[^)]+)\)", raw)
    missing_figs = [rel for rel in fig_links if not (PROJECT / rel).exists()]
    hrs = re.findall(r"(?m)^---\s*$", raw)

    after = {
        "markdown_pages": _dir_hashes(PROJECT / "markdown_pages", "*.md"),
        "resolved_pages": _dir_hashes(PROJECT / "resolved_pages", "*.md"),
        "figures": _dir_hashes(PROJECT / "figures", "*"),
    }

    validation = AssembledMarkdownValidator().validate(
        raw_md=raw,
        project_root=PROJECT,
        expected_pages=pages,
        allow_unresolved_figures=False,
    )

    report = {
        "phase": "7",
        "promoted_pages": promoted,
        "rebuild_notes": rebuild_notes,
        "readiness": readiness,
        "assemble_first": {
            "success": r1.success,
            "cached": r1.cached,
            "error": r1.error,
            "resolved_sources": r1.resolved_sources,
            "canonical_sources": r1.canonical_sources,
            "continuity_candidates": r1.continuity_candidates,
            "continuity_patches_applied": r1.continuity_patches_applied,
            "warnings": r1.warnings,
            "assembly_hash": r1.assembly_hash,
            "report_path": r1.report_path,
        },
        "assemble_second": {
            "success": r2.success,
            "cached": r2.cached,
            "assembly_hash": r2.assembly_hash,
        },
        "continuity_candidates_detected": len(candidates),
        "raw_md": {
            "exists": raw_path.exists(),
            "size_bytes": raw_path.stat().st_size if raw_path.exists() else 0,
            "page_markers": len(markers),
            "page_marker_list": markers,
            "unresolved_figure_markers": max(len(unresolved), len(loose)),
            "figure_links": len(fig_links),
            "figure_link_paths": fig_links,
            "missing_figure_paths": missing_figs,
            "horizontal_rules": len(hrs),
            "validator_ok": validation.ok,
            "validator_blocking": validation.blocking,
        },
        "upstream_sha256_unchanged": before == after,
        "typora_smoke": "not_performed",
        "acceptance": {
            "page_markers_8": len(markers) == 8,
            "unresolved_figures_0": max(len(unresolved), len(loose)) == 0,
            "missing_figure_paths_0": len(missing_figs) == 0,
            "second_assemble_cached": bool(r2.cached),
            "upstream_unchanged": before == after,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all(report["acceptance"].values()) and r1.success
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

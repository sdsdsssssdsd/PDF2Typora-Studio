"""
Phase 9 end-to-end regression (NOT part of default pytest).

Usage:
  python scripts/phase9_e2e.py --pdf "E:\\PDFtomd\\O-001_Kuzilek2017_DataPaper.pdf"
  python scripts/phase9_e2e.py --resume --workspace "E:\\PDFtomd\\workspace\\_e2e_phase9_..."

Stops honestly when Transcription / Figure / Cleaner review is required.
"""

from __future__ import annotations

import argparse
import atexit
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.model_profiles import ModelProfileStore
from ai.providers.ollama_provider import OllamaVisionProvider
from ai.runtime.ollama_manager import OllamaRuntimeManager
from config.config_manager import get_ollama_settings, load_config
from core.assemble_models import AssemblyRequest
from core.models import ModelQualification, PipelineStage, RenderRequest, RenderSettings, StageStatus
from services.batch_cleaner_service import BatchCleanerService
from services.batch_figure_service import BatchFigureService
from services.batch_transcription_service import BatchTranscriptionService
from services.final_freeze_service import FinalFreezeService
from services.final_readiness_service import FinalReadinessService
from services.figure_readiness_service import FigureReadinessService
from services.markdown_assembler import MarkdownAssembler
from services.project_service import ProjectService
from services.render_service import RenderService
from services.transcription_service import TranscriptionService
from services.typora_export_service import TyporaExportService
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _review_counts(project_root: Path, db_path: Path) -> dict:
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    try:
        tr = repo.list_review_pages()
        fig = repo.list_figure_review_items()
        cl = repo.list_cleaner_review_items()
        return {
            "transcription": len(tr),
            "figures": len(fig),
            "cleaner": len(cl),
            "transcription_pages": [r.get("page_number") for r in tr],
            "figure_items": [
                {"page": f.get("page_number"), "index": f.get("figure_index")} for f in fig
            ],
        }
    finally:
        db.close()


def _stop_review(report: dict, kind: str, project: Path, detail: dict) -> int:
    report["end_to_end_status"] = "REVIEW_REQUIRED"
    report["paused_at"] = kind
    report["review"] = detail
    report["project_path"] = str(project)
    report["message"] = (
        f"REVIEW REQUIRED: {kind}. Resolve in GUI, then re-run with --resume."
    )
    _write_report(report)
    print(report["message"])
    print(f"project: {project}")
    return 2


def _write_report(report: dict) -> Path:
    reports = Path(report.get("project_path") or ROOT) / "reports"
    if report.get("project_path"):
        reports = Path(report["project_path"]) / "reports"
    else:
        reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"e2e_phase9_{_stamp()}.json"
    # also root copy
    root_copy = ROOT / f"phase9_e2e_report.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    root_copy.write_text(text, encoding="utf-8")
    print(f"report → {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 9 E2E regression")
    parser.add_argument(
        "--pdf",
        default=str(ROOT / "O-001_Kuzilek2017_DataPaper.pdf"),
    )
    parser.add_argument("--workspace", default="", help="Existing project for --resume")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--export-root", default="")
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip Vision/Figure (only Final/Export if project already complete)",
    )
    args = parser.parse_args()
    cfg = load_config()
    timings: dict[str, float] = {}
    report: dict = {
        "phase": "9",
        "mode": "resume" if args.resume else "fresh",
        "started_at": _now(),
        "pdf": args.pdf,
        "timings_sec": timings,
        "manual_interventions_total": 0,
        "stages": {},
    }
    ollama = None
    started_ollama = False

    def _cleanup_ollama() -> None:
        nonlocal started_ollama, ollama
        if started_ollama and ollama is not None:
            try:
                ollama.stop_managed()
            except Exception:  # noqa: BLE001
                pass
            started_ollama = False

    atexit.register(_cleanup_ollama)

    t0 = time.perf_counter()
    if args.resume:
        if not args.workspace:
            print("--resume requires --workspace")
            return 1
        project_root = Path(args.workspace)
        svc = ProjectService()
        project = svc.open_project(project_root)
    else:
        pdf = Path(args.pdf)
        if not pdf.exists():
            print(f"PDF missing: {pdf}")
            return 1
        report["pdf_hash"] = file_sha256(pdf)
        e2e_ws = ROOT / "workspace" / f"_e2e_phase9_{pdf.stem}"
        e2e_ws.mkdir(parents=True, exist_ok=True)
        svc = ProjectService(workspace_root=e2e_ws)
        project = svc.create_project(pdf)
        report["pdf_hash"] = file_sha256(project.info.source_pdf)

    project_root = project.root
    db_path = project.db_path
    report["project_path"] = str(project_root)
    pages = list(range(1, project.info.page_count + 1))

    # Auto-skip earlier stages on resume when artifacts already exist
    if args.resume and not args.skip_vision:
        if (project_root / "intermediate" / "clean.md").exists():
            args.skip_vision = True
            report["resume_skip"] = "vision_figure_assemble_clean_already_done_or_clean_present"
        else:
            fig_ready = FigureReadinessService(
                project_root=project_root, db_path=db_path, config=cfg
            ).summarize()
            reviews = _review_counts(project_root, db_path)
            if reviews["transcription"] or reviews["figures"]:
                report["finished_at"] = _now()
                kind = "Transcription" if reviews["transcription"] else "Figure"
                return _stop_review(report, kind, project_root, reviews)
            if fig_ready.get("ready") and (
                project_root / "markdown_pages"
            ).exists():
                args.skip_vision = True
                report["resume_skip"] = "vision_done_figures_ready"

    # ---------- Render ----------
    if not args.skip_vision:
        tr = time.perf_counter()
        render = RenderService()
        settings = RenderSettings(
            dpi=int((cfg.get("pdf") or {}).get("dpi", 200)),
            image_format=str((cfg.get("pdf") or {}).get("image_format", "png")),
        )
        req = RenderRequest(
            pdf_path=project.info.source_pdf,
            pages=tuple(pages),
            output_dir=project.pages_dir,
            settings=settings,
            pdf_hash=file_sha256(project.info.source_pdf),
            db_path=db_path,
        )
        results = render.render_pages(req)
        timings["render"] = round(time.perf_counter() - tr, 3)
        report["stages"]["render"] = {
            "success": sum(1 for r in results if r.success and not r.cached),
            "cached": sum(1 for r in results if r.cached),
            "failed": sum(1 for r in results if not r.success),
        }

        # ---------- Vision ----------
        model = args.model or (cfg.get("ai") or {}).get("selected_model") or ""
        store = ModelProfileStore()
        if not model:
            for p in store.list_all():
                if p.qualification == ModelQualification.QUALIFIED:
                    model = p.model_name
                    break
        if not model:
            report["stages"]["transcription"] = {"status": "NO_MODEL"}
            report["end_to_end_status"] = "BLOCKED_NO_MODEL"
            report["finished_at"] = _now()
            report["total_sec"] = round(time.perf_counter() - t0, 3)
            _write_report(report)
            print("No QUALIFIED vision model — cannot continue Vision. Use GUI to qualify.")
            _cleanup_ollama()
            return 3

        report["model"] = model
        ollama_settings = get_ollama_settings(cfg)
        runtime = Path(ollama_settings["runtime_path"])
        if not (runtime / "ollama.exe").exists() and Path(r"E:\Ollama\ollama.exe").exists():
            ollama_settings["runtime_path"] = Path(r"E:\Ollama")
            ollama_settings["models_path"] = Path(r"E:\Ollama\models")
        ollama = OllamaRuntimeManager(settings=ollama_settings)
        started_ollama = False
        try:
            status = ollama.start_bundled()
            started_ollama = True
            url = status.base_url or ollama.resolve_base_url()
            provider = OllamaVisionProvider(url)
        except Exception as exc:  # noqa: BLE001
            report["stages"]["transcription"] = {
                "status": "OLLAMA_UNAVAILABLE",
                "error": str(exc),
            }
            report["end_to_end_status"] = "BLOCKED_OLLAMA"
            report["finished_at"] = _now()
            _write_report(report)
            print(f"Ollama unavailable: {exc}")
            _cleanup_ollama()
            return 3

        tt = time.perf_counter()
        trans = TranscriptionService(provider, project_root, db_path)
        batch = BatchTranscriptionService(
            transcription=trans,
            project_root=project_root,
            db_path=db_path,
            profiles=store,
            page_count=project.info.page_count,
            config=cfg,
        )
        try:
            digest = trans.get_model_digest(model)
            report["model_digest"] = digest
            create = batch.create_run(
                pages=pages, primary_model=model, require_qualified=True, mode="e2e"
            )
        except Exception as exc:  # noqa: BLE001
            report["stages"]["transcription"] = {
                "status": "CREATE_RUN_FAILED",
                "error": str(exc),
            }
            report["end_to_end_status"] = "BLOCKED_VISION"
            report["finished_at"] = _now()
            _write_report(report)
            print(f"Vision run failed: {exc}")
            _cleanup_ollama()
            return 3
        batch.mark_run(create.run_id, "RUNNING")
        vision_stats = {
            "auto_accepted": 0,
            "reviewed": 0,
            "failed": 0,
            "cached": 0,
            "real_calls": 0,
        }
        while True:
            page = batch.next_waiting(create.run_id)
            if page is None:
                break
            result = batch.process_page(create.run_id, page)
            st_s = str(result.status or "")
            if result.cached or "CACHE" in st_s.upper():
                vision_stats["cached"] += 1
            else:
                vision_stats["real_calls"] += 1
            if "REVIEW" in st_s.upper():
                vision_stats["reviewed"] += 1
            elif "FAIL" in st_s.upper():
                vision_stats["failed"] += 1
            else:
                vision_stats["auto_accepted"] += 1
            batch.refresh_counts(create.run_id)
        final_run = batch.finalize_run(create.run_id)
        timings["transcription"] = round(time.perf_counter() - tt, 3)
        report["stages"]["transcription"] = {
            **vision_stats,
            "finalize": final_run if isinstance(final_run, dict) else str(final_run),
        }

        reviews = _review_counts(project_root, db_path)
        if reviews["transcription"]:
            report["manual_interventions_total"] += reviews["transcription"]
            report["finished_at"] = _now()
            report["total_sec"] = round(time.perf_counter() - t0, 3)
            _cleanup_ollama()
            return _stop_review(report, "Transcription", project_root, reviews)

        # ---------- Figures ----------
        tf = time.perf_counter()
        fig_svc = BatchFigureService(
            project_root=project_root,
            pdf_path=project.info.source_pdf,
            db_path=db_path,
            pdf_hash=file_sha256(project.info.source_pdf),
            config=cfg,
        )
        fig_summary = fig_svc.process_pages(pages)
        timings["figures"] = round(time.perf_counter() - tf, 3)
        fig_ready = FigureReadinessService(
            project_root=project_root, db_path=db_path, config=cfg
        ).summarize()
        report["stages"]["figures"] = {**fig_summary, "readiness": fig_ready}
        reviews = _review_counts(project_root, db_path)
        if reviews["figures"] or not fig_ready.get("ready"):
            report["manual_interventions_total"] += reviews["figures"]
            report["finished_at"] = _now()
            report["total_sec"] = round(time.perf_counter() - t0, 3)
            _cleanup_ollama()
            return _stop_review(report, "Figure", project_root, reviews)

    # ---------- Assemble ----------
    ta = time.perf_counter()
    assembler = MarkdownAssembler(
        project_root=project_root, db_path=db_path, config=cfg
    )
    asm = assembler.assemble(
        AssemblyRequest(
            project_root=project_root,
            page_numbers=tuple(pages),
            preserve_page_markers=True,
            apply_continuity_patches=True,
            allow_unresolved_figures=False,
            force=False,
        )
    )
    timings["assemble"] = round(time.perf_counter() - ta, 3)
    report["stages"]["assemble"] = {
        "success": asm.success,
        "cached": asm.cached,
        "error": asm.error,
        "pages": asm.total_pages,
    }
    if not asm.success:
        report["end_to_end_status"] = "ASSEMBLE_FAILED"
        report["finished_at"] = _now()
        _write_report(report)
        return 1

    # ---------- Cleaner ----------
    tc = time.perf_counter()
    cleaner = BatchCleanerService(
        project_root=project_root, db_path=db_path, config=cfg, text_provider=None
    )
    clean_summary = cleaner.process_pages(pages, force=False)
    timings["cleaner"] = round(time.perf_counter() - tc, 3)
    report["stages"]["cleaner"] = clean_summary
    reviews = _review_counts(project_root, db_path)
    if reviews["cleaner"]:
        report["manual_interventions_total"] += reviews["cleaner"]
        report["finished_at"] = _now()
        report["total_sec"] = round(time.perf_counter() - t0, 3)
        _cleanup_ollama()
        return _stop_review(report, "Cleaner", project_root, reviews)

    # ---------- Final + Export ----------
    readiness = FinalReadinessService(
        project_root=project_root, db_path=db_path, config=cfg
    ).summarize()
    report["stages"]["final_readiness"] = readiness
    if not readiness.get("ready"):
        report["end_to_end_status"] = "FINAL_NOT_READY"
        report["finished_at"] = _now()
        _write_report(report)
        return 1

    tfz = time.perf_counter()
    freeze = FinalFreezeService(
        project_root=project_root, db_path=db_path, config=cfg
    )
    fz1 = freeze.freeze()
    fz2 = freeze.freeze()
    timings["final"] = round(time.perf_counter() - tfz, 3)
    report["stages"]["final"] = {
        "first": {
            "success": fz1.success,
            "status": fz1.status,
            "clean_sha256": fz1.clean_sha256,
            "final_sha256": fz1.final_sha256,
            "byte_identical": fz1.byte_identical,
        },
        "second": {"success": fz2.success, "status": fz2.status, "cached": fz2.cached},
    }
    if not fz1.success:
        report["end_to_end_status"] = "FINAL_FAILED"
        report["finished_at"] = _now()
        _write_report(report)
        return 1

    te = time.perf_counter()
    export_cfg = dict(cfg)
    if args.export_root:
        export_cfg["export"] = {
            **(cfg.get("export") or {}),
            "default_root": args.export_root,
        }
    exporter = TyporaExportService(
        project_root=project_root, db_path=db_path, config=export_cfg
    )
    ex1 = exporter.export(include_source_pdf=True)
    ex2 = exporter.export(include_source_pdf=True)
    timings["export"] = round(time.perf_counter() - te, 3)
    report["stages"]["export"] = {
        "first": {
            "success": ex1.success,
            "status": ex1.status,
            "path": str(ex1.export_path) if ex1.export_path else None,
            "error": ex1.error,
        },
        "second": {
            "success": ex2.success,
            "status": ex2.status,
            "cached": ex2.cached,
        },
    }

    report["end_to_end_status"] = "SUCCESS" if ex1.success else "EXPORT_FAILED"
    report["finished_at"] = _now()
    report["total_sec"] = round(time.perf_counter() - t0, 3)
    _write_report(report)
    print(json.dumps({"status": report["end_to_end_status"], "timings": timings}, indent=2))
    return 0 if ex1.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

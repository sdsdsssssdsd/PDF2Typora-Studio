"""Phase 9 live Final + Export on existing Phase 8 pilot project."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from services.final_freeze_service import FinalFreezeService
from services.final_readiness_service import FinalReadinessService
from services.final_validator import FinalValidator
from services.typora_export_service import TyporaExportService
from services.typora_launcher import TyporaLauncher
from utils.hashing import file_sha256

PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase9_live_report.json"


def _upstream_hashes() -> dict:
    out: dict = {}
    for name, folder, pat in (
        ("raw", PROJECT / "intermediate", "raw.md"),
        ("clean", PROJECT / "intermediate", "clean.md"),
        ("markdown_pages", PROJECT / "markdown_pages", "*.md"),
        ("resolved_pages", PROJECT / "resolved_pages", "*.md"),
        ("figures", PROJECT / "figures", "*"),
    ):
        d = {}
        if pat.endswith(".md") and "*" not in pat:
            f = folder / pat
            if f.exists():
                d[pat] = file_sha256(f)
        elif folder.is_dir():
            for p in sorted(folder.glob(pat)):
                if p.is_file():
                    d[p.name] = file_sha256(p)
        out[name] = d
    return out


def main() -> int:
    if not (PROJECT / "intermediate" / "clean.md").exists():
        print("clean.md missing — run Phase 8 first")
        return 1

    cfg = load_config()
    before = _upstream_hashes()
    clean_sha_before = before["clean"]["clean.md"]

    readiness = FinalReadinessService(
        project_root=PROJECT, db_path=PROJECT / "project.db", config=cfg
    ).summarize()
    validation = FinalValidator(cfg).validate(project_root=PROJECT)

    freeze = FinalFreezeService(
        project_root=PROJECT, db_path=PROJECT / "project.db", config=cfg
    )
    first = freeze.freeze()
    second = freeze.freeze()

    exporter = TyporaExportService(
        project_root=PROJECT, db_path=PROJECT / "project.db", config=cfg
    )
    exp1 = exporter.export(include_source_pdf=True)
    exp2 = exporter.export(include_source_pdf=True)

    after = _upstream_hashes()
    final_path = PROJECT / "final.md"
    export_md = None
    if exp1.markdown_path:
        export_md = exp1.markdown_path

    typora_note = "not_performed"
    if export_md and export_md.exists():
        # do not auto-launch GUI apps in CI-ish script; probe config only
        exe = (cfg.get("typora") or {}).get("executable_path") or ""
        if exe and Path(exe).exists():
            typora_note = f"executable_configured:{exe}"
        else:
            typora_note = "typora_executable_not_configured"

    source_pdf = PROJECT / "source.pdf"
    export_pdf = (exp1.export_path / "source.pdf") if exp1.export_path else None

    report = {
        "phase": "9",
        "project": str(PROJECT),
        "readiness": readiness,
        "validation": {
            "ok": validation.ok,
            "status": validation.status,
            "blocking": validation.blocking,
            "page_markers": validation.page_markers,
            "figure_markers": validation.figure_markers,
            "image_links_total": validation.image_links_total,
            "image_links_valid": validation.image_links_valid,
            "image_links_missing": validation.image_links_missing,
            "absolute_paths": validation.absolute_paths,
            "unsafe_paths": validation.unsafe_paths,
            "horizontal_rules": validation.horizontal_rules,
            "release_warnings": validation.release_warnings,
        },
        "freeze_first": {
            "success": first.success,
            "status": first.status,
            "clean_sha256": first.clean_sha256,
            "final_sha256": first.final_sha256,
            "byte_identical": first.byte_identical,
            "cached": first.cached,
        },
        "freeze_second": {
            "success": second.success,
            "status": second.status,
            "cached": second.cached,
        },
        "export_first": {
            "success": exp1.success,
            "status": exp1.status,
            "path": str(exp1.export_path) if exp1.export_path else None,
            "figure_count": exp1.figure_count,
            "final_hash": exp1.final_hash,
            "export_md_hash": file_sha256(export_md) if export_md and export_md.exists() else None,
            "source_pdf_match": (
                file_sha256(source_pdf) == file_sha256(export_pdf)
                if source_pdf.exists() and export_pdf and export_pdf.exists()
                else False
            ),
            "error": exp1.error,
        },
        "export_second": {
            "success": exp2.success,
            "status": exp2.status,
            "cached": exp2.cached,
        },
        "upstream_unchanged": before == after,
        "clean_sha_unchanged": after["clean"]["clean.md"] == clean_sha_before,
        "typora_smoke": typora_note,
        "math_heavy_validation": "pending",
        "acceptance": {
            "validation_pass": validation.ok,
            "final_exists": final_path.exists(),
            "byte_identical": bool(first.byte_identical),
            "page_markers_0": validation.page_markers == 0,
            "figure_markers_0": validation.figure_markers == 0,
            "images_4_4": validation.image_links_total == 4
            and validation.image_links_valid == 4,
            "export_ok": exp1.success,
            "export_md_matches_final": (
                export_md is not None
                and export_md.exists()
                and file_sha256(export_md) == file_sha256(final_path)
            ),
            "freeze_second_cached": bool(second.cached or second.status == "UP_TO_DATE"),
            "export_second_cached": bool(exp2.cached or exp2.status == "UP_TO_DATE"),
            "upstream_unchanged": before == after,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["acceptance"], ensure_ascii=False, indent=2))
    print(f"report → {REPORT}")
    ok = all(report["acceptance"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

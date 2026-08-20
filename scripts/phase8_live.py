"""Phase 8 live cleaner pilot on 8-page Kuzilek project."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from services.assembled_markdown_validator import PAGE_MARKER_RE
from services.batch_cleaner_service import BatchCleanerService
from services.clean_document_validator import CleanDocumentValidator
from services.clean_readiness_service import CleanReadinessService
from services.transcription_validator import FIGURE_MARKER_RE
from utils.hashing import file_sha256

PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase8_live_report.json"


def _hashes() -> dict:
    out = {}
    for name, folder, pat in (
        ("raw", PROJECT / "intermediate", "raw.md"),
        ("markdown_pages", PROJECT / "markdown_pages", "*.md"),
        ("resolved_pages", PROJECT / "resolved_pages", "*.md"),
        ("figures", PROJECT / "figures", "*"),
    ):
        d = {}
        path = folder
        if pat == "raw.md":
            f = path / "raw.md"
            if f.exists():
                d["raw.md"] = file_sha256(f)
        else:
            if path.is_dir():
                for p in sorted(path.glob(pat)):
                    if p.is_file():
                        d[p.name] = file_sha256(p)
        out[name] = d
    return out


def main() -> int:
    if not (PROJECT / "intermediate" / "raw.md").exists():
        print("raw.md missing — run Phase 7 first")
        return 1

    cfg = load_config()
    before = _hashes()

    svc = BatchCleanerService(
        project_root=PROJECT,
        db_path=PROJECT / "project.db",
        config=cfg,
        text_provider=None,  # SMART: rules-only unless AI needed; no AI in pilot
    )
    analysis = svc.analyze(list(range(1, 9)))
    first = svc.process_pages(list(range(1, 9)), force=True)
    second = svc.process_pages(list(range(1, 9)), force=False)

    after = _hashes()
    clean_path = PROJECT / "intermediate" / "clean.md"
    traced_path = PROJECT / "intermediate" / "clean_traced.md"
    clean = clean_path.read_text(encoding="utf-8") if clean_path.exists() else ""
    traced = traced_path.read_text(encoding="utf-8") if traced_path.exists() else ""

    imgs = re.findall(r"!\[[^\]]*]\((figures/[^)]+)\)", clean)
    missing = [i for i in imgs if not (PROJECT / i).exists()]
    doc_v = CleanDocumentValidator().validate(
        project_root=PROJECT, expected_pages=list(range(1, 9))
    )
    readiness = CleanReadinessService(
        project_root=PROJECT, db_path=PROJECT / "project.db", config=cfg
    ).summarize(list(range(1, 9)))

    report = {
        "phase": "8",
        "analysis": analysis,
        "first": first,
        "second": {
            "cached_pages": second.get("cached"),
            "ai_called": second.get("ai_called"),
            "document_cached": second.get("document_cached"),
            "success": second.get("success"),
        },
        "clean_md": {
            "size_bytes": clean_path.stat().st_size if clean_path.exists() else 0,
            "page_markers": len(PAGE_MARKER_RE.findall(clean)),
            "figure_markers": len(FIGURE_MARKER_RE.findall(clean)),
            "figure_links": len(imgs),
            "missing_figures": missing,
        },
        "clean_traced": {
            "page_markers": len(PAGE_MARKER_RE.findall(traced)),
        },
        "document_validation": {
            "ok": doc_v.ok,
            "blocking": doc_v.blocking,
            "warnings": doc_v.warnings,
        },
        "readiness": readiness,
        "upstream_unchanged": before == after,
        "hashes_before": before,
        "hashes_after": after,
        "typora_smoke": "not_performed",
        "math_heavy_validation": "pending",
        "acceptance": {
            "clean_page_markers_0": len(PAGE_MARKER_RE.findall(clean)) == 0,
            "clean_figure_markers_0": len(FIGURE_MARKER_RE.findall(clean)) == 0,
            "figure_links_valid": len(missing) == 0 and len(imgs) >= 4,
            "traced_markers_8": len(PAGE_MARKER_RE.findall(traced)) == 8,
            "raw_unchanged": before.get("raw") == after.get("raw"),
            "upstream_unchanged": before == after,
            "document_ok": doc_v.ok,
            "second_cache": bool(second.get("document_cached") or second.get("cached")),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report["acceptance"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

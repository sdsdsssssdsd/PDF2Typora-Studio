"""Phase 6 live figure pilot — promote experiment pages 3/4/7 then analyze."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from core.models import PipelineStage, StageStatus
from services.batch_figure_service import BatchFigureService
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256

PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase6_live_report.json"
PILOT_PAGES = (3, 4, 7)


def _promote_experiment(page: int) -> bool:
    exp_root = PROJECT / "experiments" / "transcription" / f"page_{page:04d}"
    if not exp_root.is_dir():
        return False
    attempts = sorted(exp_root.iterdir(), reverse=True)
    if not attempts:
        return False
    latest = attempts[0]
    resp_path = latest / "response.json"
    md_path = latest / "markdown.md"
    if not resp_path.exists():
        return False
    result = json.loads(resp_path.read_text(encoding="utf-8"))
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else result.get("markdown", "")
    canon_md = PROJECT / "markdown_pages" / f"page_{page:04d}.md"
    canon_json = PROJECT / "page_results" / f"page_{page:04d}.json"
    canon_md.parent.mkdir(parents=True, exist_ok=True)
    canon_json.parent.mkdir(parents=True, exist_ok=True)
    canon_md.write_text(f"<!-- PAGE: {page:04d} -->\n\n{md}", encoding="utf-8")
    payload = {
        "result": result,
        "provenance": {"mode": "pilot_promote", "attempt_dir": str(latest)},
        "acceptance": {"mode": "pilot"},
    }
    canon_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main() -> int:
    project = PROJECT
    pdf = project / "source.pdf"
    db_path = project / "project.db"
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    promoted = []
    for p in PILOT_PAGES:
        if _promote_experiment(p):
            promoted.append(p)
            repo.upsert_stage_state(p, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
    db.close()

    svc = BatchFigureService(
        project_root=project,
        pdf_path=pdf,
        db_path=db_path,
        pdf_hash=file_sha256(pdf),
        config=load_config(),
    )
    summary = svc.process_pages(list(PILOT_PAGES), analyze_only=False)

    page_details = []
    for p in PILOT_PAGES:
        exp = project / "experiments" / "figures" / f"page_{p:04d}"
        figs = []
        if exp.is_dir():
            for d in exp.iterdir():
                rep = d / "candidate_report.json"
                if rep.exists():
                    figs.append(json.loads(rep.read_text(encoding="utf-8")))
        page_details.append({"page": p, "debug": figs})

    report = {
        "promoted_from_experiments": promoted,
        "pilot_pages": list(PILOT_PAGES),
        **summary,
        "page_details": page_details,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

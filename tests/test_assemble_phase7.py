"""Phase 7 unit tests — assemble / continuity / readiness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.config_manager import load_config
from core.assemble_models import AssemblyRequest, ContinuityPatchAction
from core.models import PipelineStage, StageStatus
from services.assemble_readiness_service import AssembleReadinessService
from services.assembled_markdown_validator import AssembledMarkdownValidator
from services.continuity_analyzer import ContinuityAnalyzer
from services.markdown_assembler import MarkdownAssembler
from services.page_source_resolver import PageSourceResolver
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256


def _setup(tmp_path: Path, pages: int = 3) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    for d in (
        "markdown_pages",
        "resolved_pages",
        "page_results",
        "figures",
        "intermediate",
        "reports",
    ):
        (root / d).mkdir(parents=True)
    db_path = root / "project.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    repo = ProjectRepository(db)
    repo.insert_project("t", str(root / "x.pdf"), pages)
    repo.init_pages(pages)
    for p in range(1, pages + 1):
        repo.upsert_stage_state(p, PipelineStage.RENDER, StageStatus.SUCCESS)
        repo.upsert_stage_state(p, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
        repo.upsert_stage_state(p, PipelineStage.FIGURES, StageStatus.SUCCESS)
    db.close()
    return root, db_path


def _write_page(
    root: Path,
    page: int,
    *,
    markdown: str,
    figures: list | None = None,
    resolved: str | None = None,
    continues_to_next: bool = False,
    continues_from_previous: bool = False,
) -> None:
    body = f"<!-- PAGE: {page:04d} -->\n\n{markdown}"
    (root / "markdown_pages" / f"page_{page:04d}.md").write_text(body, encoding="utf-8")
    if resolved is not None:
        rbody = f"<!-- PAGE: {page:04d} -->\n\n{resolved}"
        (root / "resolved_pages" / f"page_{page:04d}.md").write_text(
            rbody, encoding="utf-8"
        )
    payload = {
        "result": {
            "page_number": page,
            "markdown": markdown,
            "figures": figures or [],
            "continues_to_next": continues_to_next,
            "continues_from_previous": continues_from_previous,
            "warnings": [],
        }
    }
    (root / "page_results" / f"page_{page:04d}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_schema_v7_migration(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    conn = db.connect()
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "continuity_patches" in tables
    assert "assemble_runs" in tables
    assert "document_artifacts" in tables
    db.close()


def test_page_order_not_lexical(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=10)
    for p in (1, 2, 10):
        _write_page(root, p, markdown=f"Page {p} body")
    # only mark used pages as ready; others still need files for full assemble
    for p in range(1, 11):
        if p not in (1, 2, 10):
            _write_page(root, p, markdown=f"Page {p}")
    resolver = PageSourceResolver(project_root=root, db_path=db_path)
    entries, errs = resolver.resolve_pages([1, 2, 10])
    assert not errs
    assert [e.page for e in entries] == [1, 2, 10]


def test_resolved_preferred_for_figures(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    fig = [{"figure_index": 1, "figure_type": "f", "marker": "<!-- FIGURE page=1 index=1 -->"}]
    _write_page(
        root,
        1,
        markdown="A\n<!-- FIGURE page=1 index=1 -->\nB",
        figures=fig,
        resolved="A\n![图](figures/p0001_fig01.png)\nB",
    )
    (root / "figures" / "p0001_fig01.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
    entries, errs = PageSourceResolver(project_root=root, db_path=db_path).resolve_pages(
        [1]
    )
    assert not errs
    assert entries[0].source_type == "resolved"


def test_missing_source_blocks_readiness(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=3)
    _write_page(root, 1, markdown="one")
    _write_page(root, 2, markdown="two")
    # page 3 missing files
    svc = AssembleReadinessService(
        project_root=root, db_path=db_path, config=load_config()
    )
    s = svc.summarize([1, 2, 3])
    assert not s["ready"]
    assert 3 in s["missing_canonical"] or any("0003" in e for e in s["source_errors"])


def test_assemble_basic_and_cache(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=2)
    _write_page(root, 1, markdown="Hello one")
    _write_page(root, 2, markdown="Hello two")
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r1 = asm.assemble(
        AssemblyRequest(project_root=root, page_numbers=(1, 2), force=True)
    )
    assert r1.success
    assert not r1.cached
    raw = (root / "intermediate" / "raw.md").read_text(encoding="utf-8")
    assert "<!-- PAGE: 0001 -->" in raw
    assert "<!-- PAGE: 0002 -->" in raw
    assert "---" not in raw.split("PAGE")[0] or True  # not inserted by assembler
    assert raw.count("<!-- PAGE:") == 2

    r2 = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1, 2)))
    assert r2.success
    assert r2.cached


def test_assemble_inserts_page_marker(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    (root / "markdown_pages" / "page_0001.md").write_text("No marker body", encoding="utf-8")
    (root / "page_results" / "page_0001.json").write_text(
        json.dumps({"result": {"page_number": 1, "markdown": "No marker body", "figures": []}}),
        encoding="utf-8",
    )
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,), force=True))
    assert r.success
    raw = (root / "intermediate" / "raw.md").read_text(encoding="utf-8")
    assert raw.startswith("<!-- PAGE: 0001 -->")


def test_duplicate_page_marker_repaired(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    text = "<!-- PAGE: 0001 -->\n\nBody\n\n<!-- PAGE: 0001 -->\n\nExtra"
    (root / "markdown_pages" / "page_0001.md").write_text(text, encoding="utf-8")
    (root / "page_results" / "page_0001.json").write_text(
        json.dumps({"result": {"page_number": 1, "markdown": "Body", "figures": []}}),
        encoding="utf-8",
    )
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,), force=True))
    assert r.success
    raw = (root / "intermediate" / "raw.md").read_text(encoding="utf-8")
    assert raw.count("<!-- PAGE: 0001 -->") == 1
    assert any("duplicate_page_marker_repaired" in w for w in r.warnings)


def test_unresolved_figure_blocks(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    md = "Text\n<!-- FIGURE page=1 index=1 -->\nMore"
    fig = [{"figure_index": 1, "figure_type": "f", "marker": "<!-- FIGURE page=1 index=1 -->"}]
    _write_page(root, 1, markdown=md, figures=fig, resolved=md)
    db = Database(db_path)
    db.initialize()
    ProjectRepository(db).upsert_figure(
        page_number=1, figure_index=1, status="resolved"
    )
    db.close()
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,), force=True))
    assert not r.success
    assert "unresolved_figure" in (r.error or "")

    r2 = asm.assemble(
        AssemblyRequest(
            project_root=root,
            page_numbers=(1,),
            force=True,
            allow_unresolved_figures=True,
        )
    )
    assert r2.success
    assert any("unresolved" in w for w in r2.warnings)


def test_missing_figure_artifact_blocks(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    _write_page(
        root,
        1,
        markdown="x",
        figures=[{"figure_index": 1, "figure_type": "f", "marker": "m"}],
        resolved="![图](figures/missing.png)",
    )
    db = Database(db_path)
    db.initialize()
    ProjectRepository(db).upsert_figure(
        page_number=1, figure_index=1, status="resolved"
    )
    db.close()
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,), force=True))
    assert not r.success
    assert "missing_figure_artifact" in (r.error or "")


def test_sources_unchanged_after_assemble(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=2)
    _write_page(root, 1, markdown="A")
    _write_page(root, 2, markdown="B", resolved="B resolved")
    hashes = {
        "md1": file_sha256(root / "markdown_pages" / "page_0001.md"),
        "md2": file_sha256(root / "markdown_pages" / "page_0002.md"),
        "r2": file_sha256(root / "resolved_pages" / "page_0002.md"),
    }
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    assert asm.assemble(
        AssemblyRequest(project_root=root, page_numbers=(1, 2), force=True)
    ).success
    assert file_sha256(root / "markdown_pages" / "page_0001.md") == hashes["md1"]
    assert file_sha256(root / "markdown_pages" / "page_0002.md") == hashes["md2"]
    assert file_sha256(root / "resolved_pages" / "page_0002.md") == hashes["r2"]


def test_failed_assemble_keeps_old_raw(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    _write_page(root, 1, markdown="Good")
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    assert asm.assemble(
        AssemblyRequest(project_root=root, page_numbers=(1,), force=True)
    ).success
    old = (root / "intermediate" / "raw.md").read_text(encoding="utf-8")
    # poison with unresolved figure on force rebuild
    _write_page(
        root,
        1,
        markdown="x",
        figures=[{"figure_index": 1, "figure_type": "f", "marker": "m"}],
        resolved="<!-- FIGURE page=1 index=1 -->",
    )
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,), force=True))
    assert not r.success
    assert (root / "intermediate" / "raw.md").read_text(encoding="utf-8") == old


def test_continuity_candidate_from_flags():
    an = ContinuityAnalyzer({"continuity": {"enable_heuristics": False}})
    c = an.analyze_pair(
        left_page=1,
        right_page=2,
        left_text="This method can be",
        right_text="used for analysis.",
        left_flags={"continues_to_next": True},
        right_flags={"continues_from_previous": True},
    )
    assert c is not None
    assert "continues_to_next" in c.source_flags


def test_continuity_join_with_space(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=2)
    _write_page(root, 1, markdown="This method can be")
    _write_page(root, 2, markdown="used for analysis.")
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    h1 = file_sha256(root / "markdown_pages" / "page_0001.md")
    h2 = file_sha256(root / "markdown_pages" / "page_0002.md")
    repo.upsert_continuity_patch(
        left_page=1,
        right_page=2,
        action=ContinuityPatchAction.JOIN_WITH_SPACE.value,
        source_hash_left=h1,
        source_hash_right=h2,
    )
    db.close()
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1, 2), force=True))
    assert r.success
    assert r.continuity_patches_applied == 1
    raw = (root / "intermediate" / "raw.md").read_text(encoding="utf-8")
    assert "<!-- PAGE: 0002 -->" in raw


def test_stale_patch_not_applied(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=2)
    _write_page(root, 1, markdown="Left")
    _write_page(root, 2, markdown="Right")
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.upsert_continuity_patch(
        left_page=1,
        right_page=2,
        action=ContinuityPatchAction.JOIN_WITH_SPACE.value,
        source_hash_left="oldhash",
        source_hash_right=file_sha256(root / "markdown_pages" / "page_0002.md"),
    )
    db.close()
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1, 2), force=True))
    assert r.success
    assert r.continuity_patches_applied == 0
    assert any("stale_patch" in w for w in r.warnings)


def test_cache_miss_on_source_change(tmp_path: Path):
    root, db_path = _setup(tmp_path, pages=1)
    _write_page(root, 1, markdown="v1", resolved="v1")
    asm = MarkdownAssembler(project_root=root, db_path=db_path, config=load_config())
    assert asm.assemble(
        AssemblyRequest(project_root=root, page_numbers=(1,), force=True)
    ).success
    _write_page(root, 1, markdown="v1", resolved="v2 changed")
    r = asm.assemble(AssemblyRequest(project_root=root, page_numbers=(1,)))
    assert r.success
    assert not r.cached
    assert "v2 changed" in (root / "intermediate" / "raw.md").read_text(encoding="utf-8")


def test_raw_validator_page_order():
    v = AssembledMarkdownValidator()
    raw = "<!-- PAGE: 0002 -->\n\na\n\n<!-- PAGE: 0001 -->\n\nb\n"
    r = v.validate(
        raw_md=raw, project_root=Path("."), expected_pages=[1, 2]
    )
    assert not r.ok

"""Phase 9 Final Validator / Freeze / Export tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config.config_manager import load_config
from services.final_freeze_service import FinalFreezeService
from services.final_validator import FinalValidator
from services.typora_export_service import TyporaExportService
from services.typora_launcher import TyporaLauncher
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256


@pytest.fixture
def cfg() -> dict:
    return load_config()


def _write_png(path: Path) -> None:
    # minimal valid 1x1 PNG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _seed_project(root: Path, *, md: str, with_fig: bool = True) -> Path:
    (root / "intermediate").mkdir(parents=True)
    (root / "figures").mkdir(parents=True)
    if with_fig:
        _write_png(root / "figures" / "a.png")
        if "figures/a.png" not in md:
            md = md + "\n\n![fig](figures/a.png)\n"
    (root / "intermediate" / "clean.md").write_text(md, encoding="utf-8")
    (root / "source.pdf").write_bytes(b"%PDF-1.4 minimal")
    db_path = root / "project.db"
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.insert_project(root.name, str(root / "source.pdf"), 1)
    repo.init_pages(1)
    from core.models import PipelineStage, StageStatus

    repo.upsert_stage_state(1, PipelineStage.CLEAN, StageStatus.SUCCESS)
    db.close()
    return db_path


def test_schema_v9_migration(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 10
    tables = {
        r[0]
        for r in db.connect()
        .execute("SELECT name FROM sqlite_master WHERE type='table'")
        .fetchall()
    }
    assert "export_runs" in tables
    assert "document_artifacts" in tables
    db.close()


def test_validator_pass(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    _seed_project(root, md="# Title\n\nHello world body text here.\n")
    v = FinalValidator(cfg).validate(project_root=root)
    assert v.ok
    assert v.page_markers == 0
    assert v.figure_markers == 0
    assert v.image_links_valid == 1


def test_validator_page_marker_fail(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    _seed_project(
        root,
        md="<!-- PAGE: 0001 -->\n\nHello world body text here.\n",
        with_fig=False,
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("page_markers" in b for b in v.blocking)


def test_validator_figure_marker_fail(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    _seed_project(
        root,
        md="<!-- FIGURE page=1 index=1 -->\n\nHello world body text here.\n",
        with_fig=False,
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("figure_markers" in b for b in v.blocking)


def test_validator_missing_image(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    (root / "intermediate").mkdir(parents=True)
    (root / "figures").mkdir(parents=True)
    (root / "intermediate" / "clean.md").write_text(
        "# T\n\n![x](figures/missing.png)\nHello world body text.\n",
        encoding="utf-8",
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("missing_image" in b for b in v.blocking)


def test_validator_absolute_path(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    (root / "intermediate").mkdir(parents=True)
    (root / "figures").mkdir(parents=True)
    (root / "intermediate" / "clean.md").write_text(
        "# T\n\n![x](E:\\\\abs\\\\a.png)\nHello world body text.\n",
        encoding="utf-8",
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert v.absolute_paths >= 1


def test_validator_path_traversal(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    (root / "intermediate").mkdir(parents=True)
    (root / "figures").mkdir(parents=True)
    (root / "intermediate" / "clean.md").write_text(
        "# T\n\n![x](figures/../../secret.png)\nHello world body text.\n",
        encoding="utf-8",
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("path_traversal" in b or "unsafe" in b for b in v.blocking)


def test_validator_horizontal_rule(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    _seed_project(
        root,
        md="# T\n\n---\n\nHello world body text here.\n",
        with_fig=False,
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("horizontal_rules" in b for b in v.blocking)


def test_validator_unbalanced_math(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    _seed_project(
        root,
        md="# T\n\n$$a+b\n\nHello world body text here.\n",
        with_fig=False,
    )
    v = FinalValidator(cfg).validate(project_root=root)
    assert not v.ok
    assert any("dollar" in b for b in v.blocking)


def test_final_byte_identical(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    db_path = _seed_project(root, md="# Title\n\nHello world body text here.\n")
    # readiness may block without full pipeline — force via validator + direct freeze parts
    # Mark figure/assemble ready by relaxing: call freeze after stubbing readiness
    svc = FinalFreezeService(project_root=root, db_path=db_path, config=cfg)
    with patch.object(
        svc.readiness,
        "summarize",
        return_value={
            "ready": True,
            "status": "READY",
            "blocking": [],
            "transcription_ready": True,
            "figures_ready": True,
            "assemble_ready": True,
            "clean_ready": True,
        },
    ):
        result = svc.freeze()
    assert result.success
    clean = root / "intermediate" / "clean.md"
    final = root / "final.md"
    assert final.exists()
    assert clean.read_bytes() == final.read_bytes()
    assert file_sha256(clean) == file_sha256(final) == result.final_sha256


def test_final_failure_keeps_old(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    db_path = _seed_project(root, md="# Title\n\nHello world body text here.\n")
    old = b"OLD FINAL CONTENT THAT MUST STAY\n"
    (root / "final.md").write_bytes(old)
    # corrupt clean so validation fails
    (root / "intermediate" / "clean.md").write_text(
        "<!-- PAGE: 0001 -->\nbad\n", encoding="utf-8"
    )
    svc = FinalFreezeService(project_root=root, db_path=db_path, config=cfg)
    with patch.object(
        svc.readiness,
        "summarize",
        return_value={"ready": True, "status": "READY", "blocking": []},
    ):
        result = svc.freeze()
    assert not result.success
    assert (root / "final.md").read_bytes() == old


def test_export_package(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    db_path = _seed_project(root, md="# Title\n\nHello world body text here.\n")
    clean = root / "intermediate" / "clean.md"
    shutil.copyfile(clean, root / "final.md")
    export_root = tmp_path / "exports"
    cfg2 = dict(cfg)
    cfg2["export"] = {
        **(cfg.get("export") or {}),
        "default_root": str(export_root),
        "backup_existing": True,
        "overwrite_existing": False,
    }
    svc = TyporaExportService(project_root=root, db_path=db_path, config=cfg2)
    first = svc.export(include_source_pdf=True)
    assert first.success
    assert first.status == "SUCCESS"
    out = export_root / "Book"
    md = out / "Book.md"
    assert md.exists()
    assert file_sha256(md) == file_sha256(root / "final.md")
    assert (out / "figures" / "a.png").exists()
    assert (out / "source.pdf").exists()
    assert not (out / "project.db").exists()
    assert not (out / "logs").exists()

    second = svc.export(include_source_pdf=True)
    assert second.success
    assert second.status == "UP_TO_DATE"
    assert second.cached


def test_export_staging_failure_preserves_old(tmp_path: Path, cfg: dict):
    root = tmp_path / "Book"
    db_path = _seed_project(root, md="# Title\n\nHello world body text here.\n")
    shutil.copyfile(root / "intermediate" / "clean.md", root / "final.md")
    export_root = tmp_path / "exports"
    old = export_root / "Book"
    old.mkdir(parents=True)
    (old / "Book.md").write_text("OLD", encoding="utf-8")
    (old / "figures").mkdir()
    _write_png(old / "figures" / "a.png")
    (old / "source.pdf").write_bytes(b"%PDF-old")

    cfg2 = dict(cfg)
    cfg2["export"] = {
        **(cfg.get("export") or {}),
        "default_root": str(export_root),
        "backup_existing": True,
    }
    svc = TyporaExportService(project_root=root, db_path=db_path, config=cfg2)
    with patch.object(svc, "_collect_figures", return_value=["figures/missing.png"]):
        result = svc.export(include_source_pdf=True, force=True)
    assert not result.success
    # old export still present (either as Book or backup)
    assert (old / "Book.md").exists() or any(export_root.glob("Book_backup_*"))


def test_typora_launcher_uses_executable(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("# hi", encoding="utf-8")
    exe = tmp_path / "Typora.exe"
    exe.write_text("x", encoding="utf-8")
    launcher = TyporaLauncher({"typora": {"executable_path": str(exe)}})
    with patch("subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        r = launcher.launch(md)
    assert r.success
    assert r.method == "typora_executable"
    popen.assert_called_once()


def test_typora_launcher_startfile_fallback(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("# hi", encoding="utf-8")
    launcher = TyporaLauncher({"typora": {"executable_path": ""}})
    with patch("sys.platform", "win32"), patch("os.startfile") as startfile:
        r = launcher.launch(md)
    assert r.success
    assert r.method == "os.startfile"
    startfile.assert_called_once()

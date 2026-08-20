"""Phase 8 cleaner unit tests."""

from __future__ import annotations

from pathlib import Path

from config.config_manager import load_config
from core.models import PipelineStage, StageStatus
from services.batch_cleaner_service import BatchCleanerService
from services.cleaner_validator import CleanerValidator
from services.cleaning_need_analyzer import CleaningNeedAnalyzer
from services.deterministic_cleaner import DeterministicCleaner
from services.raw_page_splitter import RawPageSplitter
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256


def _proj(tmp_path: Path, pages: int = 2) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    for d in ("intermediate", "clean_pages", "page_results", "reports", "figures"):
        (root / d).mkdir(parents=True)
    db_path = root / "p.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    repo = ProjectRepository(db)
    repo.insert_project("t", str(root / "x.pdf"), pages)
    repo.init_pages(pages)
    for p in range(1, pages + 1):
        repo.upsert_stage_state(p, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
    db.close()
    return root, db_path


def test_schema_v8(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    names = {
        r[0]
        for r in db.connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "cleaner_reviews" in names
    db.close()


def test_raw_page_splitter_order():
    raw = (
        "<!-- PAGE: 0001 -->\n\nOne\n\n"
        "<!-- PAGE: 0002 -->\n\nTwo\n\n"
        "<!-- PAGE: 0010 -->\n\nTen\n"
    )
    frags = RawPageSplitter().split_text(raw)
    assert [f.page_number for f in frags] == [1, 2, 10]
    assert "PAGE" not in frags[0].body


def test_convert_math_delimiters():
    det = DeterministicCleaner(load_config())
    r = det.clean(page_number=1, body=r"Inline \(x+1\) and display \[a+b=c\].")
    assert "$x+1$" in r.cleaned
    assert "$$" in r.cleaned
    assert "a+b=c" in r.cleaned


def test_remove_hr_and_printed_label():
    det = DeterministicCleaner(load_config())
    body = "37\n\nHello\n\n---\n\nWorld"
    r = det.clean(page_number=1, body=body, printed_page_label="37")
    assert "37" not in r.cleaned.splitlines()[0:1] or "Hello" in r.cleaned
    assert not any(ln.strip() == "---" for ln in r.cleaned.splitlines())
    assert any(a["action"] == "remove_printed_page_label" for a in r.actions)


def test_printed_label_not_in_prose():
    det = DeterministicCleaner(load_config())
    body = "Equation 37 describes the model."
    r = det.clean(page_number=1, body=body, printed_page_label="37")
    assert "Equation 37" in r.cleaned


def test_validator_math_format_pass():
    v = CleanerValidator(load_config())
    src = r"\[a+b=c\]"
    cln = "$$\na+b=c\n$$"
    # after deterministic convert both sides for fair check:
    det = DeterministicCleaner(load_config())
    src2 = det.clean(page_number=1, body=src).cleaned
    r = v.validate(source=src2, cleaned=cln)
    assert r.ok


def test_validator_math_content_block():
    v = CleanerValidator(load_config())
    r = v.validate(source="$x+1$", cleaned="$x+2$")
    assert not r.ok
    assert any(i.code == "math_content_changed" for i in r.blocking)


def test_validator_numeric_block():
    v = CleanerValidator(load_config())
    r = v.validate(
        source="This method uses 32 samples.",
        cleaned="This method uses 64 samples.",
    )
    assert any(i.code == "numeric_content_changed" for i in r.blocking)


def test_validator_prose_rewrite_block():
    v = CleanerValidator(load_config())
    r = v.validate(
        source="The method is effective.",
        cleaned="The approach is highly effective.",
    )
    assert any(i.code == "visible_prose_changed" for i in r.blocking)


def test_validator_image_block():
    v = CleanerValidator(load_config())
    r = v.validate(
        source="![图](figures/a.png)",
        cleaned="![图](figures/b.png)",
    )
    assert any(i.code == "image_reference_changed" for i in r.blocking)


def test_validator_table_block():
    v = CleanerValidator(load_config())
    src = "| a | b |\n| --- | --- |\n| 0.95 | x |"
    cln = "| a | b |\n| --- | --- |\n| 0.96 | x |"
    r = v.validate(source=src, cleaned=cln)
    assert any(i.code == "table_content_changed" for i in r.blocking)


def test_batch_cleaner_rules_and_cache(tmp_path: Path):
    root, db_path = _proj(tmp_path, 2)
    raw = (
        "<!-- PAGE: 0001 -->\n\nHello world\n\n"
        "<!-- PAGE: 0002 -->\n\nInline \\(a+b\\) ok\n"
    )
    (root / "intermediate" / "raw.md").write_text(raw, encoding="utf-8")
    raw_hash = file_sha256(root / "intermediate" / "raw.md")

    svc = BatchCleanerService(project_root=root, db_path=db_path, config=load_config())
    s1 = svc.process_pages([1, 2], force=True)
    assert s1.get("failed", 0) == 0
    assert (root / "clean_pages" / "page_0001.md").exists()
    assert (root / "intermediate" / "clean.md").exists()
    clean = (root / "intermediate" / "clean.md").read_text(encoding="utf-8")
    traced = (root / "intermediate" / "clean_traced.md").read_text(encoding="utf-8")
    assert "<!-- PAGE:" not in clean
    assert traced.count("<!-- PAGE:") == 2
    assert "$a+b$" in (root / "clean_pages" / "page_0002.md").read_text(encoding="utf-8")

    s2 = svc.process_pages([1, 2], force=False)
    assert s2.get("cached", 0) >= 1 or s2.get("document_cached")

    assert file_sha256(root / "intermediate" / "raw.md") == raw_hash


def test_failed_clean_keeps_old(tmp_path: Path):
    root, db_path = _proj(tmp_path, 1)
    (root / "intermediate" / "raw.md").write_text(
        "<!-- PAGE: 0001 -->\n\nGood page\n", encoding="utf-8"
    )
    svc = BatchCleanerService(project_root=root, db_path=db_path, config=load_config())
    assert svc.process_pages([1], force=True).get("success") is not False
    old = (root / "intermediate" / "clean.md").read_text(encoding="utf-8")
    # corrupt by removing clean page and forcing builder fail path via missing pages
    (root / "clean_pages" / "page_0001.md").unlink()
    # rebuild with empty clean_pages should fail but old clean.md remains until builder
    # Our builder only replaces after success — simulate by calling builder directly
    from services.clean_document_builder import CleanDocumentBuilder

    r = CleanDocumentBuilder(root).build([1], force=True)
    assert not r.success
    assert (root / "intermediate" / "clean.md").read_text(encoding="utf-8") == old


def test_cleaning_need_analyzer_detects_math():
    an = CleaningNeedAnalyzer()
    r = an.analyze(page_number=1, cleaned_body=r"still has \(x\)", deterministic_issues=[])
    assert r.needs_ai
    assert "math_delimiter_issue" in r.reasons

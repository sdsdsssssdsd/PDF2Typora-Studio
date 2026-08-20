"""Database schema tests."""

import tempfile
from pathlib import Path

from storage.database import Database
from storage.repository import ProjectRepository


def test_database_initialize_and_pages():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        db.initialize()
        repo = ProjectRepository(db)
        repo.insert_project("test_book", "/tmp/source.pdf", 5)
        repo.init_pages(5)
        pages = repo.list_pages()
        assert len(pages) == 5
        assert pages[0]["status"] == "WAITING"
        assert pages[4]["page_number"] == 5
        stages = repo.list_stage_states("render")
        assert len(stages) == 5
        assert stages[0]["status"] == "waiting"
        assert db.get_schema_version() == 10
        db.close()

"""Batch transcription pipeline tests (no live Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.model_profiles import ModelProfile, ModelProfileStore
from core.models import (
    BatchItemStatus,
    ModelQualification,
    PipelineStage,
    StageStatus,
    TranscriptionOptions,
)
from services.batch_transcription_service import BatchTranscriptionService
from services.transcription_service import TranscriptionService
from storage.database import Database
from storage.repository import ProjectRepository

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _good_json(page: int) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "page_number": page,
            "markdown": f"Page {page} body text about the paper.",
            "figures": [],
            "continues_from_previous": False,
            "continues_to_next": False,
            "warnings": [],
            "needs_review": False,
        }
    )


class _FakeClient:
    def list_tags(self):
        return [
            {"name": "primary:1", "digest": "digest-primary"},
            {"name": "fallback:1", "digest": "digest-fallback"},
        ]

    def list_running(self):
        return []

    def unload_model(self, model: str):
        return {}


class _FakeProvider:
    def __init__(self, by_model: dict[str, list] | None = None) -> None:
        self._client = _FakeClient()
        self.calls: list[str] = []
        self.by_model = by_model or {}

    def transcribe_page_structured(self, **kwargs):
        model = kwargs["model"]
        page = kwargs["page_number"]
        self.calls.append(model)
        queue = self.by_model.get(model)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item, {"total_duration_ns": 1_000_000_000, "size_vram": 111}
        return _good_json(page), {
            "total_duration_ns": 1_000_000_000,
            "size_vram": 111,
        }


def _setup_project(tmp_path: Path, pages: int = 4):
    root = tmp_path / "proj"
    (root / "pages").mkdir(parents=True)
    db_path = root / "project.db"
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.insert_project("t", "s.pdf", pages)
    repo.init_pages(pages)
    for n in range(1, pages + 1):
        png = root / "pages" / f"page_{n:04d}.png"
        png.write_bytes(TINY_PNG)
        repo.upsert_stage_state(n, PipelineStage.RENDER, StageStatus.SUCCESS)
    db.close()
    return root, db_path


def _service(tmp_path: Path, provider, pages: int = 4):
    root, db_path = _setup_project(tmp_path, pages)
    store = ModelProfileStore(tmp_path / "profiles.json")
    store.upsert(
        ModelProfile(
            model_name="primary:1",
            digest="digest-primary",
            qualification=ModelQualification.QUALIFIED,
        )
    )
    store.upsert(
        ModelProfile(
            model_name="fallback:1",
            digest="digest-fallback",
            qualification=ModelQualification.QUALIFIED,
        )
    )
    trans = TranscriptionService(provider, root, db_path)
    batch = BatchTranscriptionService(
        transcription=trans,
        project_root=root,
        db_path=db_path,
        profiles=store,
        page_count=pages,
        config={
            "batch_transcription": {
                "auto_accept": True,
                "use_cache": True,
                "unload_on_finish": False,
                "max_quality_retries": 1,
                "max_timeout_retries": 1,
                "keep_alive": "5m",
            }
        },
    )
    return batch, root, db_path, trans


def test_unrendered_pages_skipped(tmp_path: Path):
    provider = _FakeProvider()
    batch, root, db_path, _ = _service(tmp_path, provider, pages=3)
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.upsert_stage_state(2, PipelineStage.RENDER, StageStatus.WAITING)
    db.close()
    created = batch.create_run(
        pages=[1, 2, 3], primary_model="primary:1", require_qualified=True
    )
    assert 2 not in created.queued_pages
    assert created.skipped_unrendered == 1


def test_auto_accept_and_canonical(tmp_path: Path):
    provider = _FakeProvider()
    batch, root, db_path, _ = _service(tmp_path, provider, pages=2)
    created = batch.create_run(pages=[1, 2], primary_model="primary:1")
    batch.process_page(created.run_id, 1)
    md = root / "markdown_pages" / "page_0001.md"
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "<!-- PAGE: 0001 -->" in text
    db = Database(db_path)
    db.initialize()
    item = ProjectRepository(db).get_batch_item(created.run_id, 1)
    db.close()
    assert item["status"] == BatchItemStatus.AUTO_ACCEPTED.value


def test_canonical_cache_second_run(tmp_path: Path):
    provider = _FakeProvider()
    batch, root, db_path, _ = _service(tmp_path, provider, pages=1)
    created = batch.create_run(pages=[1], primary_model="primary:1")
    batch.process_page(created.run_id, 1)
    calls = len(provider.calls)
    created2 = batch.create_run(pages=[1], primary_model="primary:1")
    result = batch.process_page(created2.run_id, 1)
    assert result.cached
    assert result.status == BatchItemStatus.CACHED.value
    assert len(provider.calls) == calls


def test_pause_resume_order(tmp_path: Path):
    provider = _FakeProvider()
    batch, _, db_path, _ = _service(tmp_path, provider, pages=4)
    created = batch.create_run(pages=[1, 2, 3, 4], primary_model="primary:1")
    batch.process_page(created.run_id, 1)
    batch.process_page(created.run_id, 2)
    batch.mark_run(created.run_id, "PAUSED")
    nxt = batch.next_waiting(created.run_id)
    assert nxt == 3


def test_crash_recovery(tmp_path: Path):
    provider = _FakeProvider()
    batch, _, db_path, _ = _service(tmp_path, provider, pages=5)
    created = batch.create_run(pages=[1, 2, 3, 4, 5], primary_model="primary:1")
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.update_batch_run(created.run_id, status="RUNNING")
    repo.update_batch_item(created.run_id, 5, status="RUNNING")
    db.close()
    n = batch.recover_stale_runs()
    assert n >= 1
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    run = repo.get_batch_run(created.run_id)
    item = repo.get_batch_item(created.run_id, 5)
    db.close()
    assert run["status"] == "INTERRUPTED"
    assert item["status"] == "WAITING"


def test_oom_fallback(tmp_path: Path):
    provider = _FakeProvider(
        {
            "primary:1": [RuntimeError("CUDA out of memory")],
            "fallback:1": [_good_json(1)],
        }
    )
    batch, _, db_path, _ = _service(tmp_path, provider, pages=1)
    created = batch.create_run(
        pages=[1],
        primary_model="primary:1",
        fallback_model="fallback:1",
    )
    result = batch.process_page(created.run_id, 1)
    assert result.fallback_used
    assert result.status == BatchItemStatus.AUTO_ACCEPTED.value


def test_context_overflow_escalates_once(tmp_path: Path):
    class OverflowThenOk(_FakeProvider):
        def transcribe_page_structured(self, **kwargs):
            opts = kwargs.get("options")
            self.calls.append(str(getattr(opts, "num_ctx", None)))
            if getattr(opts, "num_ctx", None) != 8192:
                raise RuntimeError("prompt is too long: 5486 tokens exceed the context length of 4096")
            return _good_json(kwargs["page_number"]), {
                "total_duration_ns": 1,
                "size_vram": 1,
            }

    provider = OverflowThenOk()
    batch, _, _, trans = _service(tmp_path, provider, pages=1)
    attempt = trans.transcribe_page(
        page_number=1,
        image_path=batch.project_root / "pages" / "page_0001.png",
        model="primary:1",
        options=TranscriptionOptions(num_ctx=4096, use_cache=False, force=True),
    )
    assert attempt.status == "SUCCESS"
    assert "8192" in provider.calls


def test_unqualified_cannot_start_batch(tmp_path: Path):
    provider = _FakeProvider()
    batch, _, _, _ = _service(tmp_path, provider, pages=1)
    batch.profiles.upsert(
        ModelProfile(
            model_name="primary:1",
            digest="digest-primary",
            qualification=ModelQualification.LIMITED,
        )
    )
    with pytest.raises(ValueError):
        batch.create_run(pages=[1], primary_model="primary:1")

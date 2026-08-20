"""Phase 5 live Vision: qualify pages 1/4/8, then 8-page batch + cache rerun."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.model_profiles import ModelProfileStore
from ai.providers.ollama_provider import OllamaVisionProvider
from ai.runtime.ollama_manager import OllamaRuntimeManager
from core.models import PipelineStage, StageStatus
from services.batch_transcription_service import BatchTranscriptionService
from services.transcription_service import TranscriptionService
from storage.database import Database
from storage.repository import ProjectRepository

GEMMA = "gemma3:4b-it-q4_K_M"
QWEN = "qwen3.5:9b-q4_K_M"
PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase5_live_report.json"


def _ensure_render_stages(db_path: Path, n: int = 8) -> None:
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.init_pages(n)
    for page in range(1, n + 1):
        repo.upsert_stage_state(page, PipelineStage.RENDER, StageStatus.SUCCESS)
    db.close()


def main() -> int:
    project = PROJECT
    db_path = project / "project.db"
    _ensure_render_stages(db_path)

    settings = {
        "mode": "bundled",
        "runtime_path": Path(r"E:\Ollama"),
        "models_path": Path(r"E:\Ollama\models"),
        "port_start": 11435,
        "port_end": 11450,
        "no_cloud": True,
        "connect_seconds": 3,
        "start_seconds": 30,
        "request_seconds": 420,
    }
    manager = OllamaRuntimeManager(settings=settings)
    started = False
    report: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        status = manager.start_bundled()
        started = True
        url = status.base_url
        print(f"Ollama ready: {url} version={status.version}", flush=True)
        provider = OllamaVisionProvider(url, request_timeout=420)
        trans = TranscriptionService(provider, project, db_path)
        store = ModelProfileStore(ROOT / "config" / "model_profiles.json")
        batch = BatchTranscriptionService(
            transcription=trans,
            project_root=project,
            db_path=db_path,
            profiles=store,
            page_count=8,
            config={
                "batch_transcription": {
                    "auto_accept": True,
                    "use_cache": True,
                    "unload_on_finish": True,
                    "keep_alive": "5m",
                    "max_quality_retries": 1,
                    "max_timeout_retries": 1,
                }
            },
        )

        t0 = time.perf_counter()
        print("=== Gemma 5A pages 1,4,8 ===", flush=True)
        gemma = batch.qualify_pages(model=GEMMA, pages=[1, 4, 8], num_ctx=None)
        report["gemma_5a"] = gemma
        report["gemma_5a_elapsed_s"] = round(time.perf_counter() - t0, 2)
        print(json.dumps(gemma, ensure_ascii=False, indent=2), flush=True)

        t1 = time.perf_counter()
        print("=== Qwen page1 @8192 ===", flush=True)
        qwen1 = batch.qualify_pages(model=QWEN, pages=[1], num_ctx=8192)
        report["qwen_page1_8192"] = qwen1
        print(json.dumps(qwen1, ensure_ascii=False, indent=2), flush=True)
        if qwen1.get("qualification") == "QUALIFIED" or qwen1.get("schema_ok", 0) >= 1:
            print("=== Qwen 5A pages 1,4,8 @8192 ===", flush=True)
            qwen3 = batch.qualify_pages(model=QWEN, pages=[1, 4, 8], num_ctx=8192)
            report["qwen_5a"] = qwen3
        else:
            report["qwen_5a"] = {"skipped": True, "reason": "page1 did not pass"}
        report["qwen_elapsed_s"] = round(time.perf_counter() - t1, 2)

        primary = None
        fallback = None
        gq = gemma.get("qualification")
        qq = (report.get("qwen_5a") or {}).get("qualification")
        if gq == "QUALIFIED":
            primary = GEMMA
        elif qq == "QUALIFIED":
            primary = QWEN
        if gq == "QUALIFIED" and qq == "QUALIFIED":
            fallback = QWEN

        report["primary"] = primary
        report["fallback"] = fallback

        if primary:
            print(f"=== Batch 8 pages primary={primary} ===", flush=True)
            t2 = time.perf_counter()
            created = batch.create_run(
                pages=list(range(1, 9)),
                primary_model=primary,
                fallback_model=fallback,
            )
            report["skipped_unrendered"] = created.skipped_unrendered
            batch.mark_run(created.run_id, "RUNNING")
            results = []
            while True:
                page = batch.next_waiting(created.run_id)
                if page is None:
                    break
                print(f"  page {page}...", flush=True)
                results.append(batch.process_page(created.run_id, page).__dict__)
            summary = batch.finalize_run(created.run_id)
            report["batch8"] = summary
            report["batch8_pages"] = results
            report["batch8_elapsed_s"] = round(time.perf_counter() - t2, 2)

            print("=== Batch 8 pages second (cache) ===", flush=True)
            t3 = time.perf_counter()
            created2 = batch.create_run(
                pages=list(range(1, 9)),
                primary_model=primary,
                fallback_model=fallback,
            )
            cache_results = []
            ai_calls = 0
            while True:
                page = batch.next_waiting(created2.run_id)
                if page is None:
                    break
                r = batch.process_page(created2.run_id, page)
                cache_results.append(r.__dict__)
                if not r.cached:
                    ai_calls += 1
            summary2 = batch.finalize_run(created2.run_id)
            report["batch8_cache"] = summary2
            report["batch8_cache_ai_calls"] = ai_calls
            report["batch8_cache_elapsed_s"] = round(time.perf_counter() - t3, 2)
        else:
            report["batch8"] = {
                "skipped": True,
                "reason": "no QUALIFIED vision model after 5A",
            }

        report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = str(exc)
        raise
    finally:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {REPORT}", flush=True)
        if started:
            try:
                manager.stop_managed()
            except Exception:
                pass
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

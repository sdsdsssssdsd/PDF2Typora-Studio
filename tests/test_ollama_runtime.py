"""Runtime manager tests — ports, ownership, directory inspect (no real Ollama)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ai.runtime.ollama_manager import (
    OllamaProcessInfo,
    OllamaRuntimeManager,
    find_available_port,
    inspect_ollama_directory,
)


def test_find_available_port():
    port = find_available_port(11435, 11450)
    assert 11435 <= port <= 11450


def test_inspect_missing_directory(tmp_path: Path):
    info = inspect_ollama_directory(tmp_path / "nope")
    assert not info.executable_found
    assert info.warnings


def test_inspect_with_fake_exe(tmp_path: Path):
    runtime = tmp_path / "ollama"
    runtime.mkdir()
    exe = runtime / "ollama.exe"
    exe.write_bytes(b"fake")
    models = tmp_path / "models"
    models.mkdir()
    (models / "blobs").mkdir()
    info = inspect_ollama_directory(runtime)
    assert info.executable_found
    assert info.executable == exe
    # models may be sibling
    sibling = inspect_ollama_directory(runtime)
    assert sibling.executable_found


def test_stop_skipped_when_not_started_by_app(tmp_path: Path):
    mgr = OllamaRuntimeManager(
        bundled_runtime=tmp_path / "runtime",
        bundled_models=tmp_path / "models",
        settings={
            "runtime_path": tmp_path / "runtime",
            "models_path": tmp_path / "models",
            "port_start": 11435,
            "port_end": 11450,
            "no_cloud": True,
            "connect_seconds": 1,
            "start_seconds": 2,
            "request_seconds": 5,
            "external_base_url": "http://127.0.0.1:11434",
            "custom_base_url": "",
            "mode": "bundled",
        },
    )
    mgr._process_info = OllamaProcessInfo(
        pid=999999,
        executable=tmp_path / "ollama.exe",
        base_url="http://127.0.0.1:11435",
        port=11435,
        started_by_app=False,
    )
    fake = MagicMock()
    mgr._process = fake
    status = mgr.stop_bundled()
    fake.terminate.assert_not_called()
    fake.kill.assert_not_called()
    assert mgr._process_info is not None  # unchanged when not owned


def test_stop_terminates_owned_process(tmp_path: Path):
    mgr = OllamaRuntimeManager(
        bundled_runtime=tmp_path / "runtime",
        bundled_models=tmp_path / "models",
        settings={
            "runtime_path": tmp_path / "runtime",
            "models_path": tmp_path / "models",
            "port_start": 11435,
            "port_end": 11450,
            "no_cloud": True,
            "connect_seconds": 1,
            "start_seconds": 2,
            "request_seconds": 5,
            "external_base_url": "http://127.0.0.1:11434",
            "custom_base_url": "",
            "mode": "bundled",
        },
    )
    fake = MagicMock()
    fake.poll.return_value = None
    fake.wait.return_value = 0
    mgr._process = fake
    mgr._process_info = OllamaProcessInfo(
        pid=12345,
        executable=tmp_path / "ollama.exe",
        base_url="http://127.0.0.1:11435",
        port=11435,
        started_by_app=True,
    )
    mgr.stop_bundled()
    fake.terminate.assert_called_once()
    assert mgr._process is None
    assert mgr._process_info is None


def test_build_environment_isolated(tmp_path: Path):
    mgr = OllamaRuntimeManager(
        bundled_runtime=tmp_path / "runtime",
        bundled_models=tmp_path / "models",
        settings={
            "runtime_path": tmp_path / "runtime",
            "models_path": tmp_path / "models",
            "port_start": 11435,
            "port_end": 11450,
            "no_cloud": True,
            "connect_seconds": 1,
            "start_seconds": 2,
            "request_seconds": 5,
            "external_base_url": "http://127.0.0.1:11434",
            "custom_base_url": "",
            "mode": "bundled",
        },
    )
    env = mgr.build_environment("127.0.0.1:11435")
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_NO_CLOUD"] == "1"
    assert Path(env["OLLAMA_MODELS"]) == (tmp_path / "models").resolve()

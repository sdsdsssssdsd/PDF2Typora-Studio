"""Configuration loader with default + user merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CONFIG_DIR.parent
_USER_PATH = _CONFIG_DIR / "user.yaml"


def project_root() -> Path:
    return _PROJECT_ROOT


def load_config() -> dict[str, Any]:
    default_path = _CONFIG_DIR / "default.yaml"

    with default_path.open(encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}

    if _USER_PATH.exists():
        with _USER_PATH.open(encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_cfg)

    _normalize_ollama_paths(config)
    return config


def save_user_config(updates: dict[str, Any]) -> None:
    """Merge *updates* into config/user.yaml (persistent preferences only)."""
    existing: dict[str, Any] = {}
    if _USER_PATH.exists():
        with _USER_PATH.open(encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    merged = _deep_merge(existing, updates)
    with _USER_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)


def get_ollama_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a normalized ollama settings dict."""
    cfg = config or load_config()
    ollama = cfg.get("ollama", {})
    bundled = ollama.get("bundled", {})
    external = ollama.get("external", {})
    custom = ollama.get("custom", {})
    timeout = ollama.get("timeout", {})

    runtime = bundled.get("runtime_path") or ollama.get("bundled_runtime", "")
    models = bundled.get("models_path") or ollama.get("bundled_models", "")
    external_url = external.get("base_url") or ollama.get(
        "external_base_url", "http://127.0.0.1:11434"
    )

    return {
        "mode": ollama.get("mode", "bundled"),
        "runtime_path": Path(runtime) if runtime else _PROJECT_ROOT / "runtime" / "ollama",
        "models_path": Path(models) if models else _PROJECT_ROOT / "runtime" / "models",
        "port_start": int(bundled.get("port_start", 11435)),
        "port_end": int(bundled.get("port_end", 11450)),
        "no_cloud": bool(bundled.get("no_cloud", True)),
        "external_base_url": external_url.rstrip("/"),
        "custom_base_url": (custom.get("base_url") or "").rstrip("/"),
        "connect_seconds": float(timeout.get("connect_seconds", 3)),
        "start_seconds": float(timeout.get("start_seconds", 30)),
        "request_seconds": float(timeout.get("request_seconds", 300)),
        "selected_model": cfg.get("ai", {}).get("selected_model")
        or cfg.get("ai", {}).get("model", ""),
        "vision_only_filter": bool(cfg.get("ai", {}).get("vision_only_filter", True)),
    }


def _normalize_ollama_paths(config: dict[str, Any]) -> None:
    ws = config.get("workspace", {})
    if "path" in ws and not Path(ws["path"]).is_absolute():
        ws["path"] = str((_PROJECT_ROOT / ws["path"]).resolve())

    ollama = config.get("ollama", {})
    bundled = ollama.setdefault("bundled", {})

    # Prefer nested keys; fall back to flat keys from Phase 1
    if "runtime_path" not in bundled and "bundled_runtime" in ollama:
        bundled["runtime_path"] = ollama["bundled_runtime"]
    if "models_path" not in bundled and "bundled_models" in ollama:
        bundled["models_path"] = ollama["bundled_models"]

    for key in ("runtime_path", "models_path"):
        if key in bundled and bundled[key] and not Path(bundled[key]).is_absolute():
            bundled[key] = str((_PROJECT_ROOT / bundled[key]).resolve())

    for key in ("bundled_runtime", "bundled_models"):
        if key in ollama and ollama[key] and not Path(ollama[key]).is_absolute():
            ollama[key] = str((_PROJECT_ROOT / ollama[key]).resolve())


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

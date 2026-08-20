"""Manage remote API providers (DeepSeek text, OpenAI-compatible vision/text)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from services.api_credential_store import ApiCredentialStore
from utils.logger import get_logger

logger = get_logger("api_provider_manager")


@dataclass
class ApiProviderConfig:
    provider_id: str
    display_name: str
    base_url: str
    model: str = ""
    credential_id: str = ""
    supports_text: bool = True
    supports_json: bool = True
    supports_vision: bool = False
    vision_detected: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApiProbeResult:
    ok: bool
    status_code: int | None = None
    models: list[str] = field(default_factory=list)
    error: str | None = None
    supports_vision: bool | None = None


class ApiProviderManager:
    """Non-Ollama providers. API keys never logged."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        store: ApiCredentialStore | None = None,
    ) -> None:
        self.config = config or {}
        self.store = store or ApiCredentialStore()
        api_cfg = self.config.get("api_providers") or {}
        self.providers = api_cfg.get("providers") or {}
        self.task_routing = api_cfg.get("task_routing") or {}

    def list_provider_configs(self) -> list[ApiProviderConfig]:
        out: list[ApiProviderConfig] = []
        for pid, raw in self.providers.items():
            if not isinstance(raw, dict):
                continue
            out.append(
                ApiProviderConfig(
                    provider_id=pid,
                    display_name=str(raw.get("display_name") or pid),
                    base_url=str(raw.get("base_url") or "").rstrip("/"),
                    model=str(raw.get("model") or ""),
                    credential_id=str(
                        raw.get("credential_id")
                        or self.store.credential_id(pid)
                    ),
                    supports_text=bool(raw.get("supports_text", True)),
                    supports_json=bool(raw.get("supports_json", True)),
                    supports_vision=bool(raw.get("supports_vision", False)),
                    vision_detected=raw.get("vision_detected"),
                )
            )
        return out

    def get_api_key(self, provider_id: str) -> str | None:
        cfg = (self.providers.get(provider_id) or {})
        cid = cfg.get("credential_id") or self.store.credential_id(provider_id)
        return self.store.get_secret(str(cid))

    def set_api_key(self, provider_id: str, api_key: str) -> str:
        cid = self.store.credential_id(provider_id)
        self.store.set_secret(cid, api_key)
        return cid

    def probe(self, provider_id: str, *, timeout: float = 10.0) -> ApiProbeResult:
        raw = self.providers.get(provider_id) or {}
        base = str(raw.get("base_url") or "").rstrip("/")
        if not base:
            return ApiProbeResult(ok=False, error="missing_base_url")
        key = self.get_api_key(provider_id)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # Never log key
        try:
            with httpx.Client(timeout=timeout, headers=headers) as client:
                # OpenAI-compatible models endpoint
                resp = client.get(f"{base}/models")
                if resp.status_code >= 400:
                    # DeepSeek also supports /models
                    return ApiProbeResult(
                        ok=False,
                        status_code=resp.status_code,
                        error=f"HTTP {resp.status_code}",
                    )
                data = resp.json()
                models = []
                for item in data.get("data") or []:
                    mid = item.get("id")
                    if mid:
                        models.append(str(mid))
                vision = bool(raw.get("supports_vision", False))
                # Heuristic: model name contains vl / vision
                if any("vl" in m.lower() or "vision" in m.lower() for m in models):
                    vision = True
                return ApiProbeResult(
                    ok=True,
                    status_code=resp.status_code,
                    models=models,
                    supports_vision=vision,
                )
        except Exception as exc:  # noqa: BLE001
            logger.info("API probe failed for %s: %s", provider_id, type(exc).__name__)
            return ApiProbeResult(ok=False, error=str(exc))

    def chat_text(
        self,
        provider_id: str,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> str:
        raw = self.providers.get(provider_id) or {}
        base = str(raw.get("base_url") or "").rstrip("/")
        key = self.get_api_key(provider_id)
        if not base or not key:
            raise RuntimeError("provider_not_configured")
        use_model = model or str(raw.get("model") or "")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
        }
        with httpx.Client(timeout=timeout, headers=headers) as client:
            resp = client.post(f"{base}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )

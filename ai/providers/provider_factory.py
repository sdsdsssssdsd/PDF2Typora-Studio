"""Factory for page-transcription Vision providers and text-cleanup adapters."""

from __future__ import annotations

from typing import Any

from ai.providers.api_provider_manager import ApiProviderManager
from ai.providers.ollama_provider import OllamaVisionProvider
from ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from ai.runtime.ollama_manager import OllamaRuntimeManager
from config.config_manager import load_config
from services.api_credential_store import ApiCredentialStore
from utils.logger import get_logger

logger = get_logger("provider_factory")


def _routing(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    return dict((cfg.get("api_providers") or {}).get("task_routing") or {})


def vision_route(config: dict[str, Any] | None = None) -> str:
    return str(_routing(config).get("page_transcription") or "ollama")


def cleanup_route(config: dict[str, Any] | None = None) -> str:
    routing = _routing(config)
    if routing.get("same_api_for_clean_and_vision"):
        return str(
            routing.get("text_cleanup")
            or routing.get("page_transcription")
            or "ollama"
        )
    return str(routing.get("text_cleanup") or "deepseek")


def is_external_route(route: str | None) -> bool:
    r = (route or "").strip().lower()
    return r not in {"", "ollama", "none"}


def is_external_vision_route(route: str | None = None) -> bool:
    return is_external_route(route or vision_route())


class ApiTextCleanupAdapter:
    """Text-only cleaner backed by OpenAI-compatible chat completions."""

    def __init__(
        self,
        manager: ApiProviderManager,
        provider_id: str,
        *,
        model: str | None = None,
    ) -> None:
        self.manager = manager
        self.provider_id = provider_id
        self.model = model

    def clean_markdown(
        self,
        *,
        markdown: str,
        page_number: int,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        _ = page_number
        schema_hint = ""
        if schema:
            import json

            schema_hint = (
                "\n\n请严格输出符合下列 JSON Schema 的单个 JSON 对象，不要 Markdown 围栏：\n"
                + json.dumps(schema, ensure_ascii=False)[:5000]
            )
        messages = [
            {"role": "system", "content": prompt + schema_hint},
            {"role": "user", "content": markdown},
        ]
        return self.manager.chat_text(
            self.provider_id,
            messages=messages,
            model=self.model,
            temperature=0.0,
        )

    def complete(self, prompt: str, markdown: str) -> str:
        return self.clean_markdown(
            markdown=markdown, page_number=0, prompt=prompt, schema=None
        )


def create_vision_provider(
    *,
    ollama_manager: OllamaRuntimeManager | None = None,
    config: dict[str, Any] | None = None,
    request_timeout: float = 300.0,
) -> OllamaVisionProvider | OpenAICompatibleProvider:
    cfg = config or load_config()
    route = vision_route(cfg)
    if not is_external_vision_route(route):
        url = "http://127.0.0.1:11434"
        if ollama_manager is not None:
            url = (
                ollama_manager.find_reachable_base_url()
                or ollama_manager.resolve_base_url()
            )
        return OllamaVisionProvider(url, request_timeout=request_timeout)

    store = ApiCredentialStore()
    manager = ApiProviderManager(cfg, store)
    raw = (manager.providers or {}).get(route) or {}
    if not raw.get("base_url"):
        logger.warning("External route %s missing base_url; falling back to ollama", route)
        if ollama_manager is not None:
            url = (
                ollama_manager.find_reachable_base_url()
                or ollama_manager.resolve_base_url()
            )
            return OllamaVisionProvider(url, request_timeout=request_timeout)
        return OllamaVisionProvider("http://127.0.0.1:11434", request_timeout=request_timeout)

    provider = OpenAICompatibleProvider.from_manager(
        manager, route, request_timeout=request_timeout
    )
    if not provider.api_key:
        logger.warning("External route %s has no API key in keyring", route)
    return provider


def create_text_provider(
    *,
    config: dict[str, Any] | None = None,
    route: str | None = None,
) -> ApiTextCleanupAdapter | None:
    """Return a text cleaner/reconstructor for the given or configured route."""
    cfg = config or load_config()
    use_route = (route or cleanup_route(cfg)).strip()
    if not is_external_route(use_route):
        return None
    store = ApiCredentialStore()
    manager = ApiProviderManager(cfg, store)
    raw = (manager.providers or {}).get(use_route) or {}
    if not raw.get("base_url") or not manager.get_api_key(use_route):
        logger.warning("Text route %s not configured (base_url/key)", use_route)
        return None
    return ApiTextCleanupAdapter(
        manager,
        use_route,
        model=str(raw.get("model") or "") or None,
    )


def create_reconstruction_client(
    *,
    config: dict[str, Any] | None = None,
) -> ApiTextCleanupAdapter | None:
    """Hybrid 重建优先用 text_cleanup（如 DeepSeek），不走 Vision 路由。"""
    cfg = config or load_config()
    cleanup = cleanup_route(cfg)
    if is_external_route(cleanup):
        return create_text_provider(config=cfg, route=cleanup)
    vision = vision_route(cfg)
    if is_external_route(vision):
        return create_text_provider(config=cfg, route=vision)
    return create_text_provider(config=cfg)

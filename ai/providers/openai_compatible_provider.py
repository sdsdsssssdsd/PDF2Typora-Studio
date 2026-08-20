"""OpenAI-compatible Vision / chat provider (cloud or gateway APIs)."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from ai.base.vision_provider import ModelCapabilities, VisionProvider, VisionResult
from ai.providers.ollama_api_client import OllamaModelInfo, _looks_like_vision
from core.models import TranscriptionOptions
from utils.logger import get_logger

logger = get_logger("openai_compatible_provider")


class _ApiClientShim:
    """Duck-type minimal surface expected by transcription/batch (list_tags / unload)."""

    def __init__(self, owner: "OpenAICompatibleProvider") -> None:
        self._owner = owner

    def list_tags(self) -> list[dict[str, Any]]:
        return [{"name": m, "digest": ""} for m in self._owner.list_models()]

    def unload_model(self, model: str) -> dict[str, Any]:
        _ = model
        return {}

    def list_running(self) -> list[Any]:
        return []

    def show_model(self, name: str) -> dict[str, Any]:
        return {"name": name, "capabilities": ["vision"] if self._owner.supports_vision else []}


class OpenAICompatibleProvider(VisionProvider):
    """Chat Completions API with optional vision (image_url parts)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "",
        provider_id: str = "openai_compatible",
        supports_vision: bool = True,
        connect_timeout: float = 10.0,
        request_timeout: float = 300.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider_id = provider_id
        self.supports_vision = supports_vision
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.extra_headers = dict(extra_headers or {})
        self._client = _ApiClientShim(self)
        self._cached_models: list[str] | None = None

    @classmethod
    def from_manager(
        cls,
        manager: Any,
        provider_id: str,
        *,
        request_timeout: float = 300.0,
    ) -> "OpenAICompatibleProvider":
        raw = (manager.providers or {}).get(provider_id) or {}
        key = manager.get_api_key(provider_id) or ""
        return cls(
            base_url=str(raw.get("base_url") or ""),
            api_key=key,
            model=str(raw.get("model") or ""),
            provider_id=provider_id,
            supports_vision=bool(raw.get("supports_vision", True)),
            request_timeout=request_timeout,
        )

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        return h

    def health_check(self) -> bool:
        if not self.base_url or not self.api_key:
            return False
        try:
            with httpx.Client(
                timeout=httpx.Timeout(8.0, connect=self.connect_timeout),
                headers=self._headers(),
            ) as client:
                resp = client.get(f"{self.base_url}/models")
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> list[str]:
        if self._cached_models is not None:
            return list(self._cached_models)
        if not self.base_url or not self.api_key:
            return [self.model] if self.model else []
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=self.connect_timeout),
                headers=self._headers(),
            ) as client:
                resp = client.get(f"{self.base_url}/models")
                resp.raise_for_status()
                data = resp.json()
            names = [
                str(item.get("id"))
                for item in (data.get("data") or [])
                if item.get("id")
            ]
            if self.model and self.model not in names:
                names.insert(0, self.model)
            self._cached_models = names
            return names
        except Exception as exc:  # noqa: BLE001
            logger.info("list_models failed: %s", type(exc).__name__)
            return [self.model] if self.model else []

    def list_model_infos(self, *, fetch_capabilities: bool = True) -> list[OllamaModelInfo]:
        _ = fetch_capabilities
        out: list[OllamaModelInfo] = []
        for name in self.list_models():
            is_v = self.supports_vision or _looks_like_vision(name, None, [])
            out.append(
                OllamaModelInfo(
                    name=name,
                    parameter_size="api",
                    capabilities=["vision"] if is_v else [],
                    is_vision=is_v,
                    digest="",
                )
            )
        return out

    def list_vision_models(self) -> list[OllamaModelInfo]:
        models = self.list_model_infos()
        vision = [m for m in models if m.is_vision]
        return vision if vision else models

    def get_model_capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            name=model,
            supports_vision=self.supports_vision or _looks_like_vision(model, None, []),
        )

    def set_model(self, model: str) -> None:
        self.model = model

    def analyze_page(
        self,
        image_path: Path,
        prompt: str,
        context: str | None = None,
    ) -> VisionResult:
        full = prompt
        if context:
            full = (
                f"{prompt}\n\n上一页尾部（仅供理解，禁止重复输出）：\n{context}"
            )
        return self.analyze_image(image_path, full, model=self.model)

    def analyze_image(
        self,
        image_path: Path,
        prompt: str,
        model: str | None = None,
        *,
        require_vision: bool = False,
    ) -> VisionResult:
        _ = require_vision
        use_model = model or self.model
        if not use_model:
            return VisionResult(
                success=False, error="No model selected", provider=self.provider_id
            )
        if not image_path.exists():
            return VisionResult(
                success=False,
                model=use_model,
                provider=self.provider_id,
                error=f"Image not found: {image_path}",
            )
        try:
            content, metrics = self._chat_vision(
                image_path=image_path,
                prompt=prompt,
                model=use_model,
                json_mode=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("analyze_image failed")
            return VisionResult(
                success=False,
                model=use_model,
                provider=self.provider_id,
                error=str(exc),
            )
        return VisionResult(
            success=True,
            markdown=content,
            content=content,
            raw_response=content[:2000],
            model=use_model,
            provider=self.provider_id,
            total_duration_ns=metrics.get("total_duration_ns"),
        )

    def transcribe_page_structured(
        self,
        *,
        image_path: Path,
        page_number: int,
        prompt: str,
        schema: dict[str, Any],
        model: str,
        options: TranscriptionOptions,
        messages: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        _ = page_number
        system = ""
        user = prompt
        if messages:
            for msg in messages:
                role = msg.get("role")
                if role == "system":
                    system = str(msg.get("content") or "")
                elif role == "user":
                    user = str(msg.get("content") or prompt)
        schema_hint = (
            "\n\n请严格输出符合下列 JSON Schema 的单个 JSON 对象，不要 Markdown 围栏：\n"
            + json.dumps(schema, ensure_ascii=False)[:6000]
        )
        full_user = user + schema_hint
        content, metrics = self._chat_vision(
            image_path=image_path,
            prompt=full_user,
            model=model,
            system=system or None,
            temperature=float(options.temperature),
            json_mode=True,
        )
        return content, metrics

    def _encode_image_data_url(self, image_path: Path) -> str:
        raw = image_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        suffix = image_path.suffix.lower().lstrip(".") or "png"
        mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
        return f"data:{mime};base64,{b64}"

    def _chat_vision(
        self,
        *,
        image_path: Path,
        prompt: str,
        model: str,
        system: str | None = None,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if not self.api_key or not self.base_url:
            raise RuntimeError("API 未配置：请在「外部 API 配置」中填写 Base URL 与 API Key")

        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": self._encode_image_data_url(image_path)},
            },
        ]
        msgs: list[dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user_content})

        payload: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        with httpx.Client(
            timeout=httpx.Timeout(self.request_timeout, connect=self.connect_timeout),
            headers=self._headers(),
        ) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload)
            if resp.status_code >= 400 and json_mode:
                # Some gateways reject response_format — retry without it
                payload.pop("response_format", None)
                resp = client.post(f"{self.base_url}/chat/completions", json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        elapsed_ns = int((time.perf_counter() - started) * 1e9)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            # Some APIs return content parts
            texts = [
                str(p.get("text") or "")
                for p in content
                if isinstance(p, dict)
            ]
            content = "".join(texts)
        usage = data.get("usage") or {}
        metrics = {
            "total_duration_ns": elapsed_ns,
            "load_duration_ns": None,
            "prompt_eval_count": usage.get("prompt_tokens"),
            "prompt_eval_duration_ns": None,
            "eval_count": usage.get("completion_tokens"),
            "eval_duration_ns": None,
            "size_vram": None,
            "context_length": None,
            "provider": self.provider_id,
        }
        return str(content).strip(), metrics

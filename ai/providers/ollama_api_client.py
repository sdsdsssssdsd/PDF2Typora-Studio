"""Thin HTTP client for Ollama REST API."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from core.exceptions import OllamaApiError, OllamaConnectionError
from utils.logger import get_logger

logger = get_logger("ollama_api")


@dataclass
class OllamaHealth:
    healthy: bool
    version: str | None = None
    latency_ms: float | None = None
    error: str | None = None


@dataclass
class OllamaModelInfo:
    name: str
    size_bytes: int | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    family: str | None = None
    capabilities: list[str] = field(default_factory=list)
    is_vision: bool = False
    digest: str | None = None


@dataclass
class RunningModelInfo:
    name: str
    size_vram: int | None = None
    context_length: int | None = None
    expires_at: str | None = None


class OllamaApiClient:
    """HTTP wrapper around Ollama ``/api/*`` endpoints."""

    def __init__(
        self,
        base_url: str,
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                timeout if timeout is not None else self.request_timeout,
                connect=self.connect_timeout,
            ),
        )

    def health_check(self) -> OllamaHealth:
        started = time.perf_counter()
        try:
            with self._client(timeout=self.connect_timeout + 2) as client:
                resp = client.get("/api/version")
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code != 200:
                return OllamaHealth(
                    healthy=False,
                    latency_ms=latency,
                    error=f"HTTP {resp.status_code}",
                )
            data = resp.json()
            version = data.get("version")
            if not version:
                return OllamaHealth(
                    healthy=False,
                    latency_ms=latency,
                    error="Missing version field",
                )
            return OllamaHealth(
                healthy=True, version=str(version), latency_ms=latency
            )
        except httpx.TimeoutException as exc:
            return OllamaHealth(healthy=False, error=f"Timeout: {exc}")
        except httpx.HTTPError as exc:
            return OllamaHealth(healthy=False, error=str(exc))
        except ValueError as exc:
            return OllamaHealth(healthy=False, error=f"Invalid JSON: {exc}")

    def list_tags(self) -> list[dict[str, Any]]:
        data = self._get_json("/api/tags")
        return list(data.get("models") or [])

    def show_model(self, model: str) -> dict[str, Any]:
        return self._post_json("/api/show", {"name": model})

    def list_running(self) -> list[RunningModelInfo]:
        data = self._get_json("/api/ps")
        result: list[RunningModelInfo] = []
        for item in data.get("models") or []:
            details = item.get("details") or {}
            result.append(
                RunningModelInfo(
                    name=item.get("name") or item.get("model") or "",
                    size_vram=item.get("size_vram"),
                    context_length=item.get("context_length")
                    or details.get("context_length"),
                    expires_at=item.get("expires_at"),
                )
            )
        return result

    def chat(
        self,
        model: str,
        prompt: str | None = None,
        image_path: Path | None = None,
        *,
        messages: list[dict[str, Any]] | None = None,
        stream: bool = False,
        format_schema: dict[str, Any] | None = None,
        think: bool | str | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if messages is None:
            message: dict[str, Any] = {"role": "user", "content": prompt or ""}
            if image_path is not None:
                message["images"] = [_encode_image(image_path)]
            messages = [message]
        elif image_path is not None:
            last = dict(messages[-1])
            last["images"] = [_encode_image(image_path)]
            messages = [*messages[:-1], last]

        opts = dict(options or {})
        if temperature is not None:
            opts.setdefault("temperature", temperature)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if opts:
            payload["options"] = opts
        if format_schema is not None:
            payload["format"] = format_schema
        if think is not None:
            payload["think"] = think
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        return self._post_json("/api/chat", payload)

    def unload_model(self, model: str) -> dict[str, Any]:
        """Unload a model by sending keep_alive=0."""
        return self._post_json(
            "/api/generate",
            {"model": model, "keep_alive": 0, "prompt": ""},
        )

    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            with self._client() as client:
                resp = client.get(path)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(str(exc)) from exc
        return self._parse_response(resp)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._client() as client:
                resp = client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(str(exc)) from exc
        return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise OllamaApiError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise OllamaApiError(f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise OllamaApiError("Expected JSON object")
        if data.get("error"):
            raise OllamaApiError(str(data["error"]))
        return data


# Name/family hints when /api/show omits capabilities (common on some builds).
_VISION_NAME_HINTS = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "minicpm_v",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen2vl",
    "llama3.2-vision",
    "llama3.2vision",
    "gemma3",
    "pixtral",
    "internvl",
    "vision",
    "vl-",
    "-vl",
    "vl:",
)


def _looks_like_vision(name: str, family: str | None, caps: list[str]) -> bool:
    if any(str(c).lower() == "vision" for c in caps):
        return True
    blob = f"{name} {family or ''}".lower()
    return any(h in blob for h in _VISION_NAME_HINTS)


def build_model_info(tag: dict[str, Any], show: dict[str, Any] | None = None) -> OllamaModelInfo:
    details = (tag.get("details") or {}) | ((show or {}).get("details") or {})
    caps = list((show or {}).get("capabilities") or [])
    # Some builds nest capabilities under model_info
    if not caps and show:
        info = show.get("model_info") or {}
        maybe = info.get("general.architecture")  # unused; keep caps empty
        _ = maybe
    name = tag.get("name") or tag.get("model") or ""
    family = details.get("family")
    return OllamaModelInfo(
        name=name,
        size_bytes=tag.get("size"),
        parameter_size=details.get("parameter_size"),
        quantization_level=details.get("quantization_level"),
        family=family,
        capabilities=caps,
        is_vision=_looks_like_vision(name, family, caps),
        digest=tag.get("digest"),
    )


def _encode_image(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")

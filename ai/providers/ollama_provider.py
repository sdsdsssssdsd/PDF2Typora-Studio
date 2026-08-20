"""Ollama vision provider — HTTP API via OllamaApiClient."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai.base.vision_provider import ModelCapabilities, VisionProvider, VisionResult
from ai.providers.ollama_api_client import (
    OllamaApiClient,
    OllamaModelInfo,
    build_model_info,
)
from core.exceptions import OllamaVisionNotSupportedError
from core.models import TranscriptionOptions
from utils.logger import get_logger

logger = get_logger("ollama_provider")


class OllamaVisionProvider(VisionProvider):
    """VisionProvider backed by a running Ollama server."""

    def __init__(
        self,
        base_url: str,
        model: str = "",
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = OllamaApiClient(
            self.base_url,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
        )
        self._think_unsupported: set[str] = set()

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = OllamaApiClient(
            self.base_url,
            connect_timeout=self._client.connect_timeout,
            request_timeout=self._client.request_timeout,
        )

    def set_model(self, model: str) -> None:
        self.model = model

    def health_check(self) -> bool:
        return self._client.health_check().healthy

    def list_models(self) -> list[str]:
        return [m.get("name", "") for m in self._client.list_tags() if m.get("name")]

    def list_model_infos(self, *, fetch_capabilities: bool = True) -> list[OllamaModelInfo]:
        tags = self._client.list_tags()
        results: list[OllamaModelInfo] = []
        for tag in tags:
            name = tag.get("name") or ""
            if not name:
                continue
            show = None
            if fetch_capabilities:
                try:
                    show = self._client.show_model(name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("show_model(%s) failed: %s", name, exc)
            results.append(build_model_info(tag, show))
        return results

    def list_vision_models(self) -> list[OllamaModelInfo]:
        models = self.list_model_infos(fetch_capabilities=True)
        vision = [m for m in models if m.is_vision]
        # If capability probing failed for every tag, still offer the installed list
        # so the UI combo is selectable (user can pick a vision model manually).
        return vision if vision else models

    def get_model_capabilities(self, model: str) -> ModelCapabilities:
        show = self._client.show_model(model)
        caps = list(show.get("capabilities") or [])
        details = show.get("details") or {}
        return ModelCapabilities(
            name=model,
            supports_vision="vision" in [c.lower() for c in caps],
            supports_tools="tools" in [c.lower() for c in caps],
            context_length=None,
            extra={
                "capabilities": caps,
                "parameter_size": details.get("parameter_size"),
                "quantization_level": details.get("quantization_level"),
                "family": details.get("family"),
            },
        )

    def analyze_page(
        self,
        image_path: Path,
        prompt: str,
        context: str | None = None,
    ) -> VisionResult:
        full_prompt = prompt
        if context:
            full_prompt = (
                f"{prompt}\n\n"
                "上一页尾部（仅供理解，禁止重复输出）：\n"
                f"{context}"
            )
        return self.analyze_image(image_path, full_prompt, model=self.model)

    def analyze_image(
        self,
        image_path: Path,
        prompt: str,
        model: str | None = None,
        *,
        require_vision: bool = False,
    ) -> VisionResult:
        use_model = model or self.model
        if not use_model:
            return VisionResult(
                success=False,
                error="No model selected",
                provider="ollama",
            )
        if not image_path.exists():
            return VisionResult(
                success=False,
                model=use_model,
                provider="ollama",
                error=f"Image not found: {image_path}",
            )

        if require_vision:
            caps = self.get_model_capabilities(use_model)
            if not caps.supports_vision:
                raise OllamaVisionNotSupportedError(
                    f"Model {use_model} does not advertise vision capability"
                )

        try:
            data = self._client.chat(
                model=use_model,
                prompt=prompt,
                image_path=image_path,
                stream=False,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("analyze_image failed")
            return VisionResult(
                success=False,
                model=use_model,
                provider="ollama",
                error=str(exc),
            )

        message = data.get("message") or {}
        content = message.get("content") or data.get("response") or ""
        return VisionResult(
            success=True,
            markdown=content,
            content=content,
            raw_response=str(data)[:2000],
            model=use_model,
            provider="ollama",
            total_duration_ns=_as_int(data.get("total_duration")),
            load_duration_ns=_as_int(data.get("load_duration")),
            prompt_eval_count=_as_int(data.get("prompt_eval_count")),
            eval_count=_as_int(data.get("eval_count")),
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
        """Send image + prompt + JSON schema; return (content_json, metrics)."""
        from utils.gpu_lock import inference_lock

        opts: dict[str, Any] = {"temperature": options.temperature}
        if options.num_ctx is not None:
            opts["num_ctx"] = options.num_ctx

        think = options.think
        if model in self._think_unsupported:
            think = None

        kwargs: dict[str, Any] = dict(
            model=model,
            prompt=None if messages else prompt,
            image_path=image_path,
            messages=messages,
            stream=False,
            format_schema=schema,
            think=think,
            options=opts,
            keep_alive=options.keep_alive,
        )

        with inference_lock():
            try:
                data = self._client.chat(**kwargs)
            except Exception as exc:
                if think is not None and "think" in str(exc).lower():
                    logger.warning(
                        "Model %s rejected think; retrying without think", model
                    )
                    self._think_unsupported.add(model)
                    kwargs["think"] = None
                    data = self._client.chat(**kwargs)
                else:
                    raise

            # Sample VRAM while model is still loaded
            metrics: dict[str, Any] = {
                "total_duration_ns": _as_int(data.get("total_duration")),
                "load_duration_ns": _as_int(data.get("load_duration")),
                "prompt_eval_count": _as_int(data.get("prompt_eval_count")),
                "prompt_eval_duration_ns": _as_int(data.get("prompt_eval_duration")),
                "eval_count": _as_int(data.get("eval_count")),
                "eval_duration_ns": _as_int(data.get("eval_duration")),
                "size_vram": None,
                "context_length": None,
            }
            try:
                running = self._client.list_running()
                for item in running:
                    if model.split(":")[0] in item.name or item.name == model:
                        metrics["size_vram"] = item.size_vram
                        metrics["context_length"] = item.context_length
                        break
            except Exception:  # noqa: BLE001
                logger.debug("list_running failed after transcription", exc_info=True)

        message = data.get("message") or {}
        content = message.get("content") or ""
        _ = page_number
        return content, metrics


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

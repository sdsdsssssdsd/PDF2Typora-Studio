"""Tests for OllamaApiClient health / tags / show (mocked HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest

from ai.providers.ollama_api_client import OllamaApiClient, build_model_info
from core.exceptions import OllamaApiError, OllamaConnectionError


def _transport(handler):
    return httpx.MockTransport(handler)


def test_health_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": "0.6.0"})

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    health = client.health_check()
    assert health.healthy
    assert health.version == "0.6.0"
    assert health.latency_ms is not None


def test_health_connection_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OllamaApiClient("http://127.0.0.1:9")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:9",
        timeout=2.0,
    )
    health = client.health_check()
    assert not health.healthy
    assert health.error


def test_health_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=2.0,
    )
    health = client.health_check()
    assert not health.healthy
    assert "Timeout" in (health.error or "")


def test_health_invalid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    health = client.health_check()
    assert not health.healthy


def test_health_http_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    health = client.health_check()
    assert not health.healthy
    assert "500" in (health.error or "")


def test_list_tags_and_vision_capability():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3-vl:4b",
                            "size": 3300000000,
                            "details": {
                                "parameter_size": "4B",
                                "quantization_level": "Q4_K_M",
                                "family": "qwen3vl",
                            },
                        },
                        {
                            "name": "llama3.2:3b",
                            "size": 2000000000,
                            "details": {
                                "parameter_size": "3B",
                                "quantization_level": "Q4_K_M",
                                "family": "llama",
                            },
                        },
                    ]
                },
            )
        if request.url.path == "/api/show":
            body = json.loads(request.content.decode())
            if "vl" in body["name"]:
                return httpx.Response(
                    200,
                    json={
                        "capabilities": ["completion", "vision"],
                        "details": {"parameter_size": "4B"},
                    },
                )
            return httpx.Response(
                200,
                json={"capabilities": ["completion"], "details": {"parameter_size": "3B"}},
            )
        return httpx.Response(404)

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    tags = client.list_tags()
    assert len(tags) == 2
    vision = build_model_info(tags[0], client.show_model(tags[0]["name"]))
    text = build_model_info(tags[1], client.show_model(tags[1]["name"]))
    assert vision.is_vision is True
    assert text.is_vision is False


def test_empty_model_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    assert client.list_tags() == []


def test_api_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="fail")

    client = OllamaApiClient("http://127.0.0.1:11434")
    client._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=_transport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5.0,
    )
    with pytest.raises(OllamaApiError):
        client.list_tags()

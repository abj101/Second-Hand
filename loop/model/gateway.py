from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from loop.config import DEFAULT_MODEL
from loop.os.capture import EncodedFrame


def _is_ollama_cloud(model_id: str) -> bool:
    name = (model_id or "").strip()
    return name.endswith(":cloud") or name.endswith("-cloud")


@dataclass
class ModelCall:
    text: str
    parsed: dict[str, Any] | None
    latency_ms: float
    model: str
    # Native Ollama reports these values in nanoseconds.  Keep the raw values
    # as well as the token counts so callers can emit them without coupling to
    # a particular Ollama response version.
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_duration_ns: int | None = None
    total_duration_ns: int | None = None
    image_supplied: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


class VlmGateway:
    """Slim Ollama JSON + image client. No dual-brain, no OpenRouter sprawl."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 512,
        num_ctx: int = 4096,
        keep_alive: str = "5m",
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        frames: list[EncodedFrame] | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ModelCall:
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        images: list[str] = []
        for frame in frames or []:
            images.append(base64.b64encode(frame.data).decode("ascii"))
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{frame.mime};base64,{images[-1]}"},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": user}, *content[1:]]},
        ]
        payload = {
            "model": self.model,
            "messages": _to_ollama(messages, images),
            "stream": False,
            "think": False,
            "format": json_schema if json_schema is not None else "json",
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens if max_tokens is not None else self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        t0 = time.perf_counter()
        seconds = self._timeout_seconds(timeout_s)
        response = httpx.post(
            f"{self._native()}/api/chat",
            json=payload,
            timeout=httpx.Timeout(seconds, connect=min(seconds, 10.0)),
        )
        if json_schema is not None and response.status_code >= 400:
            payload["format"] = "json"
            response = httpx.post(
                f"{self._native()}/api/chat",
                json=payload,
                timeout=httpx.Timeout(seconds, connect=min(seconds, 10.0)),
            )
        _raise_for_status(response)
        message = (response.json() or {}).get("message") or {}
        text = str(message.get("content") or "")
        parsed = _parse_json(text)
        return _model_call_from_response(
            text=text, parsed=parsed, model=self.model,
            latency_ms=(time.perf_counter() - t0) * 1000,
            response=response, image_supplied=bool(frames),
        )

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = True,
        max_tokens: int | None = None,
        frames: list[EncodedFrame] | None = None,
        timeout_s: float | None = None,
    ) -> ModelCall:
        import base64

        images: list[str] = []
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for frame in frames or []:
            images.append(base64.b64encode(frame.data).decode("ascii"))
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{frame.mime};base64,{images[-1]}"},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": user if not frames else user_content,
            },
        ]
        payload = {
            "model": self.model,
            "messages": _to_ollama(messages, images),
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens if max_tokens is not None else self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        t0 = time.perf_counter()
        seconds = self._timeout_seconds(timeout_s)
        response = httpx.post(
            f"{self._native()}/api/chat",
            json=payload,
            timeout=httpx.Timeout(seconds, connect=min(seconds, 10.0)),
        )
        _raise_for_status(response)
        message = (response.json() or {}).get("message") or {}
        text = str(message.get("content") or "")
        parsed = _parse_json(text) if json_mode else None
        return _model_call_from_response(
            text=text, parsed=parsed, model=self.model,
            latency_ms=(time.perf_counter() - t0) * 1000,
            response=response, image_supplied=bool(frames),
        )

    def _timeout_seconds(self, timeout_s: float | None) -> float:
        if timeout_s is not None:
            if timeout_s <= 0:
                raise ValueError("timeout_s must be positive")
            return float(timeout_s)
        return 300.0 if _is_ollama_cloud(self.model) else 180.0

    def unload(self) -> None:
        try:
            httpx.post(
                f"{self._native()}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=60.0,
            )
        except Exception:  # noqa: BLE001
            return

    def _native(self) -> str:
        parsed = urlparse(self.base_url)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[: -len("/v1")]
        return urlunparse((parsed.scheme, parsed.netloc, path or "", "", "", "")).rstrip("/")


def _to_ollama(messages: list[dict[str, Any]], images: list[str]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            native.append({"role": role, "content": content})
            continue
        texts: list[str] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
        item: dict[str, Any] = {"role": role, "content": "\n".join(t for t in texts if t)}
        if role == "user" and images:
            item["images"] = images
        native.append(item)
    return native


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def _model_call_from_response(
    *,
    text: str,
    parsed: dict[str, Any] | None,
    model: str,
    latency_ms: float,
    response: httpx.Response,
    image_supplied: bool,
) -> ModelCall:
    """Normalize optional Ollama accounting fields without requiring them."""
    body = response.json() or {}

    def _integer(name: str) -> int | None:
        value = body.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    metrics = {
        "prompt_tokens": _integer("prompt_eval_count"),
        "output_tokens": _integer("eval_count"),
        "load_duration_ns": _integer("load_duration"),
        "prompt_eval_duration_ns": _integer("prompt_eval_duration"),
        "eval_duration_ns": _integer("eval_duration"),
        "total_duration_ns": _integer("total_duration"),
        "image_supplied": image_supplied,
    }
    return ModelCall(
        text=text, parsed=parsed, latency_ms=latency_ms, model=model,
        prompt_tokens=metrics["prompt_tokens"], output_tokens=metrics["output_tokens"],
        load_duration_ns=metrics["load_duration_ns"],
        prompt_eval_duration_ns=metrics["prompt_eval_duration_ns"],
        eval_duration_ns=metrics["eval_duration_ns"], total_duration_ns=metrics["total_duration_ns"],
        image_supplied=image_supplied, metrics=metrics,
    )
def _raise_for_status(response: httpx.Response) -> None:
    """Keep Ollama's diagnostic when a model/tag rejects a capability."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text.strip()
        suffix = f": {detail[:600]}" if detail else ""
        raise RuntimeError(f"Ollama {response.status_code} for {response.request.url}{suffix}") from exc

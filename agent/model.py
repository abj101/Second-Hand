"""Ollama chat client for local and cloud models (trimmed from loop/model/gateway.py).

Cloud models (`name-cloud` / `name:cloud`) go through the local daemon; no API key needed.
Every call is stateless: the caller passes all context it wants the model to see.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass

import httpx
from PIL import Image


@dataclass
class Reply:
    text: str
    data: dict | None
    ms: float
    prompt_tokens: int | None
    output_tokens: int | None


class Ollama:
    def __init__(self, model: str, *, temperature: float = 0.2, max_tokens: int = 4096,
                 num_ctx: int = 16384, timeout_s: float | None = None) -> None:
        self.model = model
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.options = {"temperature": temperature, "num_predict": max_tokens, "num_ctx": num_ctx}
        cloud = model.endswith(("-cloud", ":cloud"))
        self.timeout_s = timeout_s or (300.0 if cloud else 180.0)

    def ask(self, system: str, user: str, images: list[Image.Image] = (), schema: dict | None = None) -> Reply:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user, "images": [_b64(i) for i in images]},
            ],
            "stream": False,
            "think": False,
            "format": schema or "json",
            "keep_alive": "10m",
            "options": self.options,
        }
        t0 = time.perf_counter()
        r = self._post(payload)
        if r.status_code >= 400 and schema is not None:  # some models reject schema formats; plain JSON mode still works
            payload["format"] = "json"
            r = self._post(payload)
        if r.status_code >= 400:
            raise RuntimeError(f"ollama {r.status_code} ({self.model}): {r.text[:300]}")
        body = r.json()
        text = str((body.get("message") or {}).get("content") or "")
        return Reply(text, parse_json(text), (time.perf_counter() - t0) * 1000,
                     body.get("prompt_eval_count"), body.get("eval_count"))

    def _post(self, payload: dict) -> httpx.Response:
        return httpx.post(f"{self.base_url}/api/chat", json=payload,
                          timeout=httpx.Timeout(self.timeout_s, connect=10.0))


def _b64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parse_json(text: str) -> dict | None:
    """JSON object from model text, tolerating code fences and leading/trailing prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            continue
    return None

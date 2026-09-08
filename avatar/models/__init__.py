from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx
from openai import OpenAI

if TYPE_CHECKING:
    from avatar.harness.capture import EncodedFrame


@dataclass
class ModelCall:
    text: str
    parsed: dict[str, Any] | None
    latency_ms: float
    cost_usd: float | None
    model: str
    provider: str


def is_ollama_cloud(model_id: str) -> bool:
    name = model_id.strip()
    return name.endswith(":cloud") or name.endswith("-cloud")


class VlmGateway:
    """OpenAI-compatible gateway. OpenRouter and Ollama share this client."""

    def __init__(
        self,
        *,
        model: str,
        provider: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        api_key: str | None = None,
        base_url: str | None = None,
        num_ctx: int | None = 4096,
        keep_alive: str | int | None = "5m",
    ) -> None:
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        if provider == "ollama":
            self.client = OpenAI(
                api_key=api_key or os.getenv("OLLAMA_API_KEY", "ollama"),
                base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            )
        elif provider == "openrouter":
            self.client = OpenAI(
                api_key=api_key or os.getenv("OPENROUTER_API_KEY", ""),
                base_url=base_url
                or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            )
        else:
            raise ValueError(f"Unknown provider {provider}")

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        frames: list[EncodedFrame] | None = None,
    ) -> ModelCall:
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for frame in frames or []:
            b64 = base64.b64encode(frame.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{frame.mime};base64,{b64}"},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        t0 = time.perf_counter()
        response = self._create(messages, json_mode=True)
        text, parsed = _parse_message(response.choices[0].message)
        if parsed is None and self.provider == "ollama":
            response = self._create(messages, json_mode=True)
            text, parsed = _parse_message(response.choices[0].message)
        latency_ms = (time.perf_counter() - t0) * 1000
        usage = getattr(response, "usage", None)
        cost = None
        if usage is not None:
            # OpenRouter may attach cost; local is $0.
            cost = float(getattr(usage, "cost", 0) or 0) if self.provider != "ollama" else 0.0
        return ModelCall(
            text=text,
            parsed=parsed,
            latency_ms=latency_ms,
            cost_usd=cost,
            model=self.model,
            provider=self.provider,
        )

    def unload(self) -> None:
        """Drop this model from Ollama VRAM so the next 8GB-fit VLM can load."""
        if self.provider != "ollama":
            return
        try:
            httpx.post(
                f"{self._ollama_native()}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=60.0,
            )
        except Exception:  # noqa: BLE001 — unload is best-effort
            return

    def _create(self, messages: list[dict[str, Any]], *, json_mode: bool):
        if self.provider == "ollama":
            return self._create_ollama(messages, json_mode=json_mode)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if json_mode and _looks_like_json_mode_error(exc):
                return self._create(messages, json_mode=False)
            raise

    def _create_ollama(self, messages: list[dict[str, Any]], *, json_mode: bool):
        # OpenAI-compat silently drops think=false; qwen3.5 then fills `reasoning`
        # with chain-of-thought and leaves content empty. Native /api/chat honors it.
        payload = ollama_chat_payload(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            num_ctx=self.num_ctx,
            keep_alive=self.keep_alive,
            json_mode=json_mode,
        )
        seconds = 300.0 if is_ollama_cloud(self.model) else 180.0
        response = httpx.post(
            f"{self._ollama_native()}/api/chat",
            json=payload,
            timeout=httpx.Timeout(seconds, connect=10.0),
        )
        response.raise_for_status()
        message = (response.json() or {}).get("message") or {}
        wrapped = _SimpleMessage(
            content=str(message.get("content") or ""),
            reasoning=str(message.get("thinking") or "") or None,
            thinking=str(message.get("thinking") or "") or None,
        )
        return _SimpleResponse(choices=[_SimpleChoice(message=wrapped)])

    def _ollama_native(self) -> str:
        parsed = urlparse(str(self.client.base_url))
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[: -len("/v1")]
        return urlunparse((parsed.scheme, parsed.netloc, path or "", "", "", "")).rstrip("/")


def _looks_like_json_mode_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "response_format" in msg or "json_object" in msg or "json schema" in msg


@dataclass
class _SimpleMessage:
    content: str
    reasoning: str | None = None
    thinking: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {"content": self.content, "reasoning": self.reasoning, "thinking": self.thinking}


@dataclass
class _SimpleChoice:
    message: _SimpleMessage


@dataclass
class _SimpleResponse:
    choices: list[_SimpleChoice]
    usage: Any = None


def ollama_chat_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    num_ctx: int | None,
    keep_alive: str | int | None,
    json_mode: bool,
) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    payload: dict[str, Any] = {
        "model": model,
        "messages": to_ollama_messages(messages),
        "stream": False,
        "options": options,
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if json_mode:
        payload["format"] = "json"
        payload["think"] = False
    return payload


def to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            native.append({"role": role, "content": content})
            continue
        texts: list[str] = []
        images: list[str] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    url = str((part.get("image_url") or {}).get("url") or "")
                    if "base64," in url:
                        images.append(url.split("base64,", 1)[1])
                    elif url:
                        images.append(url)
        item: dict[str, Any] = {"role": role, "content": "\n".join(t for t in texts if t)}
        if images:
            item["images"] = images
        native.append(item)
    return native


def _message_texts(message: Any) -> list[str]:
    dump = message.model_dump() if hasattr(message, "model_dump") else {}
    if not isinstance(dump, dict):
        dump = {}
    seen: list[str] = []
    for value in (
        getattr(message, "content", None),
        dump.get("content"),
        getattr(message, "reasoning", None),
        dump.get("reasoning"),
        getattr(message, "thinking", None),
        dump.get("thinking"),
    ):
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped and stripped not in seen:
            seen.append(stripped)
    return seen


def _parse_message(message: Any) -> tuple[str, dict[str, Any] | None]:
    texts = _message_texts(message)
    combined = "\n".join(texts)
    objects = _json_objects(combined)
    if not objects:
        for candidate in texts:
            objects.extend(_json_objects(candidate))
    parsed = _preferred_json(objects)
    return combined, parsed


def _json_objects(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    found: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        end: int | None = None
        for j in range(i, n):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            break
        try:
            data = json.loads(text[i : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data:
            found.append(data)
        i = end + 1
    return found


def _preferred_json(objects: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not objects:
        return None
    for obj in reversed(objects):
        if any(key in obj for key in ("actions", "invoke_skill", "invoke_gsm")):
            return obj
    for obj in reversed(objects):
        if "screen_kind" in obj or "board" in obj or "goal" in obj:
            return obj
    return objects[-1]


def _loads_json(text: str) -> dict[str, Any] | None:
    objects = _json_objects(text)
    return objects[0] if objects else None


def _as_model_rows(items: Any, default_provider: str) -> list[tuple[str, str]]:
    if not items:
        return []
    if isinstance(items, dict):
        items = [items]
    rows: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            model_id = str(item["id"])
            rows.append((model_id, _row_provider(item.get("provider"), model_id, default_provider)))
        elif isinstance(item, str) and item.strip():
            model_id = item.strip()
            rows.append((model_id, _row_provider(None, model_id, default_provider)))
    return rows


def _row_provider(explicit: Any, model_id: str, default_provider: str) -> str:
    if explicit:
        return str(explicit)
    if is_ollama_cloud(model_id):
        return "ollama"
    return default_provider


def _locked_items(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    locked = cfg.get("models", {}).get("locked") or []
    if isinstance(locked, dict):
        locked = [locked]
    return [item for item in locked if isinstance(item, dict) and item.get("id")]


def _catalog_rows(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Locked non-brain + local + cloud. Used when --model toggles off the default eyes."""
    locked = _locked_items(cfg)
    if locked:
        items = [item for item in locked if item.get("role", "primary") != "brain"]
        rows = _as_model_rows(items, "ollama")
    else:
        rows = []
    seen = {model for model, _ in rows}
    extra = _as_model_rows(cfg.get("models", {}).get("local"), "ollama")
    extra += _as_model_rows(cfg.get("models", {}).get("cloud"), "openrouter")
    for model, provider in extra:
        if model not in seen:
            rows.append((model, provider))
            seen.add(model)
    return rows


def configured_models(
    cfg: dict[str, Any],
    only: str | list[str] | None = None,
    *,
    role: str | None = None,
) -> list[tuple[str, str]]:
    """Return (model_id, provider) rows. Locked roster wins after See experiment.

    Brain-role rows are omitted unless ``role='brain'`` so See/Act L1 never
    send screenshots to the text planner.

    ``only`` / AVATAR_MODEL also search ``models.local`` and ``models.cloud``
    so ``--model`` can toggle the single-VLM planner (local qwen3.5 vs
    Ollama Cloud). Unmatched filters are explicit Ollama ids.
    Cloud tags like ``gemma4:31b-cloud`` use the Ollama provider (local daemon
    offloads after ``ollama signin``).
    """
    locked = _locked_items(cfg)
    if locked:
        if role:
            items = [item for item in locked if item.get("role") == role]
        else:
            items = [item for item in locked if item.get("role", "primary") != "brain"]
        rows = _as_model_rows(items, "ollama")
    else:
        rows = _as_model_rows(cfg.get("models", {}).get("local"), "ollama")
        rows += _as_model_rows(cfg.get("models", {}).get("cloud"), "openrouter")

    filters: list[str] = []
    if only:
        filters.extend(only if isinstance(only, list) else [only])
    elif os.getenv("AVATAR_MODEL") or os.getenv("AVATAR_LOCAL_MODEL"):
        filters.append(os.getenv("AVATAR_MODEL") or os.getenv("AVATAR_LOCAL_MODEL") or "")
    filters = [f for f in filters if f]
    if not filters:
        return rows
    matched = [(model, provider) for model, provider in rows if any(f in model for f in filters)]
    if matched:
        return matched
    extra = [(model, provider) for model, provider in _catalog_rows(cfg) if any(f in model for f in filters)]
    if extra:
        return extra
    return [(filt, "ollama") for filt in filters]


def make_gateway(
    cfg: dict[str, Any],
    model_id: str,
    provider: str,
    *,
    num_ctx: int | None = None,
    keep_alive: str | int | None = None,
) -> VlmGateway:
    ollama = cfg.get("ollama") or {}
    if num_ctx is None:
        raw_ctx = ollama.get("num_ctx", 4096)
        num_ctx = None if raw_ctx is None else int(raw_ctx)
    if keep_alive is None:
        keep_alive = ollama.get("keep_alive", "5m")
    return VlmGateway(
        model=model_id,
        provider=provider,
        temperature=float(cfg.get("temperature", 0.7)),
        max_tokens=int(cfg.get("max_tokens", 1024)),
        num_ctx=num_ctx,
        keep_alive=keep_alive,
    )


def _provider_for(cfg: dict[str, Any], model_id: str) -> str:
    for item in _locked_items(cfg):
        if item.get("id") == model_id:
            return str(item.get("provider", "ollama"))
    for mid, provider in _catalog_rows(cfg):
        if mid == model_id:
            return provider
    return "ollama"


def make_eyes(cfg: dict[str, Any], model_id: str | None = None) -> VlmGateway:
    """Vision model (See / scout). Prefer locked role eyes, then primary.

    ``model_id`` overrides the locked default so ``--model`` can toggle
    qwen3.5 vs an Ollama Cloud model on the single-VLM path.
    """
    if model_id:
        return make_gateway(cfg, model_id, _provider_for(cfg, model_id))
    for wanted in ("eyes", "primary"):
        for item in _locked_items(cfg):
            if item.get("role", "primary") == wanted:
                return make_gateway(cfg, str(item["id"]), str(item.get("provider", "ollama")))
    rows = configured_models(cfg)
    if not rows:
        raise ValueError("No eyes/primary model configured.")
    chosen, provider = rows[0]
    return make_gateway(cfg, chosen, provider)


def unload_locked_eyes(cfg: dict[str, Any]) -> None:
    """Drop the locked VLM from Ollama VRAM so CUDA DINO can load."""
    try:
        make_eyes(cfg).unload()
    except Exception:  # noqa: BLE001 — best-effort before detector load
        return


def make_brain(cfg: dict[str, Any], model_id: str | None = None) -> VlmGateway:
    """Text planner. Dual-resident; smaller ctx than eyes. Does not unload eyes."""
    ollama = cfg.get("ollama") or {}
    brain_ctx = int(ollama.get("brain_num_ctx", 2048))
    if model_id:
        provider = "ollama"
        for item in _locked_items(cfg):
            if item.get("id") == model_id:
                provider = str(item.get("provider", "ollama"))
                brain_ctx = int(item.get("num_ctx") or brain_ctx)
                break
        return make_gateway(cfg, model_id, provider, num_ctx=brain_ctx)
    for item in _locked_items(cfg):
        if item.get("role") == "brain":
            ctx = int(item.get("num_ctx") or brain_ctx)
            return make_gateway(
                cfg,
                str(item["id"]),
                str(item.get("provider", "ollama")),
                num_ctx=ctx,
            )
    candidates = cfg.get("models", {}).get("brain_candidates") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    if candidates:
        first = candidates[0]
        if isinstance(first, str):
            return make_gateway(cfg, first, "ollama", num_ctx=brain_ctx)
        if isinstance(first, dict) and first.get("id"):
            return make_gateway(
                cfg,
                str(first["id"]),
                str(first.get("provider", "ollama")),
                num_ctx=brain_ctx,
            )
    raise ValueError("No brain model configured (role: brain or models.brain_candidates).")


def brain_fallback_id(cfg: dict[str, Any]) -> str | None:
    for item in _locked_items(cfg):
        if item.get("role") == "brain" and item.get("fallback"):
            return str(item["fallback"])
    candidates = cfg.get("models", {}).get("brain_candidates") or []
    if isinstance(candidates, list) and len(candidates) > 1:
        second = candidates[1]
        if isinstance(second, str):
            return second
        if isinstance(second, dict) and second.get("id"):
            return str(second["id"])
    return None

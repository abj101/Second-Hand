from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from loop.os.capture import EncodedFrame

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:12b")


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


def stage_call_metrics(
    call: ModelCall | Mapping[str, Any] | None,
    *,
    num_ctx: int,
) -> dict[str, Any]:
    """Prompt/output tokens, latency, num_ctx, and prompt-token utilization."""
    prompt: int | None
    output: int | None
    latency: float | None
    if isinstance(call, ModelCall):
        prompt, output, latency = call.prompt_tokens, call.output_tokens, call.latency_ms
    elif isinstance(call, Mapping):
        raw_prompt, raw_output, raw_latency = (
            call.get("prompt_tokens"), call.get("output_tokens"), call.get("latency_ms"),
        )
        prompt = raw_prompt if isinstance(raw_prompt, int) else None
        output = raw_output if isinstance(raw_output, int) else None
        latency = float(raw_latency) if isinstance(raw_latency, (int, float)) else None
    else:
        prompt = output = latency = None
    utilization = (prompt / num_ctx) if isinstance(prompt, int) and num_ctx > 0 else None
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "latency_ms": latency,
        "num_ctx": int(num_ctx),
        "utilization": utilization,
    }


_SUMMARY_PREFIX = "Compacted prior accepted state: "


@dataclass
class EpisodeThread:
    """Bounded text-only episode history for one model role.

    Callers commit only after their own schema/intent checks succeed.
    Images stay off the stored turns so later calls send the current frame only.
    """

    recent: int = 3
    max_chars: int = 12_000
    _turns: list[tuple[str, str]] = field(default_factory=list, repr=False)
    _summary: str | None = None
    _committed: int = 0
    compaction_count: int = 0
    _last_sent_chars: int = 0

    def reset(self) -> None:
        self._turns.clear()
        self._summary = None
        self._committed = 0
        self.compaction_count = 0
        self._last_sent_chars = 0

    def messages(self, *, system: str = "", user: str = "") -> list[dict[str, str]]:
        budget = self.max_chars - len(system) - len(user)
        if budget <= 0:
            self._last_sent_chars = 0
            return []
        turns = list(self._turns)
        summary = self._summary
        while True:
            packed = self._history_messages(turns, summary)
            sent = sum(len(row["content"]) for row in packed)
            if sent <= budget:
                self._last_sent_chars = sent
                return packed
            if summary:
                summary = None
                continue
            if len(turns) > 1:
                turns = turns[1:]
                continue
            self._last_sent_chars = 0
            return []

    def commit(self, user: str, assistant: str) -> None:
        self._turns.append((user, assistant))
        self._committed += 1
        self._compact()

    def context_chars(self) -> int:
        return sum(len(message["content"]) for message in self._history_messages(self._turns, self._summary))

    def telemetry(self) -> dict[str, int]:
        return {
            "turn_count": self._committed,
            "context_chars": self.context_chars(),
            "sent_chars": self._last_sent_chars,
            "compaction_count": self.compaction_count,
        }

    def _history_messages(
        self, turns: list[tuple[str, str]], summary: str | None,
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if summary:
            out.append({"role": "user", "content": summary})
        for user, assistant in turns:
            out.append({"role": "user", "content": user})
            out.append({"role": "assistant", "content": assistant})
        return out

    def _compact(self) -> None:
        # ponytail: last-N fold; send-time char budget in messages() if ctx overflows
        if len(self._turns) <= self.recent:
            return
        older, self._turns = self._turns[: -self.recent], self._turns[-self.recent :]
        self._fold(older)

    def _fold(self, older: list[tuple[str, str]]) -> None:
        if not older:
            return
        latest = older[-1][1]
        try:
            state: Any = json.loads(latest)
        except json.JSONDecodeError:
            state = latest
        dropped = len(older)
        if self._summary and self._summary.startswith(_SUMMARY_PREFIX):
            try:
                prev = json.loads(self._summary[len(_SUMMARY_PREFIX) :])
                dropped += int(prev.get("dropped_turns") or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        self._summary = _SUMMARY_PREFIX + json.dumps(
            {"prior_state": state, "dropped_turns": dropped},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.compaction_count += 1


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
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelCall:
        messages, images = _chat_messages(
            system=system, user=user, frames=frames, history=history, list_user=True,
        )
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
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelCall:
        messages, images = _chat_messages(
            system=system, user=user, frames=frames, history=history, list_user=False,
        )
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


DEFAULT_PROPOSAL_MODEL = os.getenv("PROPOSAL_MODEL", "gpt-5.6")


class ProposalGateway:
    """OpenAI-compatible Responses API client for structured image proposals.

    Off the qualified-action hot path. Uses httpx only.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 800,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or os.getenv("PROPOSAL_API_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = model or DEFAULT_PROPOSAL_MODEL
        self.max_tokens = max_tokens

    @classmethod
    def from_env(cls) -> "ProposalGateway | None":
        key = os.getenv("PROPOSAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        return cls(api_key=key)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        schema_name: str = "proposal",
        frames: list[EncodedFrame] | None = None,
        timeout_s: float | None = None,
    ) -> ModelCall:
        import base64

        user_content: list[dict[str, Any]] = [{"type": "input_text", "text": user}]
        for frame in frames or []:
            b64 = base64.b64encode(frame.data).decode("ascii")
            user_content.append(
                {"type": "input_image", "image_url": f"data:{frame.mime};base64,{b64}"}
            )
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": user_content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                }
            },
            "max_output_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        seconds = 60.0 if timeout_s is None else float(timeout_s)
        response = httpx.post(
            f"{self.base_url}/responses",
            json=body,
            headers=headers,
            timeout=httpx.Timeout(seconds, connect=min(seconds, 10.0)),
        )
        _raise_for_status(response)
        payload = response.json() or {}
        text = _responses_text(payload)
        parsed = _parse_json(text)
        return _model_call_from_response(
            text=text,
            parsed=parsed,
            model=self.model,
            latency_ms=(time.perf_counter() - t0) * 1000,
            response=response,
            image_supplied=bool(frames),
        )


def _responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in payload.get("output") or ():
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or ():
            if not isinstance(part, dict):
                continue
            text = part.get("text") or part.get("output_text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def _chat_messages(
    *,
    system: str,
    user: str,
    frames: list[EncodedFrame] | None,
    history: Sequence[Mapping[str, Any]] | None,
    list_user: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
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
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for item in history or ():
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            messages.append({"role": role, "content": content})
        else:
            messages.append({"role": role, "content": str(content or "")})
    messages.append({
        "role": "user",
        "content": user_content if (frames or list_user) else user,
    })
    return messages, images


def _to_ollama(messages: list[dict[str, Any]], images: list[str]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    last_user: dict[str, Any] | None = None
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            item = {"role": role, "content": content}
        else:
            texts: list[str] = []
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(str(part.get("text") or ""))
            item = {"role": role, "content": "\n".join(t for t in texts if t)}
        if role == "user":
            last_user = item
        native.append(item)
    if images and last_user is not None:
        last_user["images"] = images
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
        raise RuntimeError(f"HTTP {response.status_code} for {response.request.url}{suffix}") from exc

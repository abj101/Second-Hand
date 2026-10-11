from __future__ import annotations
import base64, hashlib, io, json, os, tempfile, time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
import httpx
from PIL import Image

MAX_WAIT_S = 900

@dataclass
class Reply:
    text: str; data: dict | None; ms: float; prompt_tokens: int | None; output_tokens: int | None

class ModelUnavailable(RuntimeError):
    def __init__(self, message: str, retries: int = 0):
        super().__init__(message); self.retries = retries

class Ollama:
    def __init__(self, model: str, *, temperature: float = 0.2, max_tokens: int = 4096,
                 num_ctx: int = 16384, timeout_s: float | None = None, log: Callable[[dict], None] | None = None,
                 cache_dir: Path | None = None) -> None:
        self.model = model
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.options = {"temperature": temperature, "num_predict": max_tokens, "num_ctx": num_ctx}
        self.timeout_s = timeout_s or (300.0 if model.endswith(("-cloud", ":cloud")) else 180.0)
        self.log, self.cache_dir, self.calls = log, Path(cache_dir) if cache_dir else None, 0
    def ask(self, system: str, user: str, images: list[Image.Image] = (), schema: dict | None = None,
            *, role: str = "") -> Reply:
        started = time.perf_counter()
        key = self._key(system, user, images, schema) if self.cache_dir else None
        cache = self.cache_dir / f"{key}.json" if key else None
        if cache and cache.is_file():
            try:
                reply = Reply(**json.loads(cache.read_text(encoding="utf-8")))
                self._log(role, (time.perf_counter() - started) * 1000, 0, reply.prompt_tokens, reply.output_tokens, True, True)
                return reply
            except (OSError, ValueError, TypeError): pass
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": [_b64(i) for i in images]}],
            "stream": False, "think": False, "format": schema or "json", "keep_alive": "10m", "options": self.options}
        retries = 0
        try:
            response, retries = self._retry(payload)
            if response.status_code >= 400 and schema is not None:
                payload["format"] = "json"
                response, extra = self._retry(payload); retries += extra
            if response.status_code >= 400:
                raise RuntimeError(f"ollama {response.status_code} ({self.model}): {response.text[:300]}")
            body = response.json(); text = str((body.get("message") or {}).get("content") or "")
            reply = Reply(text, parse_json(text), (time.perf_counter() - started) * 1000,
                          body.get("prompt_eval_count"), body.get("eval_count"))
            self.calls += 1
            if cache and reply.data is not None: self._store(cache, reply)
            self._log(role, reply.ms, retries, reply.prompt_tokens, reply.output_tokens, True, False)
            return reply
        except Exception as error:
            self._log(role, (time.perf_counter() - started) * 1000, getattr(error, "retries", retries), None, None, False, False)
            raise
    def _retry(self, payload: dict) -> tuple[httpx.Response, int]:
        waited = retries = 0
        while True:
            try: response = self._post(payload)
            except (httpx.ConnectError, httpx.TimeoutException) as error: response, error_text = None, str(error)
            else:
                error_text = ""
                if response.status_code != 429 and response.status_code < 500: return response, retries
            if waited >= MAX_WAIT_S: raise ModelUnavailable(f"ollama unavailable after {waited}s: {error_text}", retries)
            after = response.headers.get("Retry-After") if response else None
            backoff = min(5 * 2 ** min(retries, 6), 300)
            try: delay = max(backoff, float(after)) if after else backoff
            except ValueError:
                try: delay = max(backoff, parsedate_to_datetime(after).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError): delay = backoff
            delay = min(max(delay, 0), 300, MAX_WAIT_S - waited); time.sleep(delay)
            waited += delay; retries += 1
    def _post(self, payload: dict) -> httpx.Response:
        return httpx.post(f"{self.base_url}/api/chat", json=payload,
                          timeout=httpx.Timeout(self.timeout_s, connect=10.0))
    def _key(self, system: str, user: str, images: list[Image.Image], schema: dict | None) -> str:
        digest = hashlib.sha256()
        for value in (self.model, system, user, json.dumps(schema, sort_keys=True)):
            raw = value.encode("utf-8"); digest.update(len(raw).to_bytes(8, "big")); digest.update(raw)
        for image in images:
            for raw in (f"{image.mode}:{image.size}".encode(), image.tobytes()):
                digest.update(len(raw).to_bytes(8, "big")); digest.update(raw)
        return digest.hexdigest()
    def _store(self, path: Path, reply: Reply) -> None:
        path.parent.mkdir(parents=True, exist_ok=True); fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream: json.dump(reply.__dict__, stream)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    def _log(self, role, ms, retries, prompt_tokens, output_tokens, ok, cached) -> None:
        if self.log: self.log({"role": role, "model": self.model, "ms": ms, "prompt_tokens": prompt_tokens,
                               "output_tokens": output_tokens, "retries": retries, "ok": ok, "cached": cached})

def _b64(image: Image.Image) -> str:
    buf = io.BytesIO(); fmt = "PNG" if max(image.size) < 1024 else "JPEG"
    image.save(buf, format=fmt, **({} if fmt == "PNG" else {"quality": 90}))
    return base64.b64encode(buf.getvalue()).decode("ascii")

def parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"): text = text.strip("`").removeprefix("json").strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError: pass
    return None

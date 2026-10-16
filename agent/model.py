from __future__ import annotations
import base64, hashlib, io, json, os, sys, tempfile, time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event
import httpx
from PIL import Image, UnidentifiedImageError

MAX_WAIT_S = 900

@dataclass
class Reply:
    text: str; data: dict | None; ms: float; prompt_tokens: int | None; output_tokens: int | None

class ModelUnavailable(RuntimeError):
    def __init__(self, message: str, retries: int = 0):
        super().__init__(message); self.retries = retries

class ModelCancelled(RuntimeError): pass

class Ollama:
    def __init__(self, model: str, *, temperature: float = 0.2, max_tokens: int = 4096,
                 num_ctx: int = 16384, timeout_s: float | None = None, log: Callable[[dict], None] | None = None,
                 cache_dir: Path | None = None, image_dir: Path | None = None, cancel_event: Event | None = None) -> None:
        self.model = model
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.options = {"temperature": temperature, "num_predict": max_tokens, "num_ctx": num_ctx}
        self.timeout_s = timeout_s or (300.0 if model.endswith(("-cloud", ":cloud")) else 180.0)
        self.log, self.cache_dir, self.calls = log, Path(cache_dir) if cache_dir else None, 0
        self.image_dir = Path(image_dir) if image_dir else None
        self.seq, self.log_failed, self._posted = 0, False, 0  # seq: asks started; _posted: _post attempts
        self.cancel_event = cancel_event
    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set(): raise ModelCancelled("model request cancelled")
    def ask(self, system: str, user: str, images: list[Image.Image] = (), schema: dict | None = None,
            *, role: str = "") -> Reply:
        self._check_cancel()
        started, posted = time.perf_counter(), self._posted
        self.seq += 1
        key = self._key(system, user, images, schema)
        cache = self.cache_dir / f"{key}.json" if self.cache_dir else None
        call = {"call": self.seq, "role": role, "key": key, "system": system, "user": user, "schema": schema,
                "params": {**self.options, "format": "json" if schema is None else "schema", "think": False, "keep_alive": "10m"},
                "cache": "none" if cache is None else "miss", "images": images}
        if cache and cache.is_file():
            try: reply = Reply(**json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError): pass
            else:
                call["cache"] = "hit"
                self._log(call, started=started, posted=posted, retries=0, reply=reply, ok=True, cached=True)
                return reply
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
            self._check_cancel()
            self.calls += 1
            if cache and reply.data is not None: self._store(cache, reply)
        except ModelCancelled:
            raise
        except BaseException as error:
            self._log(call, started=started, posted=posted, retries=getattr(error, "retries", retries),
                      reply=None, ok=False, cached=False, error=_error(error))
            raise
        error = None if reply.data is not None else {"type": "MalformedReply", "message": "reply is not a JSON object"}
        self._log(call, started=started, posted=posted, retries=retries, reply=reply, ok=error is None, cached=False, error=error)
        return reply
    def _retry(self, payload: dict) -> tuple[httpx.Response, int]:
        waited = retries = 0
        while True:
            self._check_cancel()
            self._posted += 1
            try: response = self._post(payload)
            except (httpx.ConnectError, httpx.TimeoutException) as error: response, error_text = None, str(error)
            else:
                self._check_cancel()
                error_text = ""
                if response.status_code != 429 and response.status_code < 500: return response, retries
            self._check_cancel()
            if waited >= MAX_WAIT_S: raise ModelUnavailable(f"ollama unavailable after {waited}s: {error_text}", retries)
            after = response.headers.get("Retry-After") if response else None
            backoff = min(5 * 2 ** min(retries, 6), 300)
            try: delay = max(backoff, float(after)) if after else backoff
            except ValueError:
                try: delay = max(backoff, parsedate_to_datetime(after).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError): delay = backoff
            delay = min(max(delay, 0), 300, MAX_WAIT_S - waited)
            if self.cancel_event is None: time.sleep(delay)
            else: self.cancel_event.wait(delay); self._check_cancel()
            waited += delay; retries += 1
    def _post(self, payload: dict) -> httpx.Response:
        self._check_cancel()
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
        self._check_cancel()
        path.parent.mkdir(parents=True, exist_ok=True); fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream: json.dump(reply.__dict__, stream)
            self._check_cancel()
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    def _log(self, call: dict, *, started: float, posted: int, retries: int, reply: Reply | None,
             ok: bool, cached: bool, error: dict | None = None) -> None:
        self._check_cancel()
        if self.log is None: return
        ms = (time.perf_counter() - started) * 1000
        try:
            self.log({"role": call["role"], "model": self.model, "ms": ms,
                      "prompt_tokens": reply.prompt_tokens if reply else None,
                      "output_tokens": reply.output_tokens if reply else None,
                      "retries": retries, "ok": ok, "cached": cached,
                      "call": call["call"], "key": call["key"], "cache": call["cache"],
                      "system": call["system"], "user": call["user"], "schema": call["schema"],
                      "params": call["params"], "response": reply.text if reply else None,
                      "posts": self._posted - posted, "images": [self._image_ref(i) for i in call["images"]],
                      "error": error})
        except OSError as failure:  # the log is evidence, not the call: a lost row must not fail the ask
            if not self.log_failed:
                print(f"ollama: call log write failed ({failure}); run provenance is incomplete", file=sys.stderr, flush=True)
            self.log_failed = True
    def _image_ref(self, image: Image.Image) -> dict:
        self._check_cancel()
        sha = _image_sha(image)
        ref = {"sha": sha, "mode": image.mode, "size": list(image.size)}
        if self.image_dir is not None:
            path = self.image_dir / f"{sha}.png"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True); buf = io.BytesIO(); image.save(buf, format="PNG")
                temp = path.with_name(f"{path.name}.{os.getpid()}.tmp"); temp.write_bytes(buf.getvalue()); os.replace(temp, path)
            ref["path"] = path.name
        return ref

def _b64(image: Image.Image) -> str:
    buf = io.BytesIO(); fmt = "PNG" if max(image.size) < 1024 else "JPEG"
    image.save(buf, format=fmt, **({} if fmt == "PNG" else {"quality": 90}))
    return base64.b64encode(buf.getvalue()).decode("ascii")

def _image_sha(image: Image.Image) -> str:
    digest = hashlib.sha256(f"{image.mode}:{image.size}".encode()); digest.update(image.tobytes())
    return digest.hexdigest()

def load_image_ref(image_dir: Path, ref: dict) -> Image.Image:
    path = Path(image_dir) / ref["path"]
    try:
        with Image.open(path) as opened: image = opened.copy()
    except UnidentifiedImageError as error: raise ValueError(f"{path} is not a readable image") from error
    if _image_sha(image) != ref["sha"]: raise ValueError(f"{path} does not match its recorded sha {ref['sha'][:12]}")
    return image

def _error(error: BaseException) -> dict:
    return {"type": type(error).__name__, "message": str(error)[:300]}

def parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"): text = text.strip("`").removeprefix("json").strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError: pass
    return None

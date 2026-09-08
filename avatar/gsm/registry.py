from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Literal

from avatar.gsm.spec import load_specs, save_specs, spec_path
from avatar.schema import GsmSpec

Outcome = Literal["merged", "spawned", "rejected", "rewritten"]
ALLOWED_KINDS = frozenset({"rule", "rl"})
EMBED_DIM = 64
TETRIS_LABELS = [
    "I tetromino",
    "O tetromino",
    "T tetromino",
    "S tetromino",
    "Z tetromino",
    "J tetromino",
    "L tetromino",
]
TETRIS_RULE_ACTIONS = ["shift_left", "shift_right", "rotate", "hard_drop"]
G2048_LABELS = ["tile"]
G2048_RULE_ACTIONS = ["swipe_down", "swipe_left", "swipe_up", "swipe_right"]


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Stable hashed character n-gram embedding. No extra VRAM."""
    vec = [0.0] * dim
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    padded = f"  {cleaned}  "
    for i in range(max(0, len(padded) - 2)):
        gram = padded[i : i + 3]
        digest = hashlib.md5(gram.encode("utf-8")).hexdigest()
        idx = int(digest, 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


class GsmRegistry:
    """Per-fixture GSM store. Frozen kinds. Merge-not-spawn. Cap 3 rewrites."""

    def __init__(
        self,
        fixture: str,
        *,
        max_rewrites: int = 3,
        soft_cap: int = 8,
        retrieve_threshold: float = 0.85,
        allowed_kinds: frozenset[str] | None = None,
        default_state_source: str = "extracted",
    ) -> None:
        self.fixture = fixture
        self.max_rewrites = max_rewrites
        self.soft_cap = soft_cap
        self.retrieve_threshold = retrieve_threshold
        self.allowed_kinds = allowed_kinds or ALLOWED_KINDS
        self.default_state_source = default_state_source
        self.specs: dict[str, GsmSpec] = {}
        self.log: list[dict[str, Any]] = []
        self.runtime: Any = None
        self.persist_path: Path | None = None

    def get(self, name: str) -> GsmSpec | None:
        if name in self.specs:
            return self.specs[name]
        key = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
        return self.specs.get(key)

    def record_invoke(self, name: str) -> None:
        spec = self.get(name)
        if spec is not None:
            spec.invoke_count += 1

    def prompt_block(self) -> str:
        enabled = [s for s in self.specs.values() if s.enabled]
        if not enabled:
            return ""
        lines = ["Available GSMs (invoke_gsm by name; empty actions):"]
        for spec in enabled:
            lines.append(f"- {spec.name} ({spec.kind}): {spec.recognize}")
        return "\n".join(lines)

    def enable(self, name: str, enabled: bool = True) -> None:
        spec = self.get(name)
        if spec is None:
            raise KeyError(name)
        spec.enabled = enabled

    def propose(self, candidate: GsmSpec) -> Outcome:
        if candidate.kind not in self.allowed_kinds:
            self.log.append(
                {"outcome": "rejected", "reason": "unknown_kind", "name": candidate.name}
            )
            return "rejected"
        match = self._best_match(candidate)
        if match is not None:
            if candidate.action_space:
                match.action_space = list(candidate.action_space)
            if candidate.end:
                match.end = candidate.end
            if candidate.recognize:
                match.recognize = candidate.recognize
                match.embedding = hash_embed(candidate.recognize)
            if candidate.labels:
                match.labels = list(candidate.labels)
            if candidate.reward_ref:
                match.reward_ref = dict(candidate.reward_ref)
            match.traces.extend(candidate.traces)
            self.log.append({"outcome": "merged", "into": match.name, "from": candidate.name})
            return "merged"
        if len(self.specs) >= self.soft_cap:
            self.log.append({"outcome": "rejected", "reason": "soft_cap", "name": candidate.name})
            return "rejected"
        if candidate.embedding is None:
            candidate.embedding = hash_embed(candidate.recognize or candidate.name)
        if not candidate.fixture:
            candidate.fixture = self.fixture
        self.specs[candidate.name] = candidate
        self.log.append({"outcome": "spawned", "name": candidate.name})
        return "spawned"

    def rewrite(self, name: str, candidate: GsmSpec) -> Outcome:
        spec = self.get(name)
        if spec is None:
            return self.propose(candidate)
        if spec.rewrites >= self.max_rewrites:
            self.log.append({"outcome": "rejected", "reason": "rewrite_cap", "name": name})
            return "rejected"
        if candidate.kind and candidate.kind not in self.allowed_kinds:
            self.log.append({"outcome": "rejected", "reason": "unknown_kind", "name": name})
            return "rejected"
        if candidate.recognize:
            spec.recognize = candidate.recognize
            spec.embedding = hash_embed(candidate.recognize)
        if candidate.labels:
            spec.labels = list(candidate.labels)
        if candidate.reward_ref:
            spec.reward_ref = dict(candidate.reward_ref)
        if candidate.action_space:
            spec.action_space = list(candidate.action_space)
        if candidate.end:
            spec.end = candidate.end
        spec.rewrites += 1
        spec.traces.extend(candidate.traces)
        self.log.append({"outcome": "rewritten", "name": name, "rewrites": spec.rewrites})
        return "rewritten"

    def stats(self) -> dict[str, Any]:
        spawned = sum(1 for e in self.log if e["outcome"] == "spawned")
        merged = sum(1 for e in self.log if e["outcome"] == "merged")
        rewritten = sum(1 for e in self.log if e["outcome"] == "rewritten")
        rejected = sum(1 for e in self.log if e["outcome"] == "rejected")
        invokes = sum(s.invoke_count for s in self.specs.values())
        spawned_plus = max(spawned + merged, 1)
        return {
            "gsm_count": len(self.specs),
            "spawned": spawned,
            "merged": merged,
            "rewritten": rewritten,
            "rejected": rejected,
            "invokes": invokes,
            "reuse_rate": merged / spawned_plus,
            "duplicate_spawn_ratio": spawned / spawned_plus,
        }

    def save(self, path: Path | None = None) -> Path:
        dest = path or self.persist_path or spec_path(self.fixture)
        save_specs(
            dest,
            list(self.specs.values()),
            extra={
                "log": self.log,
                "max_rewrites": self.max_rewrites,
                "soft_cap": self.soft_cap,
                "retrieve_threshold": self.retrieve_threshold,
                "fixture": self.fixture,
            },
        )
        return dest

    @classmethod
    def load(cls, path: Path, fixture: str) -> "GsmRegistry":
        data: dict[str, Any] = {}
        if path.exists():
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
        lib = cls(
            fixture,
            max_rewrites=int(data.get("max_rewrites") or 3),
            soft_cap=int(data.get("soft_cap") or 8),
            retrieve_threshold=float(data.get("retrieve_threshold") or 0.85),
        )
        for spec in load_specs(path):
            lib.specs[spec.name] = spec
        lib.log = list(data.get("log") or [])
        return lib

    @classmethod
    def from_config(cls, cfg: dict[str, Any], fixture: str) -> "GsmRegistry":
        gsm_cfg = cfg.get("gsm") or {}
        lib = cls(
            fixture,
            max_rewrites=int(gsm_cfg.get("max_opt_iters") or 3),
            retrieve_threshold=float(gsm_cfg.get("retrieve_threshold") or 0.85),
            default_state_source=str(gsm_cfg.get("default_state_source") or "extracted"),
        )
        raw = ((gsm_cfg.get("fixtures") or {}).get(fixture)) or []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            payload = dict(item)
            payload.setdefault("fixture", fixture)
            payload.setdefault("enabled", True)
            payload.setdefault("state_source", lib.default_state_source)
            if payload.get("kind") == "rule" and not payload.get("labels"):
                if fixture == "tetris":
                    payload["labels"] = list(TETRIS_LABELS)
                elif fixture == "2048":
                    payload["labels"] = list(G2048_LABELS)
            if not payload.get("action_space"):
                if fixture == "tetris":
                    payload["action_space"] = list(TETRIS_RULE_ACTIONS)
                elif fixture == "2048":
                    payload["action_space"] = list(G2048_RULE_ACTIONS)
            if not payload.get("detector_family"):
                payload["detector_family"] = str(gsm_cfg.get("detector") or "pixel-blob")
            payload.setdefault("detector_device", str(gsm_cfg.get("detector_device") or "cpu") or "cpu")
            spec = GsmSpec.model_validate(payload)
            spec.embedding = hash_embed(spec.recognize or spec.name)
            lib.specs[spec.name] = spec
            lib.log.append({"outcome": "spawned", "name": spec.name, "source": "yaml"})
        lib._overlay_persisted()
        lib.persist_path = spec_path(fixture)
        return lib

    def _overlay_persisted(self) -> None:
        """YAML kinds stay frozen. Disk may only retune labels / rewrites / reward / end."""
        path = self.persist_path or spec_path(self.fixture)
        if not path.exists():
            return
        from avatar.gsm.optimize import _usable_labels

        for saved in load_specs(path):
            live = self.get(saved.name)
            if live is None:
                continue
            cleaned = _usable_labels(
                list(saved.labels or []),
                "",
                fixture=self.fixture,
            )
            if cleaned:
                live.labels = cleaned
                live.rewrites = int(saved.rewrites or 0)
            if saved.reward_ref:
                live.reward_ref = dict(saved.reward_ref)
            if saved.end:
                live.end = saved.end

    def _best_match(self, candidate: GsmSpec) -> GsmSpec | None:
        cname = re.sub(r"[^a-z0-9]+", " ", candidate.name.lower()).strip()
        for spec in self.specs.values():
            if re.sub(r"[^a-z0-9]+", " ", spec.name.lower()).strip() == cname:
                return spec
        query = hash_embed(candidate.recognize or candidate.name)
        best: GsmSpec | None = None
        best_score = -1.0
        for spec in self.specs.values():
            emb = spec.embedding or hash_embed(spec.recognize or spec.name)
            score = cosine(query, emb)
            if score > best_score:
                best_score = score
                best = spec
        if best is not None and best_score >= self.retrieve_threshold:
            return best
        return None

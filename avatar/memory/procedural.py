from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from avatar.config import project_root

TOP_N = 8


@dataclass
class ProceduralStat:
    action: str
    n_success: int = 0
    n_fail: int = 0
    n_noop: int = 0
    last_note: str = ""
    last_updated: str = ""
    key: str = ""
    duration_ms: int | None = None
    action_design: str = ""


class ProceduralMemory:
    """Persisted per-fixture action → success/noop/fail counts. No embeddings."""

    def __init__(self, fixture: str, *, root: Path | None = None) -> None:
        self.fixture = fixture
        base = root if root is not None else project_root() / "data" / "memory"
        self.path = Path(base) / fixture / "procedural.json"
        self.stats: dict[str, ProceduralStat] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        rows = data.get("stats") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return
        for item in rows:
            if not isinstance(item, dict) or not item.get("action"):
                continue
            raw_ms = item.get("duration_ms")
            duration_ms = None if raw_ms is None or raw_ms == "" else int(raw_ms)
            stat = ProceduralStat(
                action=str(item["action"]),
                n_success=int(item.get("n_success") or 0),
                n_fail=int(item.get("n_fail") or 0),
                n_noop=int(item.get("n_noop") or 0),
                last_note=str(item.get("last_note") or ""),
                last_updated=str(item.get("last_updated") or ""),
                key=str(item.get("key") or ""),
                duration_ms=duration_ms,
                action_design=str(item.get("action_design") or ""),
            )
            self.stats[stat.action] = stat

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fixture": self.fixture,
            "stats": [asdict(stat) for stat in self.stats.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record(self, action: str, verdict: str, note: str = "") -> ProceduralStat:
        key = action.strip() or "(none)"
        stat = self.stats.get(key) or ProceduralStat(action=key)
        lowered = verdict.lower().strip()
        if lowered == "success":
            stat.n_success += 1
        elif lowered == "noop":
            stat.n_noop += 1
        else:
            stat.n_fail += 1
        if note:
            stat.last_note = note[:240]
        stat.last_updated = datetime.now(timezone.utc).isoformat()
        self.stats[key] = stat
        self.save()
        return stat

    def record_code(
        self,
        name: str,
        *,
        key: str = "",
        duration_ms: int | None = None,
        action_design: str = "",
    ) -> ProceduralStat:
        action = name.strip() or "(none)"
        stat = self.stats.get(action) or ProceduralStat(action=action)
        stat.key = key
        stat.duration_ms = duration_ms
        stat.action_design = action_design
        stat.last_updated = datetime.now(timezone.utc).isoformat()
        self.stats[action] = stat
        self.save()
        return stat

    def retrieve(self, names: list[str]) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            stat = self.stats.get(name)
            if stat is None:
                continue
            bits = [f"- {stat.action}:"]
            if stat.key:
                bits.append(f"key={stat.key}")
            if stat.duration_ms is not None:
                bits.append(f"duration_ms={stat.duration_ms}")
            lines.append(" ".join(bits))
            if stat.action_design:
                lines.append(f"  action_design: {stat.action_design}")
        return "\n".join(lines)

    def top_rows(self, limit: int = TOP_N) -> list[ProceduralStat]:
        def sort_key(stat: ProceduralStat) -> tuple[Any, ...]:
            fails = stat.n_noop + stat.n_fail
            return (-fails, stat.last_updated or "", stat.action)

        return sorted(self.stats.values(), key=sort_key)[:limit]

    def prompt_block(self) -> str:
        rows = self.top_rows()
        if not rows:
            return "Procedural: no action stats yet."
        lines = [
            "Procedural (action → success/noop/fail). rate is a percent; success= is a count.",
        ]
        for stat in rows:
            note = f" — {stat.last_note}" if stat.last_note else ""
            code_bits: list[str] = []
            if stat.key:
                code_bits.append(f"key={stat.key}")
            if stat.duration_ms is not None:
                code_bits.append(f"duration_ms={stat.duration_ms}")
            code = (" " + " ".join(code_bits)) if code_bits else ""
            design = f" — {stat.action_design}" if stat.action_design else ""
            total = stat.n_success + stat.n_noop + stat.n_fail
            rate = int(round(100.0 * stat.n_success / total)) if total else 0
            lines.append(
                f"- {stat.action}: success={stat.n_success} noop={stat.n_noop} "
                f"fail={stat.n_fail} rate={rate}% ({stat.n_success}/{total})"
                f"{note}{code}{design}"
            )
        return "\n".join(lines)

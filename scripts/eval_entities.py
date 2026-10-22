"""Entity-stability metrics over a recorded run (SD: measure before changing the memory)."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from agent.memory import _actor  # ponytail: private helper reused so "actor" means what memory means
from eval_moves import _jsonl


def _changes(rows: list[dict], key: str) -> Counter:
    last, out = {}, Counter()
    for r in rows:
        ident = r.get("id")
        if ident in last and last[ident] != r.get(key): out[ident] += 1
        last[ident] = r.get(key)
    return out


def report(root: Path, start: int = 0, end: int = 10**9) -> None:
    trace = [r for r in _jsonl(root / "trace.jsonl") if r.get("screen") == "world" and r.get("step") is not None]
    truth = {int(r["step"]): (r.get("map"), r.get("x"), r.get("y")) for r in _jsonl(root / "truth.jsonl") if r.get("step") is not None}
    journal = [r for r in _jsonl(root / "memory" / "entities.jsonl") if start <= r.get("last", r.get("step", -1)) <= end]
    steps = [(int(r["step"]), r) for r in trace]
    static = []  # (step, row) for world steps in range whose truth position did not move since the previous world step
    for (ps, _), (s, r) in zip(steps, steps[1:]):
        if start <= s <= end and truth.get(s) is not None and truth.get(s) == truth.get(ps): static.append(r)
    counts = [len(r.get("entities") or []) for r in static]
    print(f"perceived_static mean={mean(counts):.2f} min={min(counts)} max={max(counts)} steps={len(counts)}" if counts else "perceived_static steps=0")

    name_flips, kind_flips = _changes(journal, "name"), _changes(journal, "kind")
    print(f"shown_name_flips total={sum(name_flips.values())} max_per_id={max(name_flips.values(), default=0)} ids={len(name_flips)}")
    print(f"kind_flips total={sum(kind_flips.values())} max_per_id={max(kind_flips.values(), default=0)} ids={len(kind_flips)}")

    latest, gone_at, false_gone = {}, {}, set()
    for r in journal:
        ident = r.get("id")
        if r.get("status") == "gone" and not _actor(r): gone_at[ident] = r.get("place")
        elif r.get("status") == "seen" and ident in gone_at and gone_at.pop(ident) == r.get("place"): false_gone.add(ident)
        latest[ident] = r
    player_ids = sum(1 for ident, r in latest.items() if ident is not None and r.get("kind") == "player")
    print(f"player_kind_ids n={player_ids}")
    gone_end = sum(1 for r in latest.values() if r.get("status") == "gone" and not _actor(r))
    print(f"false_gone ids={len(false_gone)} static_gone_at_end={gone_end}")

    tile_moves, prev = 0, {}
    for r in journal:
        ident = r.get("id")
        if ident in prev and prev[ident] != r.get("tile") and not _actor(r): tile_moves += 1
        prev[ident] = r.get("tile")
    print(f"static_tile_moves total={tile_moves}")

    labels = [(r.get("option") or {}).get("label") for r in static]
    labels = [l for l in labels if isinstance(l, str) and l.startswith(("check ", "talk to "))]
    churn = sum(1 for a, b in zip(labels, labels[1:]) if a != b)
    print(f"option_label_churn changes={churn} thing_steps={len(labels)} (chosen label only; offered list not stored in trace)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", dest="end", type=int, default=10**9)
    a = ap.parse_args(argv)
    report(ROOT / "runs" / a.run, a.start, a.end)
    print("requests=0")


if __name__ == "__main__":
    main()

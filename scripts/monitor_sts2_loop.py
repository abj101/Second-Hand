"""Monitor an sts2 collect run and request cooperative stop if stuck in a loop."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loop.live import read, request_stop, stop_requested
from loop.log.store import latest_run_dir
from loop.config import project_root


TARGET_BIND_STREAK = 4
_NONCANONICAL_ID_RE = re.compile(r"^(object:|mark_|mk_)\d+$")
_UNKNOWN_CHARACTER_N_RE = re.compile(r"^unknown_character_\d+$", re.I)


def action_name(action: dict) -> str:
    name = action.get("name")
    if name:
        return str(name)
    cx, cy = action.get("click_x"), action.get("click_y")
    if cx is not None and cy is not None:
        return f"pt_{float(cx):.2f}_{float(cy):.2f}"
    mark = action.get("mark")
    if mark is not None:
        return f"mk_{mark}"
    return "?"


def load_steps(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def snapshot_populated(row: dict) -> bool:
    snap = row.get("turn_snapshot") or {}
    if not isinstance(snap, dict):
        return False
    cards = snap.get("cards") or []
    targets = snap.get("targets") or []
    return bool(cards) or bool(targets)


def is_noncanonical_id(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    return bool(_NONCANONICAL_ID_RE.match(raw) or _UNKNOWN_CHARACTER_N_RE.match(raw))


def _result_error(row: dict) -> str:
    result = row.get("result") or {}
    if not isinstance(result, dict):
        return ""
    return str(result.get("error") or "")


def hygiene_counts(rows: list[dict]) -> dict[str, int]:
    noncanonical = 0
    target_not_found = 0
    source_unconfirmed = 0
    forced_rescan = 0
    for row in rows:
        snap = row.get("turn_snapshot") or {}
        if isinstance(snap, dict):
            for card in snap.get("cards") or []:
                if isinstance(card, dict) and is_noncanonical_id(str(card.get("card_id") or "")):
                    noncanonical += 1
            for target in snap.get("targets") or []:
                if isinstance(target, dict) and is_noncanonical_id(str(target.get("target_id") or "")):
                    noncanonical += 1
        err = _result_error(row)
        if "target-not-found" in err:
            target_not_found += 1
        if "source-unconfirmed" in err:
            source_unconfirmed += 1
        if str(row.get("perception_source") or "") == "forced_rescan":
            forced_rescan += 1
    return {
        "noncanonical_ids": noncanonical,
        "target_not_found": target_not_found,
        "source_unconfirmed": source_unconfirmed,
        "forced_rescan": forced_rescan,
        "steps": len(rows),
    }


def hygiene_line(rows: list[dict]) -> str:
    counts = hygiene_counts(rows)
    return (
        f"noncanonical_ids={counts['noncanonical_ids']} "
        f"target_not_found={counts['target_not_found']} "
        f"source_unconfirmed={counts['source_unconfirmed']} "
        f"forced_rescan={counts['forced_rescan']}/{counts['steps']}"
    )


def consecutive_target_not_found(rows: list[dict]) -> int:
    streak = 0
    for row in reversed(rows):
        if "target-not-found" in _result_error(row):
            streak += 1
            continue
        break
    return streak


def stuck_reason(rows: list[dict], *, window: int, min_no_change: int) -> str | None:
    if len(rows) < window:
        return None
    tail = rows[-window:]

    def identity(row: dict) -> tuple:
        metrics = row.get("metrics") or {}
        return (
            str(metrics.get("screen") or "").strip().lower(),
            metrics.get("hp"),
            metrics.get("floor"),
        )

    ids = [identity(r) for r in tail]
    if len(set(ids)) == 1:
        screen, hp, floor = ids[0]
        populated = any(snapshot_populated(r) for r in tail)
        if populated and screen not in {"tutorial", "title"}:
            return f"HUD frozen screen={screen or '?'} hp={hp} floor={floor} for {window} steps"

    if all(not bool((r.get("result") or {}).get("pixel_changed")) for r in tail):
        return f"last {window} steps had no pixel change"
    names = [action_name(r.get("action") or {}) for r in tail]
    if len(set(names)) <= 2 and window >= 10:
        return f"only {len(set(names))} unique actions in last {window} steps: {sorted(set(names))}"
    if window >= 12:
        cycle = names[-6:]
        prev = names[-12:-6]
        if cycle == prev:
            return f"repeating 6-step action cycle: {cycle}"
    if len(rows) >= min_no_change:
        tail2 = rows[-min_no_change:]
        if all(not bool((r.get("result") or {}).get("pixel_changed")) for r in tail2):
            return f"last {min_no_change} steps had no pixel change"
    return None


def _print_step(rows: list[dict]) -> None:
    last = rows[-1] if rows else {}
    print(
        f"step={len(rows)} terminal={last.get('terminal') if rows else '?'} "
        f"action={action_name((last.get('action') or {})) if rows else '?'}",
        flush=True,
    )


def _poll_report(rows: list[dict], *, window: int, min_no_change: int) -> tuple[str | None, bool]:
    print(hygiene_line(rows), flush=True)
    bind_loop = consecutive_target_not_found(rows) >= TARGET_BIND_STREAK
    reason = stuck_reason(rows, window=window, min_no_change=min_no_change)
    return reason, bind_loop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", "--run-dir", dest="run_dir", type=Path, default=None)
    parser.add_argument("--once", action="store_true", help="one poll against a frozen run, then exit")
    parser.add_argument("--poll-s", type=float, default=60.0)
    parser.add_argument("--window", type=int, default=12)
    parser.add_argument("--min-no-change", type=int, default=12)
    parser.add_argument("--stuck-checks", type=int, default=3, help="consecutive stuck polls before stop")
    parser.add_argument("--idle-s", type=float, default=300.0, help="stop if step count unchanged this long")
    args = parser.parse_args(argv)

    root = project_root()
    run_dir = args.run_dir or latest_run_dir("sts2", root)
    if run_dir is None:
        print("no sts2 run dir found", file=sys.stderr)
        return 2
    steps_path = run_dir / "turns.jsonl"
    print(f"monitoring {run_dir}", flush=True)

    if args.once:
        rows = load_steps(steps_path)
        _print_step(rows)
        reason, bind_loop = _poll_report(rows, window=args.window, min_no_change=args.min_no_change)
        if bind_loop:
            print("STUCK: target bind loop", flush=True)
            return 1
        if reason:
            print(f"stuck signal 1/{args.stuck_checks}: {reason}", flush=True)
        return 0

    stuck_hits = 0
    last_step = -1
    last_progress = time.monotonic()

    while True:
        live = read(root)
        live_run = live.get("run_dir")
        if live_run and str(run_dir) not in str(live_run):
            print(f"waiting: live run is {live_run}", flush=True)
            time.sleep(args.poll_s)
            continue

        phase = live.get("phase")
        if phase in {"done", "stopped", "error", "idle"} and stop_requested(root):
            print(f"run ended phase={phase}", flush=True)
            return 0

        rows = load_steps(steps_path)
        step_n = len(rows)
        if step_n > last_step:
            last_step = step_n
            last_progress = time.monotonic()
            _print_step(rows)

        reason, bind_loop = _poll_report(rows, window=args.window, min_no_change=args.min_no_change)
        if bind_loop:
            print("STUCK: target bind loop", flush=True)
            request_stop(root)
            return 1

        idle_s = time.monotonic() - last_progress
        if reason:
            stuck_hits += 1
            print(f"stuck signal {stuck_hits}/{args.stuck_checks}: {reason}", flush=True)
        else:
            stuck_hits = 0

        if stuck_hits >= args.stuck_checks:
            print(f"requesting stop: {reason}", flush=True)
            request_stop(root)
            return 0
        if idle_s >= args.idle_s and step_n > 0:
            print(f"requesting stop: no new steps for {idle_s:.0f}s", flush=True)
            request_stop(root)
            return 0
        if phase in {"done", "stopped", "error"}:
            print(f"run ended phase={phase}", flush=True)
            return 0

        time.sleep(args.poll_s)


if __name__ == "__main__":
    raise SystemExit(main())

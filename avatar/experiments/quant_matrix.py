"""VLM comparison matrix on OS-harness 2048 (Qwen3-VL baseline vs peers)."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from avatar.config import load_config, project_root
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.experiments.common import parse_act, safe_call
from avatar.fixtures.browser_window import launch_app_window
from avatar.fixtures.server import serve_2048
from avatar.harness.cycle import AgentCycle
from avatar.harness.executor import FocusLost, OsController
from avatar.harness.primitives import key_tap
from avatar.harness.profile import profile_from_config, settings_from_frame
from avatar.harness.window import close_windows, wait_for_window
from avatar.models import make_gateway
from avatar.schema import TurnLog

SWIPE_NAMES = frozenset({"swipe_up", "swipe_down", "swipe_left", "swipe_right"})

QUANT_SYSTEM = """You are a vision-language game QA agent. You see only pixels.
You never receive DOM, emulator RAM, or game APIs.
Reply with a single JSON object. No markdown fences. No commentary outside JSON.
Do not invent actions named wait or observe.

JSON schema:
{
  "board": [[number or null, number or null, number or null, number or null], ...],
  "score": number or null,
  "goal": string,
  "actions": [{"name": string, "details": string}],
  "reasoning": string
}
For 2048, board is exactly 4 rows of 4 cells, top-to-bottom, left-to-right.
board[0] is the TOP row. board[3] is the BOTTOM row. board[0][0] is TOP-LEFT.
Empty cells are null. Do not skip empty rows. Read every cell from the pixels.
score is the number in the Score box above the grid (not Best).
Pick exactly one action name from: swipe_up, swipe_down, swipe_left, swipe_right.
Play to merge equal tiles or increase empty cells. Prefer keeping the highest tile in a corner.
"""

QUANT_USER = (
    "Fixture: 2048. Task: read the 4x4 board from the pixels, then execute one legal swipe "
    "that merges tiles or increases empty cells. Prefer keeping the high tile in a corner."
)


@dataclass(frozen=True)
class QuantCell:
    size: str
    quant: str
    ollama_id: str
    source: Literal["official", "bartowski", "ollama"]
    hf_repo: str | None = None
    gguf_candidates: tuple[str, ...] = ()
    mmproj_candidates: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.size}-{self.quant}"


CELLS: tuple[QuantCell, ...] = (
    QuantCell("qwen3.5", "4b-instruct-q4_K_M", "qwen3.5:4b-instruct-q4_K_M", "official"),
    QuantCell("qwen3.5", "4b-q4_K_M", "qwen3.5:4b-q4_K_M", "official"),
    QuantCell("qwen3.5", "4b-q8_0", "qwen3.5:4b-q8_0", "official"),
)


def gguf_cache_dir() -> Path:
    path = project_root() / ".cache" / "vlm-gguf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def norm_cell(v: object) -> int:
    if v is None or v == 0:
        return 0
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def score_board(pred: list | None, oracle_grid: list) -> int:
    if not pred or not isinstance(pred, list) or len(pred) != 4:
        return 0
    correct = 0
    for r in range(4):
        row = pred[r] if r < len(pred) else []
        if not isinstance(row, list):
            row = []
        for c in range(4):
            pv = norm_cell(row[c] if c < len(row) else None)
            ov = norm_cell(oracle_grid[r][c] if r < len(oracle_grid) and c < len(oracle_grid[r]) else 0)
            if pv == ov:
                correct += 1
    return correct


def max_tile(grid: list | None) -> int:
    if not grid:
        return 0
    best = 0
    for row in grid:
        if not isinstance(row, list):
            continue
        for cell in row:
            best = max(best, norm_cell(cell))
    return best


def grid_tuple(grid: object) -> tuple[tuple[int, ...], ...]:
    if not isinstance(grid, list):
        return ()
    rows: list[tuple[int, ...]] = []
    for row in grid:
        if not isinstance(row, list):
            rows.append(())
            continue
        rows.append(tuple(norm_cell(c) for c in row))
    return tuple(rows)


def fetch_oracle(base_url: str, timeout: float = 2.0) -> dict[str, Any]:
    response = httpx.get(base_url.rstrip("/") + "/__oracle", timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def read_oracle(base_url: str, attempts: int = 10) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(attempts):
        try:
            last = fetch_oracle(base_url)
        except Exception:  # noqa: BLE001
            last = {}
        if last.get("grid"):
            return last
        time.sleep(0.08)
    return last


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


def _summarize_cell(turns: list[dict[str, Any]], *, wall_s: float, status: str) -> dict[str, Any]:
    play = [t for t in turns if not t.get("warmup")]
    n = len(play)
    lats = [float(t["latency_ms"]) for t in play if t.get("latency_ms")]
    tiles = [int(t.get("tiles") or 0) for t in play]
    last = play[-1] if play else {}
    return {
        "status": status,
        "n": n,
        "tiles_avg": (sum(tiles) / n) if n else 0.0,
        "legal_move_pct": (100.0 * sum(1 for t in play if t.get("moved")) / n) if n else 0.0,
        "parse_ok_pct": (100.0 * sum(1 for t in play if t.get("parse_ok")) / n) if n else 0.0,
        "latency_mean_ms": statistics.fmean(lats) if lats else None,
        "latency_p50_ms": _pct(lats, 50),
        "latency_p95_ms": _pct(lats, 95),
        "wall_s": wall_s,
        "score_start": play[0].get("score_before") if play else None,
        "score_end": last.get("score_after"),
        "max_tile": max((int(t.get("max_tile_after") or 0) for t in play), default=0),
        "game_over": bool(last.get("over")),
        "stuck": sum(1 for t in play if t.get("stuck")),
    }


def _focus_call(controller: OsController, fn, retries: int = 5):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            controller.focus()
            if attempt:
                time.sleep(0.35)
            return fn()
        except FocusLost as exc:
            last = exc
            time.sleep(0.4)
    if last is not None:
        raise last
    raise RuntimeError("focus retry exhausted")


def _restart_game(controller: OsController, base_url: str) -> dict[str, Any]:
    _focus_call(controller, lambda: controller.execute((key_tap("KeyR"),)))
    time.sleep(0.45)
    return read_oracle(base_url)


def _run_cell(
    *,
    cell: QuantCell,
    cfg: dict[str, Any],
    controller: OsController,
    cycle: AgentCycle,
    logger: JsonlLogger,
    run_dir: Path,
    base_url: str,
    turns: int,
    skip_models: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "size": cell.size,
        "quant": cell.quant,
        "model": cell.ollama_id,
        "source": cell.source,
        "label": cell.label,
    }
    print(f"\n=== {cell.label}  {cell.ollama_id} ({cell.source}) ===", flush=True)
    t0 = time.perf_counter()
    gateway = None
    turn_rows: list[dict[str, Any]] = []
    status = "ok"
    try:
        oracle = _restart_game(controller, base_url)
        if skip_models:
            row.update(_summarize_cell([], wall_s=time.perf_counter() - t0, status="skipped"))
            row["note"] = "skip-models"
            return row

        gateway = make_gateway(cfg, cell.ollama_id, "ollama")
        cycle.gateway = gateway
        controller.focus()
        print("  layout…", flush=True)
        plan = controller.capture.diagnose_and_apply(
            scout_dir=run_dir / "scout",
            scout_name=cell.label,
        )
        row["scout_image"] = f"scout/{cell.label}.jpg"
        row["scout_plan"] = f"scout/{cell.label}.json"
        crop = getattr(plan, "crop", None)
        print(
            f"  layout source={getattr(plan, 'source', '?')} "
            f"side={getattr(plan, 'longest_side', '?')} crop={crop}",
            flush=True,
        )
        print("  warmup…", flush=True)
        frame, _skipped = cycle.capture()
        warmup = safe_call(gateway, system=QUANT_SYSTEM, user=QUANT_USER, frames=[frame])
        warmup_ms = None if warmup is None else warmup.latency_ms
        warmup_err = isinstance(getattr(warmup, "parsed", None), dict) and "error" in (warmup.parsed or {})
        if warmup is None or warmup_err:
            status = f"fail:{warmup.parsed if warmup else 'no-call'}"
            print(f"  warmup failed: {status}", flush=True)
            row.update(_summarize_cell([], wall_s=time.perf_counter() - t0, status=status))
            return row
        print(f"  warmup latency={warmup_ms:.0f}ms", flush=True)

        last_swipe: str | None = None
        last_moved = True
        for turn in range(1, turns + 1):
            controller.focus()
            before = read_oracle(base_url)
            if before.get("over"):
                print("  game over — stopping cell", flush=True)
                break
            path = run_dir / "frames" / f"{cell.size}_{cell.quant}_t{turn:02d}.jpg"
            print(f"  turn {turn}/{turns}: asking {cell.ollama_id}…", flush=True)
            call = None
            try:
                frame, skipped = cycle.capture()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(frame.data)
                call = safe_call(gateway, system=QUANT_SYSTEM, user=QUANT_USER, frames=[frame])
                parsed = None if call is None else call.parsed
                plan = parse_act(parsed)
                if plan.actions:
                    plan.actions = plan.actions[:1]
                result = _focus_call(controller, lambda: cycle.act(plan))
                executed = result.executed
                latency_ms = None if call is None else call.latency_ms
            except Exception as exc:  # noqa: BLE001
                executed = []
                parsed = {"error": str(exc)}
                plan = parse_act(parsed)
                latency_ms = None
                skipped = False
                print(f"      exec_error={exc}", flush=True)
                frame = None  # type: ignore[assignment]

            settle = controller.capture.wait_settled()
            after = read_oracle(base_url)
            pred_board = parsed.get("board") if isinstance(parsed, dict) else None
            tiles = score_board(pred_board if isinstance(pred_board, list) else None, before.get("grid") or [])
            swipe = executed[0] if executed else None
            parse_ok = swipe in SWIPE_NAMES
            moved = grid_tuple(before.get("grid")) != grid_tuple(after.get("grid")) and bool(after.get("grid"))
            stuck = bool(swipe) and swipe == last_swipe and not moved and not last_moved
            last_swipe = swipe
            last_moved = moved
            score_before = before.get("score")
            score_after = after.get("score")
            rec = {
                "turn": turn,
                "warmup": False,
                "executed": executed,
                "parse_ok": parse_ok,
                "moved": moved,
                "stuck": stuck,
                "tiles": tiles,
                "latency_ms": latency_ms,
                "score_before": score_before,
                "score_after": score_after,
                "max_tile_after": max_tile(after.get("grid") if isinstance(after.get("grid"), list) else None),
                "over": bool(after.get("over")),
                "skipped_frame": skipped,
                "settle_ms": settle.elapsed_ms,
                "settle_changed": settle.changed,
            }
            turn_rows.append(rec)
            latency_s = "n/a" if latency_ms is None else f"{latency_ms:.0f}ms"
            print(
                f"      -> {executed or '(none)'} tiles={tiles}/16 moved={moved} [{latency_s}]",
                flush=True,
            )
            extra = {
                "attempt": turn,
                "task": "play_one_legal_swipe",
                "oracle_before": before,
                "oracle_after": after,
                "tiles": tiles,
                "moved": moved,
                "parse_ok": parse_ok,
                "quant": cell.quant,
                "size": cell.size,
                "source": cell.source,
                "settle_ms": settle.elapsed_ms,
                "settle_changed": settle.changed,
            }
            if frame is not None:
                logger.write(
                    TurnLog(
                        screenshot_ref=str(path),
                        capture_settings=settings_from_frame(
                            frame,
                            controller.capture.profile,
                            settle_ms=settle.elapsed_ms,
                        ),
                        model=cell.ollama_id,
                        harness_level="L1",
                        fixture="2048",
                        paused=False,
                        prompt_mode="pixels",
                        reasoning=plan.reasoning,
                        action=plan.model_dump() if hasattr(plan, "model_dump") else {},
                        result=extra,
                        latency_ms=latency_ms,
                        cost_usd=None if call is None else call.cost_usd,
                        stage="quant",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        status = f"fail:{exc}"
        print(f"  cell failed: {exc}", flush=True)
    finally:
        if gateway is not None:
            gateway.unload()
            cycle.gateway = None

    wall_s = time.perf_counter() - t0
    row.update(_summarize_cell(turn_rows, wall_s=wall_s, status=status))
    print(
        f"  done {cell.label}: tiles={row['tiles_avg']:.1f}/16 "
        f"legal={row['legal_move_pct']:.0f}% lat={row['latency_mean_ms'] or 0:.0f}ms "
        f"wall={row['wall_s']:.1f}s",
        flush=True,
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VLM comparison matrix on 2048")
    parser.add_argument("--window", default=None, help="browser window title substring")
    parser.add_argument("--launch", action="store_true", help="open 2048 in an Edge/Chrome app window")
    parser.add_argument("--headed", action="store_true", help="alias for --launch")
    parser.add_argument("--turns", type=int, default=None, metavar="N")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument(
        "--cell",
        action="append",
        default=None,
        metavar="LABEL",
        help="Only run this cell label (e.g. 2b-q8_0). Repeatable.",
    )
    args = parser.parse_args(argv)
    if args.headed:
        args.launch = True

    cfg = load_config()
    turns = args.turns or int(cfg.get("n_micro", 20))
    if turns < 1:
        parser.error("--turns must be >= 1")
    fixture_cfg = (cfg.get("fixtures") or {}).get("2048") or {}
    window_title = args.window or fixture_cfg.get("window_title") or "2048"
    run_dir = new_run_dir("quant", "2048")
    logger = JsonlLogger(run_dir)

    cells = list(CELLS)
    if args.cell:
        wanted = {c.lower() for c in args.cell}
        cells = [c for c in CELLS if c.label.lower() in wanted or c.ollama_id in wanted]
        if not cells:
            parser.error(f"No cells matched {args.cell}. Labels: {[c.label for c in CELLS]}")

    server, url = serve_2048()
    print(f"2048 served at {url}", flush=True)
    print(f"Focus a window titled like {window_title!r} (or pass --launch).", flush=True)
    if args.launch:
        closed = close_windows(window_title)
        if closed:
            print(f"Closed {closed} leftover {window_title!r} window(s).", flush=True)
        launch_app_window(url)
        time.sleep(1.0)
    try:
        wait_for_window(window_title, timeout_s=25)
    except RuntimeError:
        server.shutdown()
        print(
            f"No window matching {window_title!r}. Open {url} in a browser, "
            "click the game, then re-run (or pass --launch).",
            flush=True,
        )
        return 2

    print(f"Quant matrix run dir: {run_dir}", flush=True)
    print(f"Cells: {[c.label for c in cells]}  turns={turns}", flush=True)
    matrix: list[dict[str, Any]] = []
    try:
        with OsController(
            window_title,
            enable_gamepad=False,
            skip_unchanged=False,
            profile=profile_from_config(cfg, "2048"),
        ) as controller:
            controller.focus()
            cycle = AgentCycle(
                table_name="swipe",
                controller=controller,
                fixture="2048",
                logger=None,
                gateway=None,
                prompt_mode="pixels",
                manual="",
                harness_level="L1",
                paused=False,
                max_actions=1,
            )
            for cell in cells:
                matrix.append(
                    _run_cell(
                        cell=cell,
                        cfg=cfg,
                        controller=controller,
                        cycle=cycle,
                        logger=logger,
                        run_dir=run_dir,
                        base_url=url,
                        turns=turns,
                        skip_models=args.skip_models,
                    )
                )
    finally:
        server.shutdown()

    out = run_dir / "matrix.json"
    payload = {"turns": turns, "cells": [asdict(c) for c in cells], "results": matrix}
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Quant matrix written to {out}", flush=True)
    for row in matrix:
        lat = row.get("latency_mean_ms")
        lat_s = "n/a" if lat is None else f"{lat:.0f}ms"
        print(
            f"  {row.get('label')}: tiles={row.get('tiles_avg', 0):.1f}/16 "
            f"legal={row.get('legal_move_pct', 0):.0f}% lat={lat_s} "
            f"wall={row.get('wall_s', 0):.1f}s status={row.get('status')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

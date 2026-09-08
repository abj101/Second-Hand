from __future__ import annotations

import argparse
import json
import time
from typing import Any

from avatar.config import load_config
from avatar.constructor.skills import default_2048_skills
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.eval.scoring import gsm_lift_table, gsm_rows_from_turns, harness_lift_table, write_markdown_table
from avatar.experiments.common import add_model_flags, parse_act, safe_model_tag, selected_models
from avatar.fixtures.browser_window import launch_app_window
from avatar.fixtures.server import latest_oracle, serve_2048
from avatar.gsm.registry import GsmRegistry
from avatar.gsm.runtime import attach_live_runtime
from avatar.harness.cycle import AgentCycle
from avatar.harness.executor import FocusLost, OsController
from avatar.harness.primitives import MousePrimitive
from avatar.harness.profile import profile_from_config
from avatar.harness.window import close_windows, list_windows, wait_for_window
from avatar.memory import AgentMemory
from avatar.models import make_brain, make_eyes, make_gateway
from avatar.prompts.library import load_manual

PLAY_TASK = (
    "Play 2048. Keep the highest tile in a corner (prefer bottom-left). "
    "This turn: pick one legal swipe from the See board, or invoke_skill "
    "keep_left / corner_down_left. "
    "Never pick a banned swipe. If the last swipe was a noop, pick a different direction. "
    "duration_ms must be null."
)
GSM_TASK = (
    "If See says ui or game over: do not invoke_gsm; use named table actions if needed. "
    "When tiles can slide in gameplay: set invoke_gsm to choose_swipe and use an empty "
    "actions list. The module picks one legal swipe and respects banned names. "
    "Do not emit named swipes in gameplay. Do not invoke_skill. duration_ms must be null."
)


def _grid_tuple(grid: object) -> tuple[tuple[int, ...], ...]:
    if not isinstance(grid, list):
        return ()
    rows: list[tuple[int, ...]] = []
    for row in grid:
        if not isinstance(row, list):
            rows.append(())
            continue
        cells: list[int] = []
        for cell in row:
            try:
                cells.append(0 if cell is None else int(cell))
            except (TypeError, ValueError):
                cells.append(0)
        rows.append(tuple(cells))
    return tuple(rows)


def _max_tile(grid: object) -> int:
    best = 0
    for row in _grid_tuple(grid):
        if row:
            best = max(best, max(row))
    return best


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


def _boot_2048(controller: OsController, *, scout_dir: Any | None = None) -> None:
    """Click the grid so arrow keys reach the page, then start a fresh game."""
    for _ in range(10):
        controller.focus()
        if controller.capture.is_foreground():
            break
        time.sleep(0.35)
    time.sleep(0.2)
    _focus_call(controller, lambda: controller.execute([MousePrimitive(op="click", x=0.50, y=0.52)]))
    time.sleep(0.15)
    _focus_call(controller, lambda: controller.execute_named("swipe", "restart"))
    time.sleep(0.45)
    cap = controller.capture
    cap.skip_streak = 0
    cap.refresh()
    cap.diagnose_and_apply(scout_dir=scout_dir)
    cap.select_preset("gameplay")
    cap.profile.scout_every = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 2048: single VLM planner may invoke_gsm choose_swipe",
    )
    parser.add_argument("--window", default=None, help="browser window title substring")
    parser.add_argument("--launch", action="store_true", help="open 2048 in an Edge/Chrome app window")
    parser.add_argument("--headed", action="store_true", help="alias for --launch")
    parser.add_argument("--level", choices=["L1", "L2"], default="L2")
    parser.add_argument("--n", type=int, default=None, help="legacy alias for --turns")
    parser.add_argument("--turns", type=int, default=None, help="play turns (default 40)")
    parser.add_argument("--manual", action="store_true", help="inject 2048 how-to (default)")
    parser.add_argument("--pixels", action="store_true", help="pixels-only: no how-to text")
    parser.add_argument("--gsm", action="store_true", help="enable choose_swipe GSM (default)")
    parser.add_argument("--no-gsm", action="store_true", help="planner-only; empty GSM registry")
    parser.add_argument(
        "--dual-brain",
        action="store_true",
        help="legacy Phase 1 L2: vision See + separate text brain",
    )
    parser.add_argument(
        "--single-vlm",
        action="store_true",
        help="Phase 2 (default): one VLM for See and plan; no text brain",
    )
    parser.add_argument(
        "--force-gsm",
        action="store_true",
        help="skip the planner and always invoke choose_swipe",
    )
    parser.add_argument(
        "--detector",
        default=None,
        choices=["pixel-blob", "grounding-dino", "open-vocab"],
        help="Override spec.detector_family before attaching the live GSM runtime.",
    )
    parser.add_argument(
        "--detector-device",
        default=None,
        choices=["cpu", "cuda"],
        help="cpu coexists with Ollama (default). cuda unloads the VLM first.",
    )
    parser.add_argument(
        "--state-source",
        default=None,
        choices=["extracted", "pixels"],
        help="G5 ablation: observation source (does not change prompt_mode).",
    )
    add_model_flags(parser)
    args = parser.parse_args(argv)
    if args.headed:
        args.launch = True
    if args.turns is None:
        args.turns = args.n or 40
    if args.turns < 1:
        parser.error("--turns must be >= 1")
    use_gsm = not args.no_gsm
    if args.gsm:
        use_gsm = True

    cfg = load_config()
    fixture_cfg = (cfg.get("fixtures") or {}).get("2048") or {}
    window_title = args.window or fixture_cfg.get("window_title") or "2048"
    run_dir = new_run_dir("gsm" if use_gsm else "act", "2048")
    logger = JsonlLogger(run_dir)
    prompt_mode = "pixels" if args.pixels else "manual"
    manual = load_manual("2048") if prompt_mode == "manual" else ""
    score_rows: list[dict[str, Any]] = []

    server, url = serve_2048()
    print(f"2048 served at {url}", flush=True)
    print(f"Focus a window titled like {window_title!r} (or pass --launch).", flush=True)
    existing: set[int] = set()
    if args.launch:
        closed = close_windows(window_title)
        if closed:
            print(f"Closed {closed} leftover {window_title!r} window(s).", flush=True)
        existing = {w.hwnd for w in list_windows(window_title)}
        launch_app_window(url)
        time.sleep(1.2)
    try:
        target = wait_for_window(window_title, timeout_s=25, exclude_hwnds=existing or None)
    except RuntimeError:
        server.shutdown()
        print(
            f"No window matching {window_title!r}. Open {url} in a browser, "
            "click the game, then re-run (or pass --launch).",
            flush=True,
        )
        return 2

    gsms = GsmRegistry.from_config(cfg, "2048") if use_gsm else None
    if gsms is not None and args.state_source:
        for spec in gsms.specs.values():
            spec.state_source = args.state_source  # type: ignore[assignment]
    if gsms is not None and args.detector:
        for spec in gsms.specs.values():
            spec.detector_family = args.detector
    detector_device = getattr(args, "detector_device", None) or str(
        (cfg.get("gsm") or {}).get("detector_device") or "cpu"
    ) or "cpu"
    if gsms is not None:
        for spec in gsms.specs.values():
            spec.detector_device = detector_device
        if detector_device == "cuda":
            from avatar.models import unload_locked_eyes

            print("detector_device=cuda: unloading locked VLM before DINO load.", flush=True)
            unload_locked_eyes(cfg)
        attach_live_runtime(gsms)
    skills = default_2048_skills()
    gsm_kind = None
    detector_family = None
    gsm_tag = "off"
    if gsms is not None and gsms.specs:
        spec0 = next(iter(gsms.specs.values()))
        gsm_kind = spec0.kind
        det = getattr(getattr(gsms, "runtime", None), "detector", None)
        detector_family = getattr(det, "family", None) or spec0.detector_family or None
        gsm_tag = gsm_kind or "rule"
    state_source = args.state_source or (
        next(iter(gsms.specs.values())).state_source
        if gsms is not None and gsms.specs
        else ((cfg.get("gsm") or {}).get("default_state_source"))
    )
    if not use_gsm:
        state_source = "n/a"

    harness_level = "L1"
    if args.level == "L2":
        harness_level = "L2" if args.dual_brain and not args.single_vlm else "GSM"
    print(f"Act run dir: {run_dir}", flush=True)
    print(
        f"Level: {harness_level}. turns={args.turns} prompt={prompt_mode} "
        f"gsm={gsm_tag} detector={detector_family or 'n/a'} state={state_source} skills=on "
        f"planner={'dual-brain' if args.dual_brain and not args.single_vlm else 'single-vlm'}",
        flush=True,
    )

    try:
        with OsController(
            window_title,
            enable_gamepad=False,
            profile=profile_from_config(cfg, "2048"),
            hwnd=target.hwnd,
        ) as controller:
            _boot_2048(controller, scout_dir=run_dir / "scout")
            play_task = GSM_TASK if use_gsm and gsms is not None and gsms.specs else PLAY_TASK
            if args.level == "L2":
                _run_l2(
                    args, cfg, controller, logger, run_dir, prompt_mode, manual,
                    score_rows, gsms, skills, gsm_tag, gsm_kind, detector_family,
                    harness_level=harness_level, play_task=play_task, state_source=state_source,
                )
            else:
                _run_l1(
                    args, cfg, controller, logger, run_dir, prompt_mode, manual,
                    score_rows, gsms, skills, gsm_tag, gsm_kind, detector_family,
                    play_task=play_task, state_source=state_source,
                )
    finally:
        server.shutdown()

    table = harness_lift_table(score_rows)
    write_markdown_table(table, run_dir / "harness_lift.md")
    write_markdown_table(gsm_lift_table(gsm_rows_from_turns(logger.read())), run_dir / "gsm_lift.md")
    last = latest_oracle()
    n_rows = len(score_rows)
    oracle_rate = (
        sum(int(bool(row.get("success"))) for row in score_rows) / n_rows if n_rows else 0.0
    )
    pixel_rate = (
        sum(int(bool(row.get("pixel_success"))) for row in score_rows) / n_rows if n_rows else 0.0
    )
    summary = {
        "fixture": "2048",
        "harness_level": harness_level,
        "gsm": gsm_tag,
        "gsm_kind": gsm_kind,
        "state_source": state_source,
        "detector_family": detector_family,
        "prompt_mode": prompt_mode,
        "turns": n_rows,
        "success_rate": oracle_rate,
        "oracle_success_rate": oracle_rate,
        "pixel_success_rate": pixel_rate,
        "score": last.get("score"),
        "max_tile": _max_tile(last.get("grid")),
        "over": bool(last.get("over")),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"2048 play done: score={summary['score']} max_tile={summary['max_tile']} "
        f"success={summary['success_rate']:.2f} oracle={summary['oracle_success_rate']:.2f} "
        f"pixel={summary['pixel_success_rate']:.2f} over={summary['over']}",
        flush=True,
    )
    print(f"Act experiment run written to {run_dir}", flush=True)
    return 0


def _run_l1(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    controller: OsController,
    logger: JsonlLogger,
    run_dir: Any,
    prompt_mode: str,
    manual: str,
    score_rows: list[dict[str, Any]],
    gsms: Any,
    skills: Any,
    gsm_tag: str,
    gsm_kind: str | None,
    detector_family: str | None,
    *,
    play_task: str = PLAY_TASK,
    state_source: str = "n/a",
) -> None:
    models = selected_models(cfg, args)
    if not models:
        models = [("dry-run", "ollama")]
    for model_id, provider in models:
        print(f"Loading {model_id} ({provider})...", flush=True)
        gateway = None if args.skip_models else make_gateway(cfg, model_id, provider)
        cycle = AgentCycle(
            table_name="swipe",
            controller=controller,
            fixture="2048",
            logger=logger,
            gateway=gateway,
            prompt_mode=prompt_mode,
            manual=manual,
            harness_level="L1",
            paused=False,
            memory=AgentMemory("2048"),
            skills=skills,
            gsms=gsms,
            max_actions=1,
        )
        try:
            _run_turns(
                cycle,
                controller,
                args=args,
                model_id=model_id,
                run_dir=run_dir,
                score_rows=score_rows,
                level="L1",
                gsm_tag=gsm_tag,
                gsm_kind=gsm_kind,
                detector_family=detector_family,
                play_task=play_task,
                state_source=state_source,
            )
        finally:
            if gateway is not None:
                gateway.unload()


def _run_l2(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    controller: OsController,
    logger: JsonlLogger,
    run_dir: Any,
    prompt_mode: str,
    manual: str,
    score_rows: list[dict[str, Any]],
    gsms: Any,
    skills: Any,
    gsm_tag: str,
    gsm_kind: str | None,
    detector_family: str | None,
    *,
    harness_level: str = "GSM",
    play_task: str = PLAY_TASK,
    state_source: str = "n/a",
) -> None:
    memory = AgentMemory("2048")
    eyes = None
    brain = None
    if args.skip_models:
        model_id = "dry-run"
    elif args.dual_brain and not args.single_vlm:
        print("Loading eyes + brain (legacy Phase 1 dual-resident)...", flush=True)
        eyes = make_eyes(cfg)
        brain = make_brain(cfg)
        model_id = f"{eyes.model}+{brain.model}"
        print(f"eyes={eyes.model} brain={brain.model}", flush=True)
    else:
        print("Loading eyes (Phase 2 single VLM planner)...", flush=True)
        models = selected_models(cfg, args)
        if models:
            eyes = make_eyes(cfg, model_id=models[0][0])
        else:
            eyes = make_eyes(cfg)
        brain = None
        model_id = eyes.model
        print(f"eyes={eyes.model} planner=single-vlm", flush=True)
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,
        fixture="2048",
        logger=logger,
        gateway=eyes,
        eyes=eyes,
        brain=brain,
        memory=memory,
        skills=skills,
        prompt_mode=prompt_mode,
        manual=manual,
        harness_level=harness_level,
        paused=False,
        gsms=gsms,
        max_actions=1,
    )
    _run_turns(
        cycle,
        controller,
        args=args,
        model_id=model_id,
        run_dir=run_dir,
        score_rows=score_rows,
        level=harness_level,
        gsm_tag=gsm_tag,
        gsm_kind=gsm_kind,
        detector_family=detector_family,
        play_task=play_task,
        state_source=state_source,
    )


def _run_turns(
    cycle: AgentCycle,
    controller: OsController,
    *,
    args: argparse.Namespace,
    model_id: str,
    run_dir: Any,
    score_rows: list[dict[str, Any]],
    level: str,
    gsm_tag: str,
    gsm_kind: str | None,
    detector_family: str | None,
    play_task: str = PLAY_TASK,
    state_source: str = "n/a",
) -> None:
    total = args.turns
    noop_streak = 0
    for attempt in range(1, total + 1):
        injected = None
        if args.skip_models or args.force_gsm:
            if cycle.gsms is not None and (args.force_gsm or args.skip_models):
                injected = parse_act(
                    {"invoke_gsm": "choose_swipe", "actions": [], "reasoning": "force gsm"}
                )
            else:
                injected = parse_act({"actions": [{"name": "swipe_left", "details": "dry-run"}]})
        path = run_dir / "frames" / f"{safe_model_tag(model_id)}_{attempt:03d}.jpg"
        stuck = False
        verdict = None
        executed: list[str] = []
        gsm_name = None
        gsm_ticks = 0
        success = False
        pixel_ok = False
        before = latest_oracle()
        if before.get("over"):
            print(f"  [{attempt}/{total}] game over — restarting", flush=True)
            try:
                _focus_call(controller, lambda: controller.execute_named("swipe", "restart"))
                time.sleep(0.4)
            except Exception as exc:  # noqa: BLE001
                print(f"  restart_error={exc}", flush=True)
                break
            before = latest_oracle()
            noop_streak = 0
        try:
            _focus_call(
                controller,
                lambda: controller.execute([MousePrimitive(op="click", x=0.50, y=0.52)]),
            )
            time.sleep(0.05)
            outcome = cycle.turn(
                play_task,
                injected=injected,
                model=model_id,
                save_frame=path,
                extra_result={
                    "attempt": attempt,
                    "task": "play_2048",
                    "gsm": gsm_tag,
                    "gsm_kind": gsm_kind,
                    "state_source": state_source,
                    "detector_family": detector_family,
                    "oracle_before": before,
                },
            )
            executed = outcome.result.executed
            gsm_name = outcome.result.invoked_gsm
            gsm_ticks = outcome.result.gsm_ticks
            cycle_verdict = getattr(getattr(outcome, "outcome", None), "verdict", None)
            pixel_ok = cycle_verdict in {"success", "partial"}
            after = latest_oracle()
            moved = _grid_tuple(before.get("grid")) != _grid_tuple(after.get("grid")) and bool(
                after.get("grid")
            )
            if moved:
                verdict = "success"
                success = True
                noop_streak = 0
            elif executed:
                verdict = "noop"
                success = False
                noop_streak += 1
            else:
                verdict = "failure"
                success = False
                noop_streak += 1
            stuck = outcome.stuck
        except Exception as exc:  # noqa: BLE001
            executed = []
            success = False
            after = latest_oracle()
            print(f"  exec_error={exc}", flush=True)
            noop_streak += 1

        score_rows.append(
            {
                "harness_level": level,
                "model": model_id,
                "task": "play_2048",
                "attempt": attempt,
                "success": success,
                "pixel_success": pixel_ok,
                "stuck": stuck,
                "gsm": gsm_tag,
                "state_source": state_source,
                "vlm_calls_per_episode": 1,
            }
        )
        extra = f" verdict={verdict} stuck={stuck}" if verdict is not None else ""
        if gsm_name:
            extra += f" gsm={gsm_name} ticks={gsm_ticks}"
        score = after.get("score") if isinstance(after, dict) else None
        print(
            f"  [{attempt}/{total}] executed={executed or '(none)'} "
            f"score={score} max={_max_tile((after or {}).get('grid'))}{extra}",
            flush=True,
        )
        if noop_streak >= 4:
            print("  four noops — clearing swipe bans (live game kept)", flush=True)
            noop_streak = 0
            if cycle.memory is not None and hasattr(cycle.memory, "episodic"):
                cycle.memory.episodic.banned.clear()


if __name__ == "__main__":
    raise SystemExit(main())

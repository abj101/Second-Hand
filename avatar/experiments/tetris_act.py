from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any

from avatar.config import load_config
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.eval.scoring import gsm_lift_table, gsm_rows_from_turns, write_markdown_table
from avatar.experiments.common import add_model_flags, parse_act, safe_model_tag, selected_models
from avatar.fixtures.browser_window import launch_app_window
from avatar.fixtures.server import serve_tetris_hint
from avatar.gsm.registry import GsmRegistry
from avatar.gsm.runtime import attach_live_runtime
from avatar.harness.cycle import AgentCycle
from avatar.harness.executor import OsController
from avatar.harness.primitives import MousePrimitive
from avatar.harness.profile import profile_from_config
from avatar.harness.window import close_windows, wait_for_window
from avatar.memory.store import AgentMemory
from avatar.models import make_gateway
from avatar.prompts.library import load_manual
from avatar.schema import TurnLog, CaptureSettings

TETRIS_PLAY_TASK = (
    "Play Tetris using the strategy in the how-to. "
    "This turn: pick a target column, rotate, shift there, then hard_drop to lock. "
    "Keep the stack flat. Leave the rightmost column empty for I. Do not dump at spawn."
)
TETRIS_GSM_TASK = (
    "If the screen is a menu, idle, or game over: use named actions start or restart. "
    "Do not invoke_gsm until an active tetromino is falling. "
    "When a piece is falling in gameplay: invoke_gsm place_piece with an empty actions list "
    "so the GSM places and locks the piece. Do not mix invoke_gsm with named actions."
)


def _game_url(*, realtime: bool, fall_ms: int | None, url: str | None) -> str:
    if url:
        return url
    if realtime:
        query = f"?fall_ms={fall_ms}" if fall_ms else ""
        return f"http://127.0.0.1:5173/{query}"
    return "http://127.0.0.1:5173/?pause_and_decide=1"


def _url_reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(url.split("?")[0], timeout=2)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_between_models(pause_between: bool) -> None:
    if pause_between:
        input("Press Enter for the next model… ")


def _boot_tetris_game(controller: OsController, *, scout_dir: Any | None = None) -> None:
    """Focus the canvas and start play. Edge app windows often ignore Enter until clicked."""
    for _ in range(10):
        controller.focus()
        if controller.capture.is_foreground():
            break
        time.sleep(0.35)
    time.sleep(0.25)
    controller.execute([MousePrimitive(op="click", x=0.5, y=0.45)])
    time.sleep(0.15)
    controller.execute_named("tetris", "start")
    cap = controller.capture
    cap.skip_streak = 0
    cap.refresh()
    settle = cap.wait_settled()
    if not settle.changed:
        controller.execute_named("tetris", "start")
        time.sleep(0.4)
        cap.skip_streak = 0
    cap.diagnose_and_apply(scout_dir=scout_dir)
    cap.select_preset("gameplay")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tetris experiment: OS harness (paused or realtime)")
    parser.add_argument("--url", default=None, help="override fixture URL")
    parser.add_argument("--window", default=None, help="window title substring")
    parser.add_argument(
        "--launch",
        action="store_true",
        help="open the URL in a standalone Edge/Chrome app window",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="deprecated alias for --launch",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="gravity keeps falling (no pause_and_decide).",
    )
    parser.add_argument("--manual", action="store_true", help="inject tetris how-to (default)")
    parser.add_argument("--pixels", action="store_true", help="pixels-only: no how-to text")
    parser.add_argument("--fall-ms", type=int, default=None, metavar="MS")
    parser.add_argument("--turns", type=int, default=None, metavar="N")
    parser.add_argument("--pause-between", action="store_true")
    parser.add_argument("--action-delay-ms", type=int, default=None, metavar="MS")
    parser.add_argument(
        "--gsm",
        action="store_true",
        help="G0+ GSM handoff: planner may invoke_gsm; inner ticks skip the VLM.",
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
        "--reflect-every",
        type=int,
        default=None,
        metavar="N",
        help="Last-task reflection every turn; last-10 + action-design every N turns. "
        "0 disables historical reflection. Default 0 for realtime GSM, else 1.",
    )
    parser.add_argument(
        "--state-source",
        default=None,
        choices=["extracted", "pixels"],
        help="G5 ablation: RL/rule observation source (does not change prompt_mode).",
    )
    parser.add_argument(
        "--force-gsm",
        action="store_true",
        help="Skip the planner and always invoke place_piece (G0 wiring / latency smoke).",
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Train TetrisGsmEnv on the live window, then attach the policy as an RL GSM.",
    )
    parser.add_argument("--train-episodes", type=int, default=8, metavar="N")
    add_model_flags(parser)
    args = parser.parse_args(argv)
    if args.turns is None:
        args.turns = 20
    if args.turns < 1:
        parser.error("--turns must be >= 1")
    if args.action_delay_ms is None:
        args.action_delay_ms = 0 if args.realtime else 800
    if args.fall_ms is not None and args.fall_ms < 1:
        parser.error("--fall-ms must be >= 1")
    if args.headed:
        args.launch = True
    if args.reflect_every is None:
        args.reflect_every = 0 if (args.gsm and args.realtime) else 1

    cfg = load_config()
    fixture_cfg = (cfg.get("fixtures") or {}).get("tetris") or {}
    window_title = args.window or str(fixture_cfg.get("window_title") or "Tetris")
    use_gsm = bool(args.gsm)
    fixture_tag = "tetris-rt" if args.realtime else "tetris"
    stage = "gsm" if use_gsm else "act"
    run_dir = new_run_dir(stage, fixture_tag)
    logger = JsonlLogger(run_dir)
    prompt_mode = "pixels" if args.pixels else "manual"
    manual = load_manual("tetris") if prompt_mode == "manual" else ""
    url = _game_url(realtime=args.realtime, fall_ms=args.fall_ms, url=args.url)
    paused = not args.realtime
    gsms = GsmRegistry.from_config(cfg, "tetris") if use_gsm else None
    if gsms is not None and args.state_source:
        for spec in gsms.specs.values():
            spec.state_source = args.state_source  # type: ignore[assignment]
    if gsms is not None and args.detector:
        for spec in gsms.specs.values():
            spec.detector_family = args.detector
    detector_device = args.detector_device or str((cfg.get("gsm") or {}).get("detector_device") or "cpu") or "cpu"
    if gsms is not None:
        for spec in gsms.specs.values():
            spec.detector_device = detector_device
        if detector_device == "cuda":
            from avatar.models import unload_locked_eyes

            print("detector_device=cuda: unloading locked VLM before DINO load.", flush=True)
            unload_locked_eyes(cfg)
        attach_live_runtime(gsms)
    gsm_kind = None
    detector_family = None
    state_source = args.state_source or ((cfg.get("gsm") or {}).get("default_state_source"))
    if gsms is not None and gsms.specs:
        spec0 = next(iter(gsms.specs.values()))
        gsm_kind = spec0.kind
        det = getattr(getattr(gsms, "runtime", None), "detector", None)
        detector_family = getattr(det, "family", None) or spec0.detector_family or None
        if args.state_source is None:
            state_source = spec0.state_source
    gsm_tag = "off" if not use_gsm else (gsm_kind or "rule")

    if not _url_reachable(url):
        print("Tetris dev server not reachable at", url, "\n", serve_tetris_hint(), flush=True)
        return 2

    print(
        f"Tetris {'realtime' if args.realtime else 'pause-and-decide'} | "
        f"prompt={prompt_mode} | gsm={gsm_tag} | detector={detector_family or 'n/a'} | "
        f"{'planner=single-vlm | ' if use_gsm else ''}"
        f"window={window_title!r} | turns={args.turns} | {url}",
        flush=True,
    )
    if args.launch:
        closed = close_windows(window_title)
        if closed:
            print(f"Closed {closed} leftover {window_title!r} window(s).", flush=True)
        launch_app_window(url)
        time.sleep(1.0)
    try:
        game_window = wait_for_window(window_title, timeout_s=25)
    except RuntimeError:
        print(
            f"No window matching {window_title!r}. Open {url} in a browser "
            "(pass --launch), click the game, then re-run.",
            flush=True,
        )
        return 2

    models = selected_models(cfg, args)
    if not models:
        models = [("dry-run", "ollama")]

    profile = profile_from_config(cfg, "tetris")
    if args.realtime:
        profile.skip_unchanged = False
    if use_gsm and args.realtime:
        profile.force_every = 0
        profile.scout_every = 0
    play_task = TETRIS_GSM_TASK if use_gsm else TETRIS_PLAY_TASK
    memory = AgentMemory("tetris")
    with OsController(window_title, profile=profile, hwnd=game_window.hwnd) as controller:
        for model_index, (model_id, provider) in enumerate(models):
            if model_index > 0:
                _wait_between_models(args.pause_between)
            print(f"\n=== {model_id} ({provider}) — {args.turns} turn(s) ===", flush=True)
            gateway = None if args.skip_models else make_gateway(cfg, model_id, provider)
            cycle = AgentCycle(
                table_name="tetris",
                controller=controller,
                fixture="tetris",
                logger=logger,
                gateway=gateway,
                eyes=gateway if use_gsm else None,
                brain=None,
                prompt_mode=prompt_mode,
                manual=manual,
                harness_level="GSM" if use_gsm else "L1",
                paused=paused,
                realtime=args.realtime,
                gsms=gsms,
                memory=memory,
                reflect_every=args.reflect_every,
            )
            try:
                _boot_tetris_game(controller, scout_dir=run_dir / "scout")
                if use_gsm and args.train and gsms is not None:
                    from avatar.gsm.rl.env import TetrisGsmEnv
                    from avatar.gsm.rl.loop import train_analyze_optimize
                    from avatar.gsm.runtime import attach_live_runtime as _reattach

                    spec = next(iter(gsms.specs.values()))
                    det = getattr(getattr(gsms, "runtime", None), "detector", None)
                    live_env = TetrisGsmEnv(
                        controller.execute_named,
                        lambda: controller.capture.grab(fresh=True)[0],
                        detector=det,
                        state_source=spec.state_source,
                        labels=list(spec.labels or []),
                    )
                    trained = train_analyze_optimize(
                        spec,
                        env=live_env,
                        max_opt_iters=1,
                        episodes=int(args.train_episodes),
                        run_dir=run_dir / "rl",
                    )
                    spec.kind = "rl"  # type: ignore[assignment]
                    spec.enabled = True
                    _reattach(gsms, rl_policy=trained["agent"])
                    print(f"Attached RL policy after {trained['opt_iters']} opt iter(s).", flush=True)
                start_path = run_dir / "frames" / f"{safe_model_tag(model_id)}_start.jpg"
                start_path.parent.mkdir(parents=True, exist_ok=True)
                frame, _ = controller.capture.grab()
                start_path.write_bytes(frame.data)
                time.sleep(0.3)
                for turn in range(1, args.turns + 1):
                    injected = None
                    if args.skip_models or args.force_gsm:
                        if use_gsm:
                            injected = parse_act(
                                {"invoke_gsm": "place_piece", "actions": [], "reasoning": "force gsm"}
                            )
                        else:
                            injected = parse_act({"actions": [{"name": "shift_left", "details": "dry-run"}]})
                    print(f"  turn {turn}/{args.turns}: asking {model_id}…", flush=True)
                    path = run_dir / "frames" / f"{safe_model_tag(model_id)}_t{turn:02d}.jpg"
                    latency_ms = None
                    extra = {
                        "attempt": turn,
                        "realtime": args.realtime,
                        "gsm": gsm_tag,
                        "gsm_kind": gsm_kind,
                        "state_source": state_source if use_gsm else "n/a",
                        "detector_family": detector_family,
                    }
                    try:
                        outcome = cycle.turn(
                            play_task,
                            injected=injected,
                            model=model_id,
                            save_frame=path,
                            extra_result=extra,
                        )
                        executed = outcome.result.executed
                        latency_ms = outcome.latency_ms
                        gsm_name = outcome.result.invoked_gsm
                    except Exception as exc:  # noqa: BLE001
                        executed = []
                        gsm_name = None
                        print(f"      exec_error={exc}", flush=True)
                        logger.write(
                            TurnLog(
                                screenshot_ref=str(path),
                                capture_settings=CaptureSettings(),
                                model=model_id,
                                harness_level="GSM" if use_gsm else "L1",
                                fixture="tetris",
                                paused=paused,
                                prompt_mode=prompt_mode,  # type: ignore[arg-type]
                                reasoning=str(exc),
                                result={**extra, "executed": [], "exec_error": str(exc), "verdict": "failure"},
                                stage="act",
                            )
                        )
                    latency_s = "n/a" if latency_ms is None else f"{latency_ms:.0f}ms"
                    handoff = f" gsm={gsm_name}" if gsm_name else ""
                    print(f"      -> {executed or '(none)'} [{latency_s}]{handoff}", flush=True)
                    if args.action_delay_ms > 0:
                        time.sleep(args.action_delay_ms / 1000)
            finally:
                if gateway is not None:
                    gateway.unload()

    turns = logger.read()
    rows = gsm_rows_from_turns(turns)
    write_markdown_table(gsm_lift_table(rows), run_dir / "harness_lift.md")
    invokes = sum(1 for rec in turns if (rec.get("result") or {}).get("gsm_name"))
    skipped = sum(1 for rec in turns if (rec.get("result") or {}).get("skipped_inference"))
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "fixture": fixture_tag,
                "harness_level": "GSM" if use_gsm else "L1",
                "gsm": gsm_tag,
                "gsm_kind": gsm_kind,
                "state_source": state_source if use_gsm else "n/a",
                "detector_family": detector_family,
                "paused": paused,
                "prompt_mode": prompt_mode,
                "turns": len(turns),
                "gsm_invokes": invokes,
                "skipped_inference": skipped,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Tetris experiment run written to {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

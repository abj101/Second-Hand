from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from loop.config import (
    DEFAULT_ACT_FALLBACK_MODEL,
    DEFAULT_ACT_MODEL,
    DEFAULT_CLOUD_MODEL,
    DEFAULT_MODEL,
    project_root,
    resolve_perceive_model,
)
from loop.fixtures import FIXTURE_IDS, make_fixture
from loop.fixtures.control_lab import LAB_TITLE
from loop.live import clear_stop, publish
from loop.log.store import EpisodeStore, new_run_dir, resolve_run_dirs
from loop.memory.skills import SkillBank
from loop.memory.store import MemoryStore
from loop.os.executor import OsController
from loop.os.gamepad import GamepadUnavailable
from loop.os.primitives import GamepadPrimitive, KeyPrimitive, MousePrimitive
from loop.os.window import client_to_screen, defocus_to_console, wait_for_window
from loop.safety.panic import PanicHotkey


def _countdown(seconds: int) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"  sending input in {remaining}…", flush=True)
        time.sleep(1)


def _install_panic(controller: OsController | None) -> PanicHotkey | None:
    def _panic() -> None:
        print("F12 panic: release_all", flush=True)
        if controller is not None:
            controller.release_all()

    hotkey = PanicHotkey(_panic)
    hotkey.start()
    return hotkey


def _prompt_learn(learn: bool | None) -> bool:
    if learn is True:
        return True
    if learn is False:
        return False
    try:
        ans = input("Learn now? [Y/n] ").strip().lower()
    except EOFError:
        return False
    return ans in {"", "y", "yes"}


def _resolve_architecture(fixture: str, requested: str | None) -> str:
    """Resolve the mechanical CLI default without changing agent runtime logic."""
    return requested or ("two_stage" if fixture == "sts2" else "overlay")


def cmd_smoke(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke unlabeled OS input against a window")
    parser.add_argument("--window", default=None, help="title substring (default: spawn control lab)")
    parser.add_argument("--no-lab", action="store_true")
    parser.add_argument("--countdown", type=int, default=3)
    parser.add_argument("--echo", type=Path, default=None)
    args = parser.parse_args(argv)

    lab_proc: subprocess.Popen | None = None
    echo_path = args.echo or (project_root() / "runs" / "control_lab_echo.json")
    title = args.window
    if title is None and not args.no_lab:
        title = LAB_TITLE
        echo_path.parent.mkdir(parents=True, exist_ok=True)
        lab_proc = subprocess.Popen(
            [sys.executable, "-m", "loop.fixtures.control_lab", str(echo_path), "25"],
            cwd=str(project_root()),
        )
    if title is None:
        parser.error("pass --window TITLE or omit it to spawn the control lab")

    try:
        print(f"Waiting for window matching {title!r}…", flush=True)
        wait_for_window(title, timeout_s=20)
        _countdown(max(0, args.countdown))
        enable_pad = title != LAB_TITLE
        with OsController(title, enable_gamepad=enable_pad) as ctl:
            ctl.focus()
            print(f"Focused {ctl.target.title!r} client={ctl.target.width}x{ctl.target.height}", flush=True)
            for code in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
                ctl.execute([KeyPrimitive(code=code, op="tap")])
                time.sleep(0.12)
            sx, sy = client_to_screen(ctl.target, 0.5, 0.5)
            print(f"Mouse click client center screen=({sx},{sy})", flush=True)
            ctl.execute([MousePrimitive(op="click", x=0.5, y=0.5)])
            time.sleep(0.2)
            try:
                if ctl.gamepad is not None:
                    ctl.execute(
                        [
                            GamepadPrimitive(op="button_tap", button="a"),
                            GamepadPrimitive(op="stick", stick="left", x=0.5, y=0.0, duration_ms=200),
                        ]
                    )
                    print("Gamepad: ok", flush=True)
            except GamepadUnavailable as exc:
                print(f"Gamepad skipped: {exc}", flush=True)
        if echo_path.exists():
            payload = json.loads(echo_path.read_text(encoding="utf-8"))
            print(f"Echo events: {[row.get('kind') for row in payload]}", flush=True)
        print("Smoke complete.", flush=True)
        return 0
    finally:
        if lab_proc is not None:
            lab_proc.terminate()
            try:
                lab_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                lab_proc.kill()


def cmd_perceive(argv: list[str] | None = None) -> int:
    from loop.perceive import run_perceive

    parser = argparse.ArgumentParser(description="Validate HUD labels, then VLM vs score_hud")
    parser.add_argument("--fixture", default="sts2", choices=FIXTURE_IDS)
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    publish(job="perceive", phase="starting", fixture=args.fixture, message="perceive", thought="")
    report = run_perceive(args.fixture, skip_models=args.skip_models, model=args.model)
    print(json.dumps({k: v for k, v in report.items() if k != "labeled"}, indent=2, default=str))
    print("labeled:", json.dumps(report.get("labeled"), indent=2))
    ok = bool(report.get("ok"))
    publish(
        job="perceive",
        phase="done" if ok else "error",
        fixture=args.fixture,
        message=str(report.get("reason") or ("ok" if ok else "failed")),
    )
    return 0 if ok else 2


def cmd_collect(argv: list[str] | None = None) -> int:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.curate.curator import librarian_spec, skill_bank_path
    from loop.model.gateway import VlmGateway

    parser = argparse.ArgumentParser(description="Collect session")
    parser.add_argument("--fixture", default="sts2", choices=FIXTURE_IDS)
    parser.add_argument("--lives", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--timeout-s", type=float, default=600.0)
    parser.add_argument(
        "--until-fail",
        action="store_true",
        help="run until death/win; raises --max-steps/--timeout-s unless already set higher",
    )
    parser.add_argument("--arm", choices=("no_skill", "dump_all", "ratchet"), default=None)
    parser.add_argument("--memory", action="store_true", help="alias for --arm dump_all")
    parser.add_argument("--skill-off-frac", type=float, default=0.5)
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument(
        "--policy",
        choices=("vlm", "heuristic"),
        default=None,
        help="sts2: heuristic is the proposer (seed SFT). skip-models on sts2 defaults to heuristic.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="reasoner model override (STS2 two_stage defaults to gemma4:31b-cloud)",
    )
    parser.add_argument(
        "--reasoner-model",
        default=None,
        help="two_stage second LLM (reasoner). Overrides --model; e.g. qwen3.5:8b-q4_K_M",
    )
    parser.add_argument(
        "--perceive-model",
        default=None,
        help="LLM1 scene perceive model (default: gemma4:31b-cloud)",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help=f"LLM1 on {DEFAULT_CLOUD_MODEL}; ACT defaults to {DEFAULT_ACT_MODEL}",
    )
    parser.add_argument("--countdown", type=int, default=3)
    parser.add_argument(
        "--eval-snapshot",
        action="store_true",
        help="copy start.sl2 over the live save after fail (eval protocol)",
    )
    parser.add_argument(
        "--hud-poll-s",
        type=float,
        default=1.0,
        help="background HUD poll interval; 0 disables. Missing bars do not pause the agent.",
    )
    dino = parser.add_mutually_exclusive_group()
    dino.add_argument(
        "--dino",
        dest="dino",
        action="store_true",
        help="enable Grounding DINO grounding",
    )
    dino.add_argument(
        "--no-dino",
        dest="dino",
        action="store_false",
        help="disable Grounding DINO (useful for an apples-to-apples ablation)",
    )
    parser.set_defaults(dino=False)
    parser.add_argument(
        "--dino-device",
        default="cpu",
        help="cpu (default, coexist with Ollama) or cuda",
    )
    parser.add_argument(
        "--dino-model",
        default="IDEA-Research/grounding-dino-base",
        help="HuggingFace Grounding DINO id (default: base; tiny is faster)",
    )
    gui = parser.add_mutually_exclusive_group()
    gui.add_argument(
        "--gui",
        dest="gui",
        action="store_true",
        help="YOLO interactable marks in the STS2 overlay (default)",
    )
    gui.add_argument(
        "--no-gui",
        dest="gui",
        action="store_false",
        help="disable YOLO GUI detector; OCR+CV only",
    )
    parser.set_defaults(gui=True)
    parser.add_argument(
        "--gui-device",
        default="cpu",
        help="cpu (default, coexist with Ollama) or cuda",
    )
    parser.add_argument(
        "--gui-model",
        default="gpa",
        choices=("gpa", "groundcua"),
        help="YOLO GUI weights: gpa (default MIT) or groundcua",
    )
    parser.add_argument(
        "--no-survey",
        action="store_true",
        help="skip the mandated 4-look camera sweep at episode start",
    )
    parser.add_argument(
        "--no-debug-frames",
        action="store_true",
        help="skip extra dino/obs frame writes when there are no boxes",
    )
    parser.add_argument(
        "--memory-scope",
        choices=("run", "episode"),
        default="run",
        help="Notebook Known/Lessons scope: run (default) or episode",
    )
    parser.add_argument(
        "--architecture",
        choices=("overlay", "two_stage"),
        default=None,
        help="sts2 architecture (default: two_stage); overlay is an explicit ablation",
    )
    parser.add_argument(
        "--debug-overlay",
        action="store_true",
        help="with two_stage, also save painted debug.jpg per step",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=3,
        help="legacy/non-STS2 two_stage: max model-driven MCP tool rounds per turn",
    )
    learn = parser.add_mutually_exclusive_group()
    learn.add_argument("--learn", action="store_true")
    learn.add_argument("--no-learn", action="store_true")
    args = parser.parse_args(argv)
    # Unknown-first dual-agent prompting is the STS2 default. Keep overlay
    # available as an explicit ablation and retain it for other fixtures.
    architecture = _resolve_architecture(args.fixture, args.architecture)
    reasoner_model = (
        args.reasoner_model
        or args.model
        or (DEFAULT_ACT_MODEL if args.fixture == "sts2" and architecture == "two_stage" else DEFAULT_MODEL)
    )
    arm = args.arm or ("dump_all" if args.memory else "no_skill")
    policy = args.policy
    if policy is None:
        policy = "heuristic" if (args.skip_models and args.fixture == "sts2") else "vlm"
    skip_models = bool(args.skip_models or policy == "heuristic")
    from loop.agent.heuristic import HEURISTIC_POLICY_ID

    policy_id = HEURISTIC_POLICY_ID if policy == "heuristic" else reasoner_model
    perceive_model = resolve_perceive_model(
        model=reasoner_model,
        perceive_model=args.perceive_model,
        cloud=bool(args.cloud),
    )
    max_steps = args.max_steps
    timeout_s = args.timeout_s
    if args.until_fail:
        if args.max_steps == 32:
            max_steps = 2000
        if args.timeout_s == 600.0:
            timeout_s = 14400.0
    clear_stop()
    publish(
        job="collect",
        phase="starting",
        fixture=args.fixture,
        arm=arm,
        lives=args.lives,
        max_steps=max_steps,
        message="starting collect",
        thought="",
        action="",
        policy_id=policy_id,
        perceive_model=perceive_model,
    )
    fixture = make_fixture(args.fixture, attach=args.fixture != "fake")
    panic = _install_panic(fixture.controller)
    try:
        if args.fixture != "fake":
            _countdown(max(0, args.countdown))
            if fixture.controller is not None:
                fixture.controller.focus()

        run_dir = new_run_dir(fixture.id, project_root())
        store = EpisodeStore(run_dir)
        memory = None
        skills = None
        if arm == "dump_all" and getattr(fixture, "pace", "realtime") != "turn":
            memory = MemoryStore(project_root() / "data" / "loop" / fixture.id / "memory.sqlite")
        if arm == "ratchet":
            spec = librarian_spec(fixture.id)
            skills = SkillBank(
                skill_bank_path(fixture.id),
                n_min=spec["n_min"],
                tau=spec["tau"],
                cap=spec["cap"],
            )
        gateway = None if skip_models else VlmGateway(
            model=reasoner_model,
            temperature=0.1,
            max_tokens=96,
            num_ctx=2048,
        )
        perceive_gateway = None
        if not skip_models and architecture == "two_stage":
            perceive_gateway = VlmGateway(model=perceive_model, num_ctx=8192)
            print(
                f"  llm1={perceive_model} ctx=8192  reasoner={reasoner_model}",
                flush=True,
            )
        elif not skip_models and perceive_model != reasoner_model:
            perceive_gateway = VlmGateway(model=perceive_model)
            print(
                f"  llm1={perceive_model}  reasoner={reasoner_model}",
                flush=True,
            )
        elif not skip_models and args.cloud:
            print(
                f"  llm1={perceive_model} (cloud)  reasoner={reasoner_model}",
                flush=True,
            )
        agent = CollectAgent(
            fixture,
            store,
            memory=memory,
            skills=skills,
            gateway=gateway,
            perceive_gateway=perceive_gateway,
            config=CollectConfig(
                lives=args.lives,
                max_steps=max_steps,
                timeout_s=timeout_s,
                arm=arm,
                memory=arm == "dump_all",
                skip_models=skip_models,
                policy_id=policy_id,
                policy=policy,
                skill_off_frac=args.skill_off_frac,
                eval_snapshot=args.eval_snapshot,
                hud_poll_s=args.hud_poll_s,
                dino=args.dino,
                dino_required=bool(args.dino and fixture.id != "fake"),
                dino_device=args.dino_device,
                dino_model=args.dino_model,
                gui=args.gui,
                gui_device=args.gui_device,
                gui_model=args.gui_model,
                gui_download=bool(args.gui),
                survey=not args.no_survey,
                debug_frames=not args.no_debug_frames,
                until_fail=args.until_fail,
                architecture=architecture,
                debug_overlay=bool(args.debug_overlay),
                max_tool_rounds=args.max_tool_rounds,
                perceive_model=perceive_model,
                reason_fallback_model=DEFAULT_ACT_FALLBACK_MODEL,
                memory_scope=args.memory_scope,
            ),
        )
        try:
            summary = agent.run_session()
            print(summary.model_dump_json(indent=2), flush=True)
            print(f"run_dir={run_dir}", flush=True)
            if fixture.controller is not None:
                fixture.controller.release_all()
            defocus_to_console()
            learn_now = True if args.learn else False if args.no_learn else None
            if _prompt_learn(learn_now):
                curate_argv = ["--fixture", fixture.id, "--run-dir", str(run_dir)]
                if args.skip_models or policy == "heuristic":
                    curate_argv.append("--skip-models")
                else:
                    curate_argv.extend(["--model", reasoner_model])
                return cmd_curate(curate_argv)
            return 0
        except Exception as exc:
            publish(job="collect", phase="error", message=str(exc), thought="")
            try:
                agent._finalize_session()
            except Exception:
                pass
            raise
        finally:
            fixture.close()
            if memory is not None:
                memory.close()
            if gateway is not None:
                gateway.unload()
            if perceive_gateway is not None:
                perceive_gateway.unload()
    finally:
        if panic is not None:
            panic.stop()


def cmd_curate(argv: list[str] | None = None) -> int:
    from loop.curate.curator import Curator, perceive_report_path

    parser = argparse.ArgumentParser(description="Librarian: critic, synth, retire, cap")
    parser.add_argument("--fixture", default="elden", choices=FIXTURE_IDS)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    publish(job="curate", phase="starting", fixture=args.fixture, message="curate", thought="")
    if not args.skip_models:
        report_path = perceive_report_path(args.fixture)
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            if not payload.get("ok"):
                print(f"perceive gate: {payload.get('reason') or 'failed'}", file=sys.stderr)
                return 2

    gateway = None
    if not args.skip_models:
        from loop.model.gateway import VlmGateway

        gateway = VlmGateway(model=args.model)
    try:
        curator = Curator(
            args.fixture,
            run_dir=args.run_dir,
            gateway=gateway,
            skip_models=args.skip_models,
        )
        report = curator.run(preview=args.preview)
        print(json.dumps(report.__dict__, indent=2, default=str), flush=True)
        publish(
            job="curate",
            phase="done",
            fixture=args.fixture,
            message=f"born={len(report.born or [])} retired={len(report.retired or [])}",
        )
        return 0
    finally:
        if gateway is not None:
            gateway.unload()


def cmd_status(argv: list[str] | None = None) -> int:
    from loop.curate.curator import Curator

    parser = argparse.ArgumentParser(description="Skill bank status")
    parser.add_argument("--fixture", default="elden", choices=FIXTURE_IDS)
    args = parser.parse_args(argv)
    curator = Curator(args.fixture, skip_models=True)
    rows = curator.preview_rows()
    print(json.dumps({"fixture": args.fixture, "skills": rows, "n": len(rows)}, indent=2))
    publish(job="status", phase="done", fixture=args.fixture, message=f"{len(rows)} skills")
    return 0


def cmd_analyze(argv: list[str] | None = None) -> int:
    from loop.analyze import analyze_fixture

    parser = argparse.ArgumentParser(description="p_on minus p_off per scheduled skill")
    parser.add_argument("--fixture", default="elden", choices=FIXTURE_IDS)
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    publish(job="analyze", phase="starting", fixture=args.fixture, message="analyze", thought="")
    report = analyze_fixture(args.fixture, run_dir=args.run_dir)
    print(json.dumps(report, indent=2), flush=True)
    n = len(report.get("skills") or [])
    publish(job="analyze", phase="done", fixture=args.fixture, message=f"{n} skills")
    return 0


def cmd_calibrate_view(argv: list[str] | None = None) -> int:
    from loop.eval.export import calibrate_view, iter_elden_runs

    parser = argparse.ArgumentParser(description="Calibrate view_changed on look-only frames")
    parser.add_argument("--run-dir", type=Path, action="append", default=None)
    args = parser.parse_args(argv)
    runs = args.run_dir or iter_elden_runs()
    report = calibrate_view(runs)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2), flush=True)
    print(f"look_only_rows={report['look_only']} view_changed={report['view_changed']}", flush=True)
    return 0


def _parse_run_dirs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", type=Path, action="append", default=None)
    parser.add_argument("--all-runs", action="store_true", help="pool every *_<fixture>_collect run")


def _run_dirs_from_args(args: argparse.Namespace) -> list[Path]:
    return resolve_run_dirs(
        args.fixture,
        project_root(),
        args.run_dir,
        all_runs=bool(getattr(args, "all_runs", False)),
    )


def cmd_export_sft(argv: list[str] | None = None) -> int:
    from loop.eval.export import notebook_schema_report, prompt_hash_report, write_sft_jsonl

    parser = argparse.ArgumentParser(description="Export verified (obs, chord, outcome) SFT rows")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--source", choices=("vlm", "heuristic", "all"), default="vlm")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    hashes = prompt_hash_report(run_dirs)
    schema = notebook_schema_report(run_dirs)
    dest = write_sft_jsonl(run_dirs[0], args.out, run_dirs=run_dirs, source=args.source)
    print(json.dumps({"wrote": str(dest), "prompt": hashes, "notebook_schema": schema}, indent=2), flush=True)
    if hashes.get("mixed"):
        print(f"dropped minority prompt_id hashes n={hashes.get('dropped')}", flush=True)
    if schema.get("mixed"):
        print(f"mixed notebook_schema versions {schema.get('versions')}", file=sys.stderr)
    return 0


def cmd_export_dpo(argv: list[str] | None = None) -> int:
    from loop.eval.export import notebook_schema_report, prompt_hash_report, write_dpo_jsonl

    parser = argparse.ArgumentParser(description="Export same-frame STS2 DPO pairs")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--source", choices=("vlm", "heuristic", "all"), default="vlm")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    dest = write_dpo_jsonl(run_dirs[0], args.out, run_dirs=run_dirs, source=args.source)
    schema = notebook_schema_report(run_dirs)
    print(
        json.dumps(
            {"wrote": str(dest), "prompt": prompt_hash_report(run_dirs), "notebook_schema": schema},
            indent=2,
        ),
        flush=True,
    )
    if schema.get("mixed"):
        print(f"mixed notebook_schema versions {schema.get('versions')}", file=sys.stderr)
    return 0


def cmd_inspect_run(argv: list[str] | None = None) -> int:
    from loop.eval.export import inspect_run

    parser = argparse.ArgumentParser(description="SFT/DPO yield, prompt hashes, reward mix")
    parser.add_argument("--fixture", default="sts2")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    report = inspect_run(run_dirs)
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


def cmd_benchmark_perceive(argv: list[str] | None = None) -> int:
    from loop.eval.perceive_benchmark import offline_score_labels, run_benchmark
    from loop.model.gateway import VlmGateway

    parser = argparse.ArgumentParser(description="A/B test perceive prompts on frozen STS2 screenshots")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--prompts", default="v1,v2", help="comma-separated keys from PERCEIVE_PROMPTS")
    parser.add_argument("--offline-ocr", action="store_true", help="only score OCR/hud on fixtures")
    parser.add_argument("--skip-models", action="store_true", help="score parser only (no VLM)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    if args.offline_ocr:
        report = offline_score_labels(args.fixture)
        print(json.dumps(report, indent=2, default=str), flush=True)
        return 0
    gateway = None
    if not args.skip_models:
        gateway = VlmGateway(model=args.model)
    try:
        report = run_benchmark(
            fixture=args.fixture,
            prompts=prompts,
            gateway=gateway,
            limit=args.limit,
        )
    finally:
        if gateway is not None:
            gateway.unload()
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


def cmd_benchmark_proposals(argv: list[str] | None = None) -> int:
    from loop.eval.grounding_benchmark import run_production_proposal_benchmark
    from loop.eval.perception_cost import default_proposal_replay_path

    parser = argparse.ArgumentParser(description="Production proposal fusion replay (truth is scorer-only)")
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_production_proposal_benchmark(args.labels or default_proposal_replay_path())
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0 if report.get("legend_clean", True) else 1


def cmd_benchmark_effects(argv: list[str] | None = None) -> int:
    from loop.eval.effect_benchmark import run_effect_benchmark
    from loop.eval.perception_cost import default_effect_replay_path

    parser = argparse.ArgumentParser(description="Offline dispatch/effect sequence replay")
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_effect_benchmark(args.labels or default_effect_replay_path())
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


def cmd_benchmark_perception_cost(argv: list[str] | None = None) -> int:
    from loop.eval.perception_cost import build_perception_cost_report

    parser = argparse.ArgumentParser(description="Merged proposal+effect perception cost report")
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--effect", type=Path, default=None)
    args = parser.parse_args(argv)
    report = build_perception_cost_report(proposal_path=args.proposal, effect_path=args.effect)
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


def cmd_eval(argv: list[str] | None = None) -> int:
    from loop.eval.export import compare_turn_runs, turn_run_metrics

    parser = argparse.ArgumentParser(description="Held-out STS2 metrics; refuse mixed prompt_id")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--candidate", type=Path, default=None)
    args = parser.parse_args(argv)
    if (args.baseline is None) ^ (args.candidate is None):
        parser.error("pass both --baseline and --candidate, or neither")
    if args.baseline is not None and args.candidate is not None:
        report = compare_turn_runs(args.baseline, args.candidate)
        print(json.dumps(report, indent=2, default=str), flush=True)
        if not report.get("ok"):
            print(str(report.get("reason") or "eval comparison refused"), file=sys.stderr)
            return 2
        return 0
    run_dirs = resolve_run_dirs(args.fixture, project_root(), [args.run_dir] if args.run_dir else None)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    report = turn_run_metrics(run_dirs[0])
    print(json.dumps(report, indent=2, default=str), flush=True)
    if report.get("mixed_prompt_id"):
        print("mixed prompt_id; refuse eval comparison", file=sys.stderr)
        return 2
    return 0


def cmd_export_frames(argv: list[str] | None = None) -> int:
    from loop.eval.export import write_frozen_frames

    parser = argparse.ArgumentParser(description="Export frozen-frame bank for Path A GRPO")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--cap", type=int, default=64)
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    dest = write_frozen_frames(run_dirs, args.out, cap=args.cap)
    print(f"wrote {dest}", flush=True)
    return 0


def cmd_cycle(argv: list[str] | None = None) -> int:
    from loop.eval.dpo import sandbox_dpo_pairs, train_dpo
    from loop.eval.export import (
        export_dpo_pairs,
        export_sft_rows,
        inspect_run,
        prompt_hash_report,
        write_dpo_jsonl,
        write_sft_jsonl,
    )
    from loop.eval.sft import sandbox_sft_dataset, train_sft, write_sft_chat_jsonl

    parser = argparse.ArgumentParser(description="inspect -> export-sft/dpo -> optional train-sft then train-dpo")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--source", choices=("vlm", "heuristic", "all"), default="vlm")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    report = inspect_run(run_dirs)
    print(json.dumps(report, indent=2, default=str), flush=True)
    hashes = prompt_hash_report(run_dirs)
    if hashes.get("mixed"):
        print(f"mixed prompt_id {hashes.get('prompt_ids')}; refuse cycle", file=sys.stderr)
        return 2
    rows = export_sft_rows(run_dirs[0], run_dirs=run_dirs, source=args.source)
    ok, reason = sandbox_sft_dataset(rows)
    if not ok:
        print(f"sft sandbox: {reason}", file=sys.stderr)
        return 2
    sft_path = write_sft_jsonl(run_dirs[0], run_dirs=run_dirs, source=args.source)
    dpo_path = write_dpo_jsonl(run_dirs[0], run_dirs=run_dirs, source=args.source)
    chat = write_sft_chat_jsonl(run_dirs[0], run_dirs=run_dirs, source=args.source)
    print(json.dumps({"sft": str(sft_path), "dpo": str(dpo_path), "chat": str(chat), "n": len(rows)}), flush=True)
    pairs = export_dpo_pairs(run_dirs[0], run_dirs=run_dirs, source=args.source)
    ok_dpo, dpo_reason = sandbox_dpo_pairs(pairs)
    if not ok_dpo:
        print(f"dpo sandbox: {dpo_reason}", file=sys.stderr)
        return 2
    if not args.execute:
        return 0
    trained = train_sft(
        run_dirs[0],
        execute=True,
        max_steps=args.max_steps,
        run_dirs=run_dirs,
        source=args.source,
    )
    print(json.dumps({k: v for k, v in trained.items() if k != "agent"}, indent=2, default=str), flush=True)
    if not trained.get("ok"):
        return 2
    dpo = train_dpo(
        run_dirs[0],
        execute=True,
        max_steps=args.max_steps,
        run_dirs=run_dirs,
        source=args.source,
    )
    print(json.dumps({k: v for k, v in dpo.items() if k != "agent"}, indent=2, default=str), flush=True)
    return 0 if dpo.get("ok") else 2


def cmd_train_sft(argv: list[str] | None = None) -> int:
    from loop.eval.sft import DEFAULT_HF_SFT, train_sft

    parser = argparse.ArgumentParser(
        description="Unload Ollama, sandbox high-score traces, Unsloth QLoRA, merge+GGUF from master"
    )
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--execute", action="store_true", help="Run Unsloth QLoRA (needs GPU)")
    parser.add_argument("--hf-model", default=DEFAULT_HF_SFT)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--source", choices=("vlm", "heuristic", "all"), default="vlm")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    publish(job="train-sft", phase="starting", fixture=args.fixture, message="train-sft")
    report = train_sft(
        run_dirs[0],
        execute=args.execute,
        hf_model=args.hf_model,
        max_steps=args.max_steps,
        out_dir=args.out,
        run_dirs=run_dirs,
        source=args.source,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "agent"}, indent=2, default=str), flush=True)
    publish(
        job="train-sft",
        phase="done" if report.get("ok") else "error",
        fixture=args.fixture,
        message=str(report.get("reason") or ""),
    )
    return 0 if report.get("ok") else 2


def cmd_train_dpo(argv: list[str] | None = None) -> int:
    from loop.eval.dpo import train_dpo
    from loop.eval.sft import DEFAULT_HF_SFT

    parser = argparse.ArgumentParser(
        description="Unload Ollama, sandbox same-frame DPO pairs, Unsloth DPOTrainer"
    )
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--execute", action="store_true", help="Run Unsloth DPO (needs GPU)")
    parser.add_argument("--hf-model", default=DEFAULT_HF_SFT)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--source", choices=("vlm", "heuristic", "all"), default="vlm")
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    run_dirs = _run_dirs_from_args(args)
    if not run_dirs:
        print("no run dir", file=sys.stderr)
        return 2
    publish(job="train-dpo", phase="starting", fixture=args.fixture, message="train-dpo")
    report = train_dpo(
        run_dirs[0],
        execute=args.execute,
        hf_model=args.hf_model,
        max_steps=args.max_steps,
        out_dir=args.out,
        run_dirs=run_dirs,
        source=args.source,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "agent"}, indent=2, default=str), flush=True)
    publish(
        job="train-dpo",
        phase="done" if report.get("ok") else "error",
        fixture=args.fixture,
        message=str(report.get("reason") or ""),
    )
    return 0 if report.get("ok") else 2


def cmd_vram_probe(argv: list[str] | None = None) -> int:
    from loop.eval.grpo import vram_probe
    from loop.eval.sft import DEFAULT_HF_SFT

    parser = argparse.ArgumentParser(description="Load 4-bit Qwen3.5-4B + LoRA; print nvidia-smi free")
    parser.add_argument("--hf-model", default=DEFAULT_HF_SFT)
    args = parser.parse_args(argv)
    report = vram_probe(hf_model=args.hf_model)
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0 if report.get("ok") else 2


def cmd_train_grpo(argv: list[str] | None = None) -> int:
    from loop.eval.grpo import train_grpo
    from loop.eval.sft import DEFAULT_HF_SFT

    parser = argparse.ArgumentParser(description="Path A frozen-frame GRPO. Refuses a live window.")
    parser.add_argument("--fixture", default="sts2")
    parser.add_argument("--frames", type=Path, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live", action="store_true", help="rejected; Path A is frozen frames only")
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("--hf-model", default=DEFAULT_HF_SFT)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None)
    _parse_run_dirs(parser)
    args = parser.parse_args(argv)
    if args.live:
        print("refuse live window; use --frames / export-frames", file=sys.stderr)
        return 2
    frames = args.frames
    if frames is None:
        run_dirs = _run_dirs_from_args(args)
        if not run_dirs:
            print("pass --frames or a collect --run-dir", file=sys.stderr)
            return 2
        frames = run_dirs[0] / "frozen_frames.jsonl"
        if not frames.exists():
            from loop.eval.export import write_frozen_frames

            frames = write_frozen_frames(run_dirs, frames)
    publish(job="train-grpo", phase="starting", fixture=args.fixture, message="train-grpo")
    report = train_grpo(
        frames,
        execute=args.execute,
        live=False,
        group_size=args.group_size,
        hf_model=args.hf_model,
        max_steps=args.max_steps,
        out_dir=args.out,
    )
    print(json.dumps(report, indent=2, default=str), flush=True)
    publish(
        job="train-grpo",
        phase="done" if report.get("ok") else "error",
        fixture=args.fixture,
        message=str(report.get("reason") or ""),
    )
    return 0 if report.get("ok") else 2


def cmd_train_rl(argv: list[str] | None = None) -> int:
    from loop.gsm.rl import train_explore_from_run
    from loop.log.store import latest_run_dir

    parser = argparse.ArgumentParser(
        description="Sandbox explore reward, train DoubleDQN on logged pairs"
    )
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--fixture", default="elden")
    parser.add_argument("--steps", type=int, default=64)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    run_dir = args.run_dir or latest_run_dir(args.fixture, project_root())
    if run_dir is None:
        print("no run dir", file=sys.stderr)
        return 2
    publish(job="train-rl", phase="starting", fixture=args.fixture, message="train-rl")
    report = train_explore_from_run(run_dir, dest=args.out, train_steps=args.steps)
    printable = {k: v for k, v in report.items() if k != "agent"}
    print(json.dumps(printable, indent=2, default=str), flush=True)
    publish(
        job="train-rl",
        phase="done" if report.get("ok") else "error",
        fixture=args.fixture,
        message=str(report.get("reason") or ""),
    )
    return 0 if report.get("ok") else 2


def cmd_clear_memory(argv: list[str] | None = None) -> int:
    from loop.memory.clear import clear_loop_memory

    parser = argparse.ArgumentParser(
        description="Wipe persisted collect memory (journal, sqlite recall, ratchet skills)"
    )
    parser.add_argument("--fixture", default="sts2", choices=[*FIXTURE_IDS, "all"])
    parser.add_argument(
        "--all-fixtures",
        action="store_true",
        help="clear every fixture under data/loop (not just --fixture)",
    )
    args = parser.parse_args(argv)
    fixture = None if args.all_fixtures or args.fixture == "all" else args.fixture
    removed = clear_loop_memory(fixture=fixture)
    if not removed:
        print("No persisted memory files found.", flush=True)
        return 0
    for name, paths in removed.items():
        print(f"{name}:", flush=True)
        for path in paths:
            print(f"  removed {path}", flush=True)
    return 0


def cmd_ui(argv: list[str] | None = None) -> int:
    from loop.ui import main as ui_main

    return ui_main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            "usage: python -m loop {perceive,collect,curate,clear-memory,status,analyze,"
            "calibrate-view,inspect-run,benchmark-perceive,benchmark-proposals,"
            "benchmark-effects,benchmark-perception-cost,eval,cycle,export-sft,export-dpo,export-frames,"
            "train-sft,train-dpo,vram-probe,train-grpo,train-rl,smoke,ui} ..."
        )
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "smoke":
        return cmd_smoke(rest)
    if cmd == "perceive":
        return cmd_perceive(rest)
    if cmd == "collect":
        return cmd_collect(rest)
    if cmd == "curate":
        return cmd_curate(rest)
    if cmd in {"clear-memory", "clear_memory"}:
        return cmd_clear_memory(rest)
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "analyze":
        return cmd_analyze(rest)
    if cmd in {"calibrate-view", "calibrate_view"}:
        return cmd_calibrate_view(rest)
    if cmd in {"inspect-run", "inspect_run"}:
        return cmd_inspect_run(rest)
    if cmd in {"benchmark-perceive", "benchmark_perceive"}:
        return cmd_benchmark_perceive(rest)
    if cmd in {"benchmark-proposals", "benchmark_proposals"}:
        return cmd_benchmark_proposals(rest)
    if cmd in {"benchmark-effects", "benchmark_effects"}:
        return cmd_benchmark_effects(rest)
    if cmd in {"benchmark-perception-cost", "benchmark_perception_cost"}:
        return cmd_benchmark_perception_cost(rest)
    if cmd == "eval":
        return cmd_eval(rest)
    if cmd == "cycle":
        return cmd_cycle(rest)
    if cmd in {"export-sft", "export_sft"}:
        return cmd_export_sft(rest)
    if cmd in {"export-dpo", "export_dpo"}:
        return cmd_export_dpo(rest)
    if cmd in {"export-frames", "export_frames"}:
        return cmd_export_frames(rest)
    if cmd in {"train-sft", "train_sft"}:
        return cmd_train_sft(rest)
    if cmd in {"train-dpo", "train_dpo"}:
        return cmd_train_dpo(rest)
    if cmd in {"vram-probe", "vram_probe"}:
        return cmd_vram_probe(rest)
    if cmd in {"train-grpo", "train_grpo"}:
        return cmd_train_grpo(rest)
    if cmd in {"train-rl", "train_rl"}:
        return cmd_train_rl(rest)
    if cmd == "ui":
        return cmd_ui(rest)
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

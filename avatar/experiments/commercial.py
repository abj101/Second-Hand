from __future__ import annotations

import argparse
from pathlib import Path

from avatar.config import load_config
from avatar.constructor.from_traces import skills_from_traces
from avatar.constructor.skills import SkillLibrary
from avatar.eval.logger import JsonlLogger, new_run_dir
from avatar.eval.scoring import plot_skill_reuse
from avatar.experiments.common import add_model_flags, parse_act, selected_models
from avatar.fixtures.commercial import PLATINUM, STS, platinum_available, sts_available
from avatar.harness.cycle import AgentCycle
from avatar.models import make_gateway
from avatar.prompts.library import load_manual


def _run_window_fixture(fixture, task: str, args, cfg) -> Path:
    run_dir = new_run_dir("commercial", fixture.table_name)
    logger = JsonlLogger(run_dir)
    prompt_mode = cfg.get("prompt_mode", "pixels")
    fixture_key = "platinum" if fixture is PLATINUM else "sts"
    manual = load_manual(fixture_key) if prompt_mode == "manual" else ""
    controller = fixture.controller()
    cycle = AgentCycle(
        table_name=fixture.table_name,
        controller=controller,
        fixture=fixture_key,
        logger=logger,
        prompt_mode=prompt_mode,
        manual=manual,
        harness_level="L1",
        paused=False,
    )
    models = selected_models(cfg, args)
    if not models:
        models = [("dry-run", "ollama")]
    try:
        for model_id, provider in models:
            gateway = None if args.skip_models else make_gateway(cfg, model_id, provider)
            cycle.gateway = gateway
            injected = None
            if gateway is None:
                injected = parse_act({"actions": [], "reasoning": "dry-run; window captured"})
            path = run_dir / "frames" / "window.jpg"
            try:
                outcome = cycle.turn(task, injected=injected, model=model_id, save_frame=path)
                executed = outcome.result.executed
            except Exception as exc:  # noqa: BLE001
                executed = []
                print(f"exec_error={exc}", flush=True)
            print(f"{fixture_key} executed={executed}", flush=True)
            if gateway is not None:
                gateway.unload()
    finally:
        controller.close()
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Commercial experiment: Platinum/StS (OS harness)")
    add_model_flags(parser)
    parser.add_argument("--traces", type=Path, default=None, help="JSONL to mine skills from")
    args = parser.parse_args(argv)
    cfg = load_config()
    summary_dir = new_run_dir("constructor", "all")

    if platinum_available():
        print("Platinum window found:", _run_window_fixture(PLATINUM, "walk or talk or open bag", args, cfg))
    else:
        print("Platinum skipped: melonDS window not found (owned ROM required, never in git).")

    if sts_available():
        print("StS window found:", _run_window_fixture(STS, "read intent and play a legal card", args, cfg))
    else:
        print("Slay the Spire skipped: window not found.")

    lib = SkillLibrary(
        max_rewrites=int(cfg.get("constructor_max_rewrites", 3)),
        soft_cap=int(cfg.get("constructor_soft_cap_skills", 8)),
    )
    if args.traces and args.traces.exists():
        lib = skills_from_traces(args.traces, lib)
    else:
        print("No --traces JSONL given; constructor library is empty until Act logs exist.")
    lib.save(summary_dir / "skills.json")
    plot_skill_reuse(lib.stats(), summary_dir / "skill_reuse.png")
    (summary_dir / "constructor_stats.json").write_text(
        __import__("json").dumps(lib.stats(), indent=2),
        encoding="utf-8",
    )
    print(f"Constructor stats -> {summary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

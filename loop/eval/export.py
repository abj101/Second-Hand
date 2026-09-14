from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from loop.agent.decision import ACT_PROMPT_ID, ACT_PROMPT_ID_V2
from loop.agent.prompts import NOTEBOOK_SCHEMA_VERSION
from loop.config import project_root
from loop.eval.outcome import VIEW_MAD_TAU, cv_backed_prompt, view_changed
from loop.log.schema import EpisodeLog, StepLog
from loop.log.store import EpisodeStore
from loop.memory.grounded import (
    eligible_as_chosen,
    eligible_as_positive,
    eligible_as_rejected,
    export_reject_reason,
    export_uncertain_reason,
)
from loop.os.chords import is_look_only


V2_DECISION_SCHEMA = "v2"
# Backward-compatible export fallback; emitted collect rows carry an explicit
# old/new prompt ID via _typed_prompt_id and are never mixed.
V2_PROMPT_ID = ACT_PROMPT_ID


def _typed_prompt_id(step: StepLog) -> str:
    if step.prompt_id:
        return step.prompt_id
    raw = step.turn_snapshot if isinstance(step.turn_snapshot, dict) else {}
    return ACT_PROMPT_ID if "knowledge" in raw else ACT_PROMPT_ID_V2


def _decision_schema(step: StepLog) -> str:
    """Return the training contract version without guessing from legacy fields."""
    return str(getattr(step, "decision_schema", "") or "v1")


def _schema_set(steps: list[StepLog]) -> set[str]:
    return {_decision_schema(step) for step in steps}


def decision_schema_report(run_dirs: list[Path]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for run_dir in run_dirs:
        for step in load_steps(run_dir):
            schema = _decision_schema(step)
            counts[schema] = counts.get(schema, 0) + 1
    versions = sorted(counts)
    return {"versions": versions, "counts": counts, "mixed": len(versions) > 1}


def _v2_intent(step: StepLog) -> dict[str, Any]:
    intent = getattr(step, "intent", {}) or {}
    if not isinstance(intent, dict) or not intent.get("kind"):
        raise ValueError(f"v2 step {step.episode_id}/{step.step_id} has no typed intent")
    return dict(intent)


def _v2_snapshot(step: StepLog) -> dict[str, Any]:
    """Build the text-only ACT input from typed state, never from image/chord data."""
    raw = getattr(step, "turn_snapshot", {}) or {}
    execution = getattr(step, "execution_plan", {}) or {}
    if not raw:
        raw = execution.get("snapshot") if isinstance(execution, dict) else None
    if not isinstance(raw, dict):
        raw = (step.metrics or {}).get("turn_snapshot")
    raw = raw if isinstance(raw, dict) else {}
    allowed = {
        "screen", "turn", "player", "cards", "targets", "controls",
        "last_execution", "knowledge",
    }
    snapshot = {key: raw[key] for key in allowed if key in raw}
    if "screen" not in snapshot:
        snapshot["screen"] = str((step.metrics or {}).get("screen") or "")
    if "player" not in snapshot:
        metrics = step.metrics or {}
        snapshot["player"] = {
            key: metrics.get(key)
            for key in ("hp", "block", "energy")
            if metrics.get(key) is not None
        }
    snapshot["snapshot_id"] = str(getattr(step, "snapshot_id", "") or "")
    # Training must not accidentally smuggle frame-local grounding into ACT.
    return _strip_v2_visual_fields(snapshot)


def _strip_v2_visual_fields(value: Any) -> Any:
    forbidden = {"bbox", "x", "y", "x0", "y0", "x1", "y1", "gx", "gy", "gx2", "gy2", "mark", "chord", "coordinates"}
    if isinstance(value, dict):
        return {
            key: _strip_v2_visual_fields(item)
            for key, item in value.items()
            if key not in forbidden and key not in {"obs", "image", "screenshot", "screenshot_ref"}
        }
    if isinstance(value, list):
        return [_strip_v2_visual_fields(item) for item in value]
    return value


def v2_user_prompt(row_or_step: dict[str, Any] | StepLog) -> str:
    step = row_or_step if isinstance(row_or_step, StepLog) else None
    snapshot = _v2_snapshot(step) if step is not None else _strip_v2_visual_fields(dict(row_or_step.get("snapshot") or {}))
    return json.dumps(
        {"snapshot": snapshot, "response_schema": "ActionIntent v2"},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _looks_elden_run(path: Path) -> bool:
    name = path.name.lower()
    return path.is_dir() and "elden" in name and (path / "episodes.jsonl").exists()


def iter_elden_runs(root: Path | None = None) -> list[Path]:
    base = (root or project_root()) / "runs"
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if _looks_elden_run(p))


def load_steps(run_dir: Path) -> list[StepLog]:
    store = EpisodeStore(run_dir)
    return [StepLog.model_validate(row) for row in store.read_steps()]


def _frame(run_dir: Path, ref: str | None) -> Image.Image | None:
    if not ref:
        return None
    path = run_dir / ref
    if not path.exists():
        return None
    return Image.open(path).convert("RGB")


def calibrate_view(
    run_dirs: list[Path] | None = None,
    *,
    mad_tau: float = VIEW_MAD_TAU,
) -> dict[str, Any]:
    """Score look-only before/after JPEGs. Prefer 20260825T050750Z / 055445Z."""
    runs = run_dirs if run_dirs is not None else iter_elden_runs()
    rows: list[dict[str, Any]] = []
    preferred = [p for p in runs if any(tag in p.name for tag in ("050750Z", "055445Z"))]
    scan = preferred or runs
    for run_dir in scan:
        for step in load_steps(run_dir):
            if not is_look_only(step.action):
                continue
            before = _frame(run_dir, step.observation.screenshot_ref)
            after = _frame(run_dir, step.observation.after_screenshot_ref)
            if before is None or after is None:
                continue
            changed, mad, shift = view_changed(
                before, after, stick_r=step.action.stick_r, mad_tau=mad_tau
            )
            rows.append(
                {
                    "run": run_dir.name,
                    "episode_id": step.episode_id,
                    "step_id": step.step_id,
                    "stick_r": step.action.stick_r,
                    "logged_pixel_changed": step.result.pixel_changed,
                    "view_changed": changed,
                    "view_mad": round(mad, 4),
                    "column_shift": shift,
                }
            )
    mads = [row["view_mad"] for row in rows]
    n_pos = sum(1 for row in rows if row["view_changed"])
    return {
        "runs": [str(p) for p in scan],
        "look_only": len(rows),
        "view_changed": n_pos,
        "mad_tau": mad_tau,
        "mad_mean": (sum(mads) / len(mads)) if mads else None,
        "rows": rows,
        "note": (
            "Calibrate τ on look-only frames from 20260825T050750Z / 055445Z. "
            "Do not SFT on runs after look→walk (zero stick_r_only)."
        ),
    }


def load_episodes(run_dir: Path) -> list[EpisodeLog]:
    store = EpisodeStore(run_dir)
    return [EpisodeLog.model_validate(row) for row in store.read_episodes()]


def _determinate_success_rate(episodes: list[EpisodeLog]) -> float | None:
    determinate = [episode for episode in episodes if episode.outcome in {"win", "loss"}]
    if not determinate:
        return None
    return sum(1 for episode in determinate if episode.outcome == "win") / len(determinate)


def _sts2_reward(prev: dict[str, Any], step: StepLog) -> float:
    from loop.reward.sts import turn_reward

    chord = step.proposed or step.action
    return turn_reward(prev, dict(step.metrics or {}), chord, step.result, terminal=step.terminal)


def _sts2_reward_parts(prev: dict[str, Any], step: StepLog) -> dict[str, float]:
    from loop.reward.sts import turn_reward_parts

    chord = step.proposed or step.action
    return turn_reward_parts(prev, dict(step.metrics or {}), chord, step.result, terminal=step.terminal)


def _heuristic_source(step: StepLog) -> str:
    policy = str(step.policy_id or "")
    if policy.startswith("sts2-heuristic"):
        return "heuristic"
    return "vlm"


def _keep_source(step: StepLog, source: str) -> bool:
    if source not in {"vlm", "heuristic", "all"}:
        raise ValueError(f"source must be vlm|heuristic|all, got {source!r}")
    if source == "all":
        return True
    return _heuristic_source(step) == source


def state_key(metrics: dict[str, Any] | None) -> tuple[Any, ...]:
    metrics = metrics or {}
    return (
        str(metrics.get("screen") or ""),
        metrics.get("hp"),
        metrics.get("energy"),
        metrics.get("floor"),
        metrics.get("block"),
    )


def memory_key(step: StepLog) -> str:
    """Rendered Notebook snapshot for DPO pairing (Phase 8)."""
    snap = str(getattr(step, "notebook_snapshot", "") or "").strip()
    if snap:
        return snap
    return ""


def comparable_state(step: StepLog, other: StepLog) -> bool:
    if state_key(step.metrics) != state_key(other.metrics):
        return False
    left = memory_key(step)
    right = memory_key(other)
    if left or right:
        return left == right
    return True


def notebook_schema_report(run_dirs: list[Path]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for run_dir in run_dirs:
        for step in load_steps(run_dir):
            if step.fixture != "sts2":
                continue
            ver = str(getattr(step, "notebook_schema", "") or NOTEBOOK_SCHEMA_VERSION)
            counts[ver] = counts.get(ver, 0) + 1
    if not counts:
        return {"versions": [], "mixed": False, "current": NOTEBOOK_SCHEMA_VERSION}
    versions = sorted(counts)
    return {
        "versions": versions,
        "counts": counts,
        "mixed": len(versions) > 1,
        "current": NOTEBOOK_SCHEMA_VERSION,
    }


def _obs_ref(step: StepLog) -> str:
    return step.observation.vlm_screenshot_ref or step.observation.screenshot_ref or ""


def majority_prompt(steps: list[StepLog]) -> tuple[str, dict[str, int], int]:
    counts: dict[str, int] = {}
    for step in steps:
        if step.fixture == "sts2" and step.prompt_id:
            counts[step.prompt_id] = counts.get(step.prompt_id, 0) + 1
    if not counts:
        return "", {}, 0
    keep = max(counts, key=counts.get)
    dropped = sum(n for key, n in counts.items() if key != keep)
    return keep, counts, dropped


def prompt_hash_report(run_dirs: list[Path]) -> dict[str, Any]:
    steps: list[StepLog] = []
    for run_dir in run_dirs:
        steps.extend(load_steps(run_dir))
    keep, counts, dropped = majority_prompt(steps)
    return {
        "keep_prompt": keep,
        "counts": counts,
        "dropped": dropped,
        "mixed": len(counts) > 1,
        "prompt_ids": sorted(counts),
    }


def export_gate_report(run_dirs: list[Path]) -> dict[str, Any]:
    """Summarize why baseline-style uncertain rows are excluded from training."""
    excluded: list[dict[str, Any]] = []
    eligible = 0
    for run_dir in run_dirs:
        for step in load_steps(run_dir):
            if step.fixture != "sts2":
                continue
            reason = export_reject_reason(step)
            if reason:
                excluded.append(
                    {
                        "run_dir": str(run_dir),
                        "episode_id": step.episode_id,
                        "step_id": step.step_id,
                        "category": reason,
                    }
                )
            elif eligible_as_positive(step):
                eligible += 1
    by_category: dict[str, int] = {}
    for row in excluded:
        cat = str(row["category"])
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "eligible_positive": eligible,
        "excluded_n": len(excluded),
        "by_category": by_category,
        "excluded": excluded,
    }


def export_sft_rows(
    run_dir: Path,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> list[dict[str, Any]]:
    """High-score traces: executed as proposed and (view_changed | CV prompt_cleared).

    STS2 keeps HUD-progress clicks (turn_reward > 0), including fail-episode Continue.
    Default `source="vlm"` drops sts2-heuristic* rows; `heuristic` drops VLM; `all` keeps both.
    """
    dirs = list(run_dirs) if run_dirs is not None else [run_dir]
    all_steps: list[tuple[Path, StepLog]] = []
    fail_ids: set[str] = set()
    for path in dirs:
        fail_ids.update(ep.episode_id for ep in load_episodes(path) if ep.terminal == "fail")
        for step in load_steps(path):
            if not _keep_source(step, source):
                continue
            all_steps.append((path, step))
    schemas = _schema_set([step for _, step in all_steps])
    # A training file is one protocol.  Do not silently train v1 and v2 in the
    # same artifact, even when prompt hashes happen to be equal.
    if len(schemas) > 1:
        return []
    if V2_DECISION_SCHEMA in schemas:
        prompt_ids = {
            _typed_prompt_id(step) for _, step in all_steps if step.fixture == "sts2"
        }
        if len(prompt_ids) > 1:
            return []
        out: list[dict[str, Any]] = []
        for path, step in all_steps:
            if step.fixture != "sts2" or not step.result.executed_as_proposed:
                continue
            if export_reject_reason(step):
                continue
            if (step.metrics or {}).get("illegal_chord"):
                continue
            try:
                intent = _v2_intent(step)
            except ValueError:
                continue
            prev = dict((step.metrics or {}).get("previous_metrics") or {})
            reward = _sts2_reward(prev, step)
            if reward <= 0 and not any(
                (step.metrics or {}).get(key) for key in ("view_changed", "prompt_cleared", "pixel_changed")
            ):
                continue
            out.append(_v2_sft_row(step, intent, path, reward))
        return out
    keep_prompt, _, _ = majority_prompt([step for _, step in all_steps])
    by_ep: dict[tuple[str, str], list[tuple[Path, StepLog]]] = {}
    for path, step in all_steps:
        by_ep.setdefault((str(path), step.episode_id), []).append((path, step))
    out: list[dict[str, Any]] = []
    for rows in by_ep.values():
        ordered = sorted(rows, key=lambda item: item[1].step_id)
        for i, (path, step) in enumerate(ordered):
            result = step.result
            chord = step.proposed or step.action
            if step.fixture == "sts2":
                if keep_prompt and step.prompt_id and step.prompt_id != keep_prompt:
                    continue
                if not result.executed_as_proposed:
                    continue
                if export_reject_reason(step):
                    continue
                if (step.metrics or {}).get("illegal_chord"):
                    continue
                prev = dict(ordered[i - 1][1].metrics or {}) if i else {}
                reward = _sts2_reward(prev, step)
                if reward <= 0:
                    continue
                if not (
                    chord.mark is not None
                    or (chord.click_x is not None and chord.click_y is not None)
                    or chord.name
                ):
                    continue
                row = _sft_row(
                    step,
                    chord,
                    obs=step.observation.vlm_screenshot_ref or step.observation.screenshot_ref,
                    turn_reward=reward,
                    run_dir=path,
                )
                out.append(row)
                continue
            if step.episode_id in fail_ids:
                continue
            if not result.executed_as_proposed:
                continue
            prompt_ok = result.prompt_cleared and cv_backed_prompt(step)
            if not (result.view_changed or prompt_ok):
                continue
            out.append(_sft_row(step, chord, obs=step.observation.screenshot_ref, run_dir=path))
    return out


def _v2_sft_row(step: StepLog, intent: dict[str, Any], run_dir: Path, reward: float) -> dict[str, Any]:
    """Text-only v2 row.  Deliberately has no obs/chord/mark/coordinate fields."""
    return {
        "episode_id": step.episode_id,
        "step_id": step.step_id,
        "fixture": step.fixture,
        "decision_schema": V2_DECISION_SCHEMA,
        "snapshot_id": str(getattr(step, "snapshot_id", "") or ""),
        "prompt_id": _typed_prompt_id(step),
        "system": "Return exactly one ActionIntent JSON object matching the supplied schema. No prose.",
        "user": v2_user_prompt(step),
        "snapshot": _v2_snapshot(step),
        "intent": intent,
        "outcome": {"turn_reward": reward, "executed": True},
        "source": _heuristic_source(step),
        "run_dir": str(run_dir),
    }


def _sft_row(
    step: StepLog,
    chord: Any,
    *,
    obs: str | None,
    turn_reward: float | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    result = step.result
    outcome = {
        "view_changed": result.view_changed,
        "prompt_cleared": result.prompt_cleared,
        "stall": result.stall,
        "pixel_changed": result.pixel_changed,
    }
    if turn_reward is not None:
        outcome["turn_reward"] = turn_reward
    row = {
        "episode_id": step.episode_id,
        "step_id": step.step_id,
        "fixture": step.fixture,
        "obs": obs or "",
        "thought": step.thought,
        "chord": chord.model_dump(),
        "hud": dict(step.metrics or {}),
        "prompt_id": step.prompt_id,
        "notebook_schema": getattr(step, "notebook_schema", "") or NOTEBOOK_SCHEMA_VERSION,
        "user_prompt_text": getattr(step, "user_prompt_text", "") or "",
        "notebook_snapshot": getattr(step, "notebook_snapshot", "") or "",
        "policy_id": step.policy_id,
        "source": _heuristic_source(step),
        "outcome": outcome,
    }
    if run_dir is not None:
        row["run_dir"] = str(run_dir)
    return row


def _is_hover_promote(proposed: Any, executed: Any) -> bool:
    return bool(getattr(proposed, "hover", False)) and not bool(getattr(executed, "hover", False))


def _export_v2_dpo_pairs(dirs: list[Path], source: str) -> list[dict[str, Any]]:
    """Pair typed intents using typed state only; no frame references are emitted."""
    out: list[dict[str, Any]] = []
    for path in dirs:
        steps = [
            step for step in load_steps(path)
            if step.fixture == "sts2" and _keep_source(step, source)
        ]
        by_ep: dict[str, list[StepLog]] = {}
        for step in steps:
            by_ep.setdefault(step.episode_id, []).append(step)
        for rows in by_ep.values():
            ordered = sorted(rows, key=lambda row: row.step_id)
            scored: list[tuple[StepLog, float, dict[str, Any]]] = []
            for i, step in enumerate(ordered):
                try:
                    intent = _v2_intent(step)
                except ValueError:
                    continue
                prev = dict((step.metrics or {}).get("previous_metrics") or {})
                scored.append((step, _sts2_reward(prev, step), intent))
            for i, (chosen_step, chosen_reward, chosen) in enumerate(scored):
                if not eligible_as_chosen(chosen_step, reward=chosen_reward):
                    continue
                reject: tuple[StepLog, float, dict[str, Any]] | None = None
                for j, candidate in enumerate(scored):
                    if i == j:
                        continue
                    rejected_step, rejected_reward, rejected = candidate
                    if _typed_prompt_id(rejected_step) != _typed_prompt_id(chosen_step):
                        continue
                    if not eligible_as_rejected(
                        rejected_step,
                        chosen=chosen_step,
                        reward=rejected_reward,
                    ):
                        continue
                    same_snapshot = bool(
                        chosen_step.snapshot_id
                        and chosen_step.snapshot_id == rejected_step.snapshot_id
                    )
                    if rejected_reward <= 0 and (same_snapshot or comparable_state(chosen_step, rejected_step)):
                        reject = candidate
                        if same_snapshot:
                            break
                if reject is None:
                    continue
                rejected_step, rejected_reward, rejected = reject
                out.append({
                    "episode_id": chosen_step.episode_id,
                    "step_id": chosen_step.step_id,
                    "decision_schema": V2_DECISION_SCHEMA,
                    "snapshot_id": chosen_step.snapshot_id,
                    "prompt_id": _typed_prompt_id(chosen_step),
                    "user": v2_user_prompt(chosen_step),
                    "pair_type": "state_bound",
                    "same_frame": bool(chosen_step.snapshot_id and chosen_step.snapshot_id == rejected_step.snapshot_id),
                    "chosen": chosen,
                    "rejected": rejected,
                    "chosen_reward": chosen_reward,
                    "rejected_reward": rejected_reward,
                    "run_dir": str(path),
                })
    return out


def export_dpo_pairs(
    run_dir: Path,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> list[dict[str, Any]]:
    """Same-frame progress vs no-op/danger. Override pairs are same-step by construction."""
    from loop.os.chords import is_empty_chord
    from loop.reward.sts import danger_turn_action

    dirs = list(run_dirs) if run_dirs is not None else [run_dir]
    pairs: list[dict[str, Any]] = []
    all_steps: list[StepLog] = []
    for path in dirs:
        for step in load_steps(path):
            if _keep_source(step, source):
                all_steps.append(step)
    schemas = _schema_set(all_steps)
    if len(schemas) > 1:
        return []
    if V2_DECISION_SCHEMA in schemas:
        return _export_v2_dpo_pairs(dirs, source)
    keep_prompt, _, _ = majority_prompt(all_steps)
    for path in dirs:
        by_ep: dict[str, list[StepLog]] = {}
        for step in load_steps(path):
            if step.fixture != "sts2":
                continue
            if not _keep_source(step, source):
                continue
            if keep_prompt and step.prompt_id and step.prompt_id != keep_prompt:
                continue
            by_ep.setdefault(step.episode_id, []).append(step)
        for rows in by_ep.values():
            ordered = sorted(rows, key=lambda row: row.step_id)
            scored: list[tuple[StepLog, float, Any]] = []
            for i, step in enumerate(ordered):
                chord = step.action if not step.result.executed_as_proposed else (step.proposed or step.action)
                prev = dict(ordered[i - 1].metrics or {}) if i else {}
                reward = _sts2_reward(prev, step)
                scored.append((step, reward, chord))
            for i, (step, reward, chord) in enumerate(scored):
                proposed = step.proposed
                executed = step.action
                if (
                    proposed is not None
                    and not step.result.executed_as_proposed
                    and not _is_hover_promote(proposed, executed)
                ):
                    empty = is_empty_chord(proposed)
                    danger = danger_turn_action(proposed, dict(step.metrics or {}))
                    illegal = bool((step.metrics or {}).get("illegal_chord"))
                    if empty or danger or illegal:
                        if export_uncertain_reason(step) is None:
                            pairs.append(
                                {
                                    "episode_id": step.episode_id,
                                    "step_id": step.step_id,
                                    "obs": _obs_ref(step),
                                    "run_dir": str(path),
                                    "prompt_id": step.prompt_id,
                                    "user_prompt_text": getattr(step, "user_prompt_text", "") or "",
                                    "pair_type": "illegal_chord" if illegal else "safety_override",
                                    "same_frame": True,
                                    "chosen": executed.model_dump(),
                                    "rejected": proposed.model_dump(),
                                    "chosen_reward": reward,
                                    "rejected_reward": 0.0 if empty else -0.5,
                                }
                            )
                if not eligible_as_chosen(step, reward=reward):
                    continue
                obs = _obs_ref(step)
                reject = None
                reject_r = 0.0
                reject_same_frame = False
                for j, (other, other_r, other_chord) in enumerate(scored):
                    if i == j:
                        continue
                    other_obs = _obs_ref(other)
                    bad = other_r <= 0 or danger_turn_action(
                        other.proposed or other.action, dict(other.metrics or {})
                    )
                    if not bad:
                        continue
                    same_frame = other_obs == obs and bool(obs)
                    same_state = comparable_state(step, other)
                    if same_frame or same_state:
                        if not eligible_as_rejected(other, chosen=step, reward=other_r):
                            continue
                        reject = other_chord
                        reject_r = other_r
                        reject_same_frame = same_frame
                        if same_frame:
                            break
                if reject is None:
                    continue
                pairs.append(
                    {
                        "episode_id": step.episode_id,
                        "step_id": step.step_id,
                        "obs": obs,
                        "run_dir": str(path),
                        "prompt_id": step.prompt_id,
                        "user_prompt_text": getattr(step, "user_prompt_text", "") or "",
                        "pair_type": "state_bound",
                        "same_frame": reject_same_frame,
                        "chosen": chord.model_dump(),
                        "rejected": reject.model_dump(),
                        "chosen_reward": reward,
                        "rejected_reward": reject_r,
                    }
                )
    return pairs


def write_dpo_jsonl(
    run_dir: Path,
    dest: Path | None = None,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> Path:
    dest = dest or (run_dir / "dpo_pairs.jsonl")
    rows = export_dpo_pairs(run_dir, run_dirs=run_dirs, source=source)
    dest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return dest


def turn_run_metrics(run_dir: Path) -> dict[str, Any]:
    """Held-out STS2 eval counters. Frozen harness + prompt_id."""
    steps = [row for row in load_steps(run_dir) if row.fixture == "sts2"]
    episodes = [row for row in load_episodes(run_dir) if row.fixture == "sts2"]
    n = len(steps)
    screens = [str((row.metrics or {}).get("screen") or "") for row in steps]
    two_click = sum(
        1
        for row in steps
        if row.action.target is not None
        or (row.action.click_x2 is not None and row.action.click_y2 is not None)
    )
    escape = sum(1 for row in steps if (row.action.name or "").lower() == "escape")
    illegal_chords = sum(1 for row in steps if (row.metrics or {}).get("illegal_chord"))
    unresolved_steps = sum(
        1 for row in steps if int((row.metrics or {}).get("unresolved_count") or 0) > 0
    )
    recheck_steps = sum(1 for row in steps if int((row.metrics or {}).get("recheck_count") or 0) > 0)
    affordance_hits = sum(1 for row in steps if (row.metrics or {}).get("affordance_hit"))
    false_fail = 0
    for row in steps:
        metrics = row.metrics or {}
        if row.terminal == "fail" and str(metrics.get("screen") or "") == "pause":
            false_fail += 1
    floors = [
        float(row.metrics["floor"])
        for row in steps
        if isinstance((row.metrics or {}).get("floor"), (int, float))
    ]
    prompt_ids = sorted({row.prompt_id for row in steps if row.prompt_id})
    hashes = prompt_hash_report([run_dir])
    return {
        "steps": n,
        "episodes": len(episodes),
        "tutorial_clear": int("tutorial" in screens and any(s not in {"", "tutorial"} for s in screens)),
        "title_to_map": int("title" in screens and "map" in screens),
        "combats_started": int("combat" in screens),
        "mean_floor": (sum(floors) / len(floors)) if floors else None,
        "false_fail": false_fail,
        "two_click_rate": (two_click / n) if n else 0.0,
        "escape_rate": (escape / n) if n else 0.0,
        "illegal_chord_rate": (illegal_chords / n) if n else 0.0,
        "unresolved_rate": (unresolved_steps / n) if n else 0.0,
        "recheck_rate": (recheck_steps / n) if n else 0.0,
        "affordance_detection_rate": (affordance_hits / n) if n else 0.0,
        "success_rate": _determinate_success_rate(episodes),
        "prompt_ids": prompt_ids,
        "mixed_prompt_id": bool(hashes["mixed"]),
    }


_COMPARE_KEYS = (
    "tutorial_clear",
    "title_to_map",
    "combats_started",
    "mean_floor",
    "false_fail",
    "two_click_rate",
    "success_rate",
    "escape_rate",
    "illegal_chord_rate",
    "unresolved_rate",
    "recheck_rate",
    "affordance_detection_rate",
)


def _metric_delta(candidate: Any, baseline: Any) -> Any:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def compare_turn_runs(baseline: Path, candidate: Path) -> dict[str, Any]:
    """Candidate minus baseline for held-out STS2 counters. No live window."""
    base = turn_run_metrics(baseline)
    cand = turn_run_metrics(candidate)
    if base.get("mixed_prompt_id"):
        return {
            "ok": False,
            "reason": "mixed prompt_id in baseline; refuse eval comparison",
            "baseline": base,
            "candidate": cand,
        }
    if cand.get("mixed_prompt_id"):
        return {
            "ok": False,
            "reason": "mixed prompt_id in candidate; refuse eval comparison",
            "baseline": base,
            "candidate": cand,
        }
    base_keep = str(prompt_hash_report([baseline]).get("keep_prompt") or "")
    cand_keep = str(prompt_hash_report([candidate]).get("keep_prompt") or "")
    if base_keep != cand_keep:
        return {
            "ok": False,
            "reason": (
                f"majority prompt_id differs between runs "
                f"baseline={base_keep!r} candidate={cand_keep!r}"
            ),
            "baseline": base,
            "candidate": cand,
        }
    deltas = {key: _metric_delta(cand.get(key), base.get(key)) for key in _COMPARE_KEYS}
    return {
        "ok": True,
        "reason": "ok",
        "baseline": base,
        "candidate": cand,
        "deltas": deltas,
        "prompt_id": base_keep,
    }


_NONCANONICAL_SNAPSHOT_ID_RE = re.compile(r"^(object:|mark_|mk_)\d+$")
_DUPLICATE_TARGET_IOU = 0.45


def is_noncanonical_snapshot_id(value: str) -> bool:
    """True for mark-derived / prompt-instance ids that must not be executable."""
    from loop.agent.entities import is_prompt_instance_id

    raw = str(value or "").strip()
    if not raw:
        return False
    if _NONCANONICAL_SNAPSHOT_ID_RE.match(raw):
        return True
    return is_prompt_instance_id(raw)


def _snapshot_bbox(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = item.get("bbox")
    if isinstance(raw, dict):
        try:
            return (float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"]))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        except (TypeError, ValueError):
            return None
    return None


def _box_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _host_enemy_count_from_metrics(metrics: dict[str, Any] | None) -> int | None:
    stats = metrics if isinstance(metrics, dict) else {}
    enemies = stats.get("enemies")
    if isinstance(enemies, (list, tuple)):
        return len(enemies)
    raw = stats.get("enemy_count")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    return None


def perception_hygiene_from_steps(steps: list[StepLog]) -> dict[str, Any]:
    """Id / bbox / confidence audits plus router and perception-source histograms."""
    noncanonical = 0
    duplicate_steps = 0
    duplicate_named = 0
    host_mismatch = 0
    conf_oor = 0
    router: dict[str, int] = {}
    sources: dict[str, int] = {}
    for step in steps:
        snap = step.turn_snapshot if isinstance(step.turn_snapshot, dict) else {}
        cards = snap.get("cards") or []
        targets = snap.get("targets") or []
        if isinstance(cards, list):
            for card in cards:
                if isinstance(card, dict) and is_noncanonical_snapshot_id(str(card.get("card_id") or "")):
                    noncanonical += 1
        boxes: list[tuple[float, float, float, float]] = []
        named: list[tuple[str, tuple[float, float, float, float]]] = []
        if isinstance(targets, list):
            for target in targets:
                if not isinstance(target, dict):
                    continue
                if is_noncanonical_snapshot_id(str(target.get("target_id") or "")):
                    noncanonical += 1
                conf = target.get("identity_confidence")
                if isinstance(conf, (int, float)) and not (0.0 <= float(conf) <= 1.0):
                    conf_oor += 1
                box = _snapshot_bbox(target)
                if box is not None:
                    boxes.append(box)
                    name = " ".join(str(target.get("name") or "").lower().split())
                    if name:
                        named.append((name, box))
        if any(
            _box_iou(boxes[i], boxes[j]) >= _DUPLICATE_TARGET_IOU
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ):
            duplicate_steps += 1
        if any(
            named[i][0] == named[j][0] and _box_iou(named[i][1], named[j][1]) >= _DUPLICATE_TARGET_IOU
            for i in range(len(named))
            for j in range(i + 1, len(named))
        ):
            duplicate_named += 1
        host_count = _host_enemy_count_from_metrics(step.metrics if isinstance(step.metrics, dict) else None)
        if host_count is not None and isinstance(targets, list) and len(targets) != host_count:
            host_mismatch += 1
        reason = str(step.router_reason or "")
        router[reason] = router.get(reason, 0) + 1
        src = str(step.perception_source or "")
        sources[src] = sources.get(src, 0) + 1
    return {
        "noncanonical_target_ids": noncanonical,
        "duplicate_target_bboxes": duplicate_steps,
        "duplicate_named_targets": duplicate_named,
        "host_count_mismatch": host_mismatch,
        "confidence_out_of_range": conf_oor,
        "router_reason": router,
        "perception_source": sources,
    }


def inspect_run(run_dirs: list[Path]) -> dict[str, Any]:
    """SFT/DPO yield, prompt hashes, screen mix, transition vs combat reward mass."""
    hashes = prompt_hash_report(run_dirs)
    schema = notebook_schema_report(run_dirs)
    sft = export_sft_rows(run_dirs[0], run_dirs=run_dirs, source="all") if run_dirs else []
    dpo = export_dpo_pairs(run_dirs[0], run_dirs=run_dirs, source="all") if run_dirs else []
    screens: dict[str, int] = {}
    mix = {"transition": 0.0, "combat": 0.0, "transition_raw": 0.0, "n": 0}
    metrics_rows: list[dict[str, Any]] = []
    all_steps: list[StepLog] = []
    for path in run_dirs:
        metrics_rows.append({"run": str(path), **turn_run_metrics(path)})
        steps = load_steps(path)
        all_steps.extend(steps)
        by_ep: dict[str, list[StepLog]] = {}
        for step in steps:
            if step.fixture != "sts2":
                continue
            by_ep.setdefault(step.episode_id, []).append(step)
            screen = str((step.metrics or {}).get("screen") or "") or "(none)"
            screens[screen] = screens.get(screen, 0) + 1
        for rows in by_ep.values():
            ordered = sorted(rows, key=lambda row: row.step_id)
            for i, step in enumerate(ordered):
                prev = dict(ordered[i - 1].metrics or {}) if i else {}
                parts = _sts2_reward_parts(prev, step)
                mix["transition"] += float(parts.get("transition") or 0)
                mix["combat"] += float(parts.get("combat") or 0)
                mix["transition_raw"] += float(parts.get("transition_raw") or 0)
                mix["n"] += 1
    sources: dict[str, int] = {}
    for row in sft:
        src = str(row.get("source") or "vlm")
        sources[src] = sources.get(src, 0) + 1
    pair_types: dict[str, int] = {}
    for row in dpo:
        kind = str(row.get("pair_type") or "state_bound")
        pair_types[kind] = pair_types.get(kind, 0) + 1
    hygiene = perception_hygiene_from_steps(all_steps)
    return {
        "runs": [str(path) for path in run_dirs],
        "sft_n": len(sft),
        "dpo_n": len(dpo),
        "sft_sources": sources,
        "dpo_pair_types": pair_types,
        "screens": screens,
        "reward_mix": mix,
        "prompt": hashes,
        "notebook_schema": schema,
        "metrics": metrics_rows,
        **hygiene,
    }


def export_frozen_frames(
    run_dirs: list[Path],
    *,
    cap: int = 64,
) -> list[dict[str, Any]]:
    """Curate frozen (image, metrics, prompt) rows for Path A group scoring."""
    from loop.agent.prompts import SYSTEM_TURN_NO_SKILL
    from loop.eval.sft import SFT_USER_TURN

    hashes = prompt_hash_report(run_dirs)
    keep = str(hashes.get("keep_prompt") or "")
    buckets: dict[str, list[dict[str, Any]]] = {"menu": [], "combat": [], "low_hp": [], "other": []}
    for path in run_dirs:
        for step in load_steps(path):
            if step.fixture != "sts2":
                continue
            if keep and step.prompt_id and step.prompt_id != keep:
                continue
            obs = _obs_ref(step)
            if not obs:
                continue
            metrics = dict(step.metrics or {})
            screen = str(metrics.get("screen") or "")
            hp = metrics.get("hp")
            hp_max = metrics.get("hp_max")
            if screen == "combat" and isinstance(hp, (int, float)) and isinstance(hp_max, (int, float)):
                bucket = "low_hp" if hp_max and float(hp) / float(hp_max) <= 0.4 else "combat"
            elif screen in {"title", "select", "tutorial", "map", "pause"}:
                bucket = "menu"
            elif screen == "combat":
                bucket = "combat"
            else:
                bucket = "other"
            buckets[bucket].append(
                {
                    "run_dir": str(path),
                    "episode_id": step.episode_id,
                    "step_id": step.step_id,
                    "obs": obs,
                    "metrics": metrics,
                    "prompt_id": step.prompt_id,
                    "system": SYSTEM_TURN_NO_SKILL.strip(),
                    "user": SFT_USER_TURN,
                    "screen": screen,
                    "bucket": bucket,
                }
            )
    per = max(1, cap // 4)
    out: list[dict[str, Any]] = []
    for key in ("menu", "combat", "low_hp", "other"):
        out.extend(buckets[key][:per])
    return out[:cap]


def write_frozen_frames(
    run_dirs: list[Path],
    dest: Path | None = None,
    *,
    cap: int = 64,
) -> Path:
    dest = dest or (run_dirs[0] / "frozen_frames.jsonl")
    rows = export_frozen_frames(run_dirs, cap=cap)
    dest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return dest


def train_row(step: StepLog) -> dict[str, Any]:
    action = step.action
    gx = gy = None
    if action.click_x is not None and action.click_y is not None:
        gx = int(round(float(action.click_x) * 1000))
        gy = int(round(float(action.click_y) * 1000))
    metrics = dict(step.metrics or {})
    hud = {
        key: metrics.get(key)
        for key in (
            "hp",
            "hp_max",
            "health_frac",
            "gold",
            "floor",
            "act",
            "energy",
            "energy_max",
            "block",
            "screen",
            "choice",
            "fail_screen",
            "win_screen",
            "ocr",
            "aim_marks",
        )
        if metrics.get(key) not in (None, "")
    }
    return {
        "episode_id": step.episode_id,
        "step_id": step.step_id,
        "ts": step.ts,
        "fixture": step.fixture,
        "thought": step.thought,
        "choice": metrics.get("choice"),
        "screen": metrics.get("screen"),
        "hud": hud,
        "action": {
            "mark": action.mark,
            "gx": gx,
            "gy": gy,
            "name": action.name,
            "click_x": action.click_x,
            "click_y": action.click_y,
        },
        "pixel_changed": step.result.pixel_changed,
        "pixel_mad": step.result.pixel_mad,
        "executed": list(step.result.executed or []),
        "terminal": step.terminal,
        "success": step.success,
        "score_delta": step.score_delta,
        "return_to_go": step.return_to_go,
        "obs": step.observation.screenshot_ref,
        "vlm": step.observation.vlm_screenshot_ref,
        "after": step.observation.after_screenshot_ref,
        "policy_id": step.policy_id,
    }


def write_train_jsonl(run_dir: Path, dest: Path | None = None) -> Path:
    dest = dest or (run_dir / "train.jsonl")
    rows = [train_row(step) for step in load_steps(run_dir)]
    dest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return dest


def write_sft_jsonl(
    run_dir: Path,
    dest: Path | None = None,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> Path:
    dest = dest or (run_dir / "sft_traces.jsonl")
    rows = export_sft_rows(run_dir, run_dirs=run_dirs, source=source)
    dest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return dest

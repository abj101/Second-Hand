from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from avatar.eval.logger import JsonlLogger
from avatar.eval.outcome import compact_see, compute_outcome
from avatar.harness.capture import EncodedFrame
from avatar.harness.executor import CycleResult, OsController
from avatar.harness.profile import SettleResult, mean_absdiff, next_skip_streak, settings_from_frame
from avatar.prompts.library import (
    act_system,
    act_user,
    action_design_system,
    action_design_user,
    brain_system,
    brain_user,
    historical_summary_system,
    historical_summary_user,
    previous_task_system,
    previous_task_user,
    scout_user,
    see_system,
    see_user,
)
from avatar.schema import (
    ActResponse,
    ActionDesignReflection,
    CaptureSettings,
    HistoricalSummary,
    PreviousTaskReflection,
    SeeResponse,
    TurnLog,
    TurnOutcomeRecord,
)


def filter_banned(plan: ActResponse, banned: set[str] | None) -> ActResponse:
    if not banned:
        return plan
    kept = [step for step in plan.actions if step.name not in banned]
    clear_skill = bool(plan.invoke_skill and plan.invoke_skill in banned)
    clear_gsm = bool(plan.invoke_gsm and plan.invoke_gsm in banned)
    if len(kept) == len(plan.actions) and not clear_skill and not clear_gsm:
        return plan
    updated = plan.model_copy(deep=True)
    updated.actions = kept
    if clear_skill:
        updated.invoke_skill = None
    if clear_gsm:
        updated.invoke_gsm = None
    if not kept and not updated.invoke_skill and not updated.invoke_gsm:
        note = "All planned actions were banned."
        updated.reasoning = f"{plan.reasoning} {note}".strip() if plan.reasoning else note
    return updated


def _occupancy_key(boxes: list[Any], *, fixture: str = "") -> str:
    from avatar.gsm.rule.policy import occupancy_fingerprint

    cols, rows = (10, 20) if fixture == "tetris" else (4, 4)
    try:
        return occupancy_fingerprint(list(boxes or []), cols=cols, rows=rows)
    except Exception:  # noqa: BLE001 — fingerprint must not abort a turn
        cells: list[tuple[float, float]] = []
        for box in boxes:
            try:
                cells.append((round(float(box.x), 2), round(float(box.y), 2)))
            except (AttributeError, TypeError, ValueError):
                continue
        if not cells:
            return ""
        return json.dumps(sorted(cells))


def _2048_has_empty(board: list[list[int | None]]) -> bool:
    return any(cell in (None, 0) for row in board for cell in row)


def _looks_gameover(status: str) -> bool:
    compact = (status or "").lower().replace(" ", "").replace("_", "")
    return compact in {"gameover", "gameended", "over"}


@dataclass
class TurnOutcome:
    result: CycleResult
    frame: EncodedFrame
    skipped: bool
    screenshot_ref: str
    latency_ms: float | None
    cost_usd: float | None
    skipped_inference: bool = False
    scout: bool = False
    settle_ms: float | None = None
    outcome: TurnOutcomeRecord | None = None
    eyes_ms: float | None = None
    brain_ms: float | None = None
    stuck: bool = False


class AgentCycle:
    """Capture → (L2: See) → plan → execute → outcome → memory."""

    def __init__(
        self,
        *,
        table_name: str,
        controller: OsController,
        fixture: str,
        logger: JsonlLogger | None = None,
        gateway: Any = None,
        eyes: Any = None,
        brain: Any = None,
        prompt_mode: str = "pixels",
        manual: str = "",
        harness_level: str = "L1",
        paused: bool = True,
        realtime: bool = False,
        settle: bool = True,
        memory: Any = None,
        skills: Any = None,
        gsms: Any = None,
        max_actions: int = 5,
        reflect_every: int = 1,
    ) -> None:
        self.table_name = table_name
        self.controller = controller
        self.fixture = fixture
        self.logger = logger
        self.eyes = eyes if eyes is not None else gateway
        self.brain = brain
        self.gateway = self.eyes
        self.prompt_mode = prompt_mode
        self.manual = manual
        self.harness_level = harness_level
        self.paused = paused
        self.realtime = realtime
        self.settle = settle
        self.memory = memory
        self.skills = skills
        self.gsms = gsms
        self.max_actions = max_actions
        self.reflect_every = reflect_every
        self._turn = 0
        self._last_scout = False
        self._last_see: SeeResponse | None = None
        self._last_plan: ActResponse | None = None
        self._last_outcome: TurnOutcomeRecord | None = None
        self._last_executed: list[str] = []
        self._sticky_gsm: str | None = None
        self._gsm_noop_streak = 0

    def capture(self) -> tuple[EncodedFrame, bool]:
        cap = self.controller.capture
        cap.refresh()
        if not cap.layout_ready:
            scout_dir = None
            if self.logger is not None:
                scout_dir = self.logger.run_dir / "scout"
            cap.diagnose_and_apply(scout_dir=scout_dir)
        fresh = bool(self.realtime or self._has_live_gsm())
        return cap.grab(fresh=fresh)

    def _maybe_scout(self) -> bool:
        cap = self.controller.capture
        every = cap.profile.scout_every
        if every <= 0:
            self._last_scout = False
            return False
        if (self._turn - 1) % every != 0:
            self._last_scout = False
            return False
        full = cap.grab_image(full=True)
        cap.refresh_layout(full)
        if self.eyes is None:
            self._last_scout = True
            return True
        from avatar.experiments.common import parse_see, safe_call
        from avatar.harness.capture import encode_image

        frame = encode_image(
            full,
            longest_side=cap.profile.scout_longest_side,
            jpeg_quality=cap.profile.jpeg_quality,
            fmt=cap.profile.fmt,
        )
        call = safe_call(
            self.eyes,
            system=see_system(prompt_mode=self.prompt_mode, manual=self.manual),
            user=scout_user(self.fixture),
            frames=[frame],
        )
        parsed = None if call is None else call.parsed
        see = parse_see(parsed)
        kind = see.screen_kind
        # 2048 always shows a New Game button; scout often mislabels gameplay as UI.
        if self.fixture == "2048" and kind == "ui":
            kind = "gameplay"
        cap.select_preset(kind)
        if kind == "gameplay":
            cap.refresh_layout(full)
        self._last_scout = True
        return True

    def _memory_block(self) -> str:
        if self.memory is not None and hasattr(self.memory, "prompt_block"):
            return self.memory.prompt_block()
        return ""

    def _banned(self) -> set[str]:
        if self.memory is not None and hasattr(self.memory, "banned"):
            return set(self.memory.banned)
        return set()

    def see(
        self,
        frame: EncodedFrame,
        *,
        injected: SeeResponse | None = None,
    ) -> tuple[SeeResponse | None, Any]:
        if injected is not None:
            self._last_see = injected
            return injected, None
        if self.eyes is None:
            return None, None
        from avatar.experiments.common import parse_see, safe_call

        call = safe_call(
            self.eyes,
            system=see_system(prompt_mode=self.prompt_mode, manual=self.manual),
            user=see_user(self.fixture),
            frames=[frame],
        )
        parsed = None if call is None else call.parsed
        try:
            see = parse_see(parsed)
        except Exception:  # noqa: BLE001 — extra/missing See fields must not abort the turn
            see = SeeResponse(screen_kind="gameplay", status="unparseable", reasoning=str(parsed))
        # 2048 always shows a New Game button; See often mislabels gameplay as UI
        # and a sparse start board as gameover.
        if self.fixture == "2048":
            updates: dict[str, Any] = {}
            if see.screen_kind == "ui":
                updates["screen_kind"] = "gameplay"
            if see.board and _2048_has_empty(see.board) and _looks_gameover(see.status):
                updates["status"] = "playing"
            if updates:
                see = see.model_copy(update=updates)
        self._last_see = see
        return see, call

    def plan(
        self,
        frame: EncodedFrame,
        task: str,
        *,
        injected: ActResponse | None = None,
        see: SeeResponse | None = None,
    ) -> tuple[ActResponse, Any]:
        if injected is not None:
            plan = filter_banned(injected, self._banned())
            self._last_plan = plan
            return plan, None
        from avatar.experiments.common import parse_act, safe_call

        memory_block = self._memory_block()
        skills_block = ""
        if self.skills is not None and hasattr(self.skills, "prompt_block"):
            skills_block = self.skills.prompt_block() or ""
        gsm_block = ""
        if self.gsms is not None and hasattr(self.gsms, "prompt_block"):
            gsm_block = self.gsms.prompt_block()
        procedural_block = self._procedural_block()
        if self.brain is not None:
            observation = see.model_dump() if see is not None else {}
            call = safe_call(
                self.brain,
                system=brain_system(
                    self.table_name,
                    memory_block=memory_block,
                    skills_block=skills_block,
                    gsm_block=gsm_block,
                    prompt_mode=self.prompt_mode,
                    manual=self.manual,
                    procedural_block=procedural_block,
                ),
                user=brain_user(self.fixture, task, observation),
            )
            parsed = None if call is None else call.parsed
            plan = filter_banned(parse_act(parsed), self._banned())
            self._last_plan = plan
            return plan, call
        if self.eyes is None:
            return parse_act({"actions": [], "reasoning": "no gateway"}), None
        call = safe_call(
            self.eyes,
            system=act_system(
                self.table_name,
                memory_block=memory_block,
                skills_block=skills_block,
                gsm_block=gsm_block,
                prompt_mode=self.prompt_mode,
                manual=self.manual,
                procedural_block=procedural_block,
            ),
            user=act_user(self.fixture, task),
            frames=[frame],
        )
        parsed = None if call is None else call.parsed
        plan = filter_banned(parse_act(parsed), self._banned())
        self._last_plan = plan
        return plan, call

    def _procedural_block(self) -> str:
        if self.memory is None or not hasattr(self.memory, "retrieve_procedural"):
            return ""
        names = list(self._last_executed)
        if not names:
            names = [step.name for step in (self._last_plan.actions if self._last_plan else [])]
        try:
            return self.memory.retrieve_procedural(names) or ""
        except Exception:  # noqa: BLE001 — RAG may land after cycle
            return ""

    def _planner_vlm(self) -> Any:
        return self.brain if self.brain is not None else self.eyes

    def _gsm_spec(self, plan: ActResponse | None) -> Any:
        if plan is None or not plan.invoke_gsm or self.gsms is None:
            return None
        spec = self.gsms.get(plan.invoke_gsm)
        if spec is not None and getattr(spec, "enabled", False):
            return spec
        return None

    def _has_live_gsm(self) -> bool:
        if self.gsms is None:
            return False
        specs = getattr(self.gsms, "specs", None)
        if not specs:
            return False
        return any(getattr(spec, "enabled", False) for spec in specs.values())

    def _pixel_delta(self, pre: Any) -> bool | None:
        cap = self.controller.capture
        grab_thumb = getattr(cap, "grab_roi_thumb", None)
        if pre is None or not callable(grab_thumb):
            return None
        try:
            post = grab_thumb()
            thresh = float(getattr(getattr(cap, "profile", None), "diff_threshold", 0.02) or 0.02)
            return mean_absdiff(pre, post) > thresh
        except Exception:  # noqa: BLE001 — fall back to settle.changed
            return None

    def _occupancy_boxes(self, frame: Any) -> list[Any]:
        if self.fixture == "2048":
            boxes = self._2048_boxes(frame)
            if boxes:
                return boxes
        rt = getattr(self.gsms, "runtime", None) if self.gsms is not None else None
        det = getattr(rt, "detector", None)
        if det is None or frame is None:
            return list(getattr(rt, "last_boxes", []) or []) if rt is not None else []
        labels: list[str] = []
        if self.gsms is not None:
            spec = next(iter((getattr(self.gsms, "specs", None) or {}).values()), None)
            labels = list(getattr(spec, "labels", None) or [])
        try:
            return list(det.detect(frame, labels) or [])
        except Exception:  # noqa: BLE001 — occupancy is a hint
            return list(getattr(rt, "last_boxes", []) or [])

    def _should_force_see(self, injected_see: SeeResponse | None) -> bool:
        if injected_see is not None:
            return True
        last = self._last_see
        if last is not None and (
            last.screen_kind == "ui" or _looks_gameover(last.status or "")
        ):
            return True
        if self._turn > 1 and self._turn % 4 == 1:
            return True
        if self._last_outcome is not None and self._last_outcome.verdict in {
            "noop",
            "failure",
            "fail",
        }:
            return True
        return False

    def _2048_boxes(self, frame: Any) -> list[Any]:
        if self.fixture != "2048" or frame is None:
            return []
        try:
            from avatar.gsm.rule.detector import G2048BlobDetector

            labels = ["tile"]
            det = None
            if self.gsms is not None:
                specs = getattr(self.gsms, "specs", None) or {}
                spec = next(iter(specs.values()), None)
                if spec is not None and getattr(spec, "labels", None):
                    labels = list(spec.labels)
                rt = getattr(self.gsms, "runtime", None)
                det = getattr(rt, "detector", None)
            if det is None:
                det = G2048BlobDetector()
            return list(det.detect(frame, labels) or [])
        except Exception:  # noqa: BLE001 — occupancy is a hint, not a gate
            return []

    def act(self, plan: ActResponse) -> CycleResult:
        rt = getattr(self.gsms, "runtime", None) if self.gsms is not None else None
        if rt is not None and hasattr(rt, "banned"):
            rt.banned = self._banned()
        if rt is not None and self.memory is not None and hasattr(rt, "rates"):
            proc = getattr(self.memory, "procedural", None)
            stats = getattr(proc, "stats", None) or {}
            rates: dict[str, float] = {}
            for name, stat in stats.items():
                total = int(stat.n_success) + int(stat.n_noop) + int(stat.n_fail)
                if total:
                    rates[str(name)] = int(stat.n_success) / total
            rt.rates = rates
        return self.controller.execute_plan(
            plan,
            self.table_name,
            max_actions=self.max_actions,
            memory=self.memory,
            skills=self.skills,
            gsms=self.gsms,
            runtime=rt,
        )

    def turn(
        self,
        task: str,
        *,
        injected: ActResponse | None = None,
        injected_see: SeeResponse | None = None,
        model: str = "dry-run",
        save_frame: Path | None = None,
        extra_result: dict[str, Any] | None = None,
    ) -> TurnOutcome:
        self._turn += 1
        sticky = False
        if (
            injected is None
            and self.realtime
            and self._sticky_gsm
            and self._has_live_gsm()
            and self._gsm_noop_streak < 2
            and self._sticky_gsm not in self._banned()
        ):
            last = self._last_see
            ui_or_over = last is not None and (
                last.screen_kind == "ui" or _looks_gameover(last.status or "")
            )
            if ui_or_over:
                self._sticky_gsm = None
            else:
                from avatar.experiments.common import parse_act

                injected = parse_act(
                    {
                        "invoke_gsm": self._sticky_gsm,
                        "actions": [],
                        "reasoning": "sticky gsm",
                    }
                )
                sticky = True
        injected_gsm = self._gsm_spec(injected) is not None
        if injected_gsm:
            scouted = False
            self._last_scout = False
        else:
            scouted = self._maybe_scout()
        frame, skipped = self.capture()
        if save_frame is not None:
            save_frame.parent.mkdir(parents=True, exist_ok=True)
            save_frame.write_bytes(frame.data)
            screenshot_ref = str(save_frame)
        else:
            screenshot_ref = f"in-memory:{self._turn}"

        skipped_inference = False
        # Live GSMs skip ROI inference-skip so the planner (or sticky invoke) can hand off.
        # Empty mixed-pacing registries may still ROI-skip like L2-realtime.
        live_gsm = self._has_live_gsm()
        if self.realtime and injected is None and not injected_gsm and not live_gsm:
            meta = self.controller.capture.last_meta
            skip, self.controller.capture.skip_streak = next_skip_streak(
                meta.changed,
                self.controller.capture.skip_streak,
                self.controller.capture.profile.force_every,
            )
            if skip:
                skipped_inference = True
                from avatar.experiments.common import parse_act

                plan = parse_act({"actions": [], "reasoning": "skipped: roi unchanged"})
                result = CycleResult(plan, [], None, plan.model_dump())
                self._log_turn(
                    screenshot_ref=screenshot_ref,
                    frame=frame,
                    plan=plan,
                    result=result,
                    model=model,
                    extra_result=extra_result,
                    skipped=skipped,
                    skipped_inference=True,
                    scouted=scouted,
                    settle_ms=None,
                    latency=None,
                    cost=None,
                )
                return TurnOutcome(
                    result=result,
                    frame=frame,
                    skipped=skipped,
                    screenshot_ref=screenshot_ref,
                    latency_ms=None,
                    cost_usd=None,
                    skipped_inference=True,
                    scout=scouted,
                    settle_ms=None,
                )

        if injected_gsm and not self._should_force_see(injected_see):
            see_before, eyes_call = self._last_see, None
            plan, plan_call = filter_banned(injected, self._banned()), None  # type: ignore[arg-type]
            self._last_plan = plan
        else:
            skip_see = (
                not self._should_force_see(injected_see)
                and self.realtime
                and self._has_live_gsm()
                and self._turn > 1
                and injected_see is None
                and self._last_see is not None
                and self._last_see.screen_kind != "ui"
                and not _looks_gameover(self._last_see.status or "")
            )
            if skip_see:
                see_before, eyes_call = self._last_see, None
            else:
                see_before, eyes_call = self.see(frame, injected=injected_see)
            if injected_gsm and see_before is not None and (
                see_before.screen_kind == "ui" or _looks_gameover(see_before.status or "")
            ):
                self._sticky_gsm = None
                sticky = False
                plan, plan_call = self.plan(frame, task, injected=None, see=see_before)
            elif injected_gsm:
                plan, plan_call = filter_banned(injected, self._banned()), None  # type: ignore[arg-type]
            else:
                plan, plan_call = self.plan(frame, task, injected=injected, see=see_before)
        self._last_plan = plan

        pre_thumb = None
        grab_thumb = getattr(self.controller.capture, "grab_roi_thumb", None)
        if callable(grab_thumb):
            try:
                pre_thumb = grab_thumb()
            except Exception:  # noqa: BLE001 — settle.changed remains the fallback
                pre_thumb = None
        pre_occ = _occupancy_key(self._occupancy_boxes(frame), fixture=self.fixture)
        result = self.act(plan)
        self._last_executed = list(result.executed or [])
        gsm_ran = result.invoked_gsm is not None
        settle: SettleResult | None = None
        settle_ms = None
        pixel_changed = True
        if self.settle:
            settle = self.controller.capture.wait_settled()
            settle_ms = settle.elapsed_ms
            delta = self._pixel_delta(pre_thumb)
            pixel_changed = settle.changed if delta is None else delta

        after_frame: EncodedFrame | None = None
        post_occ = ""
        occupancy_changed: bool | None = None
        if gsm_ran or self.fixture == "2048":
            try:
                after_frame, _ = self.capture()
                post_occ = _occupancy_key(self._occupancy_boxes(after_frame), fixture=self.fixture)
                if pre_occ or post_occ:
                    occupancy_changed = pre_occ != post_occ
                    if occupancy_changed:
                        pixel_changed = True
            except Exception:  # noqa: BLE001 — luma/settle remains the fallback
                after_frame = None

        see_after = see_before
        see_after_call = None
        if (
            not gsm_ran
            and self.eyes is not None
            and pixel_changed
            and injected_see is None
            and injected is None
        ):
            if after_frame is None:
                after_frame, _ = self.capture()
            see_after, see_after_call = self.see(after_frame)

        outcome = compute_outcome(
            pixel_changed=pixel_changed,
            actions=result.executed,
            goal=plan.goal or task,
            see_before=see_before,
            see_after=see_after,
            compare_see=not gsm_ran,
            occupancy_changed=occupancy_changed,
        )
        occ = post_occ or pre_occ
        if not occ and gsm_ran:
            rt = getattr(self.gsms, "runtime", None) if self.gsms is not None else None
            occ = _occupancy_key(list(getattr(rt, "last_boxes", []) or []), fixture=self.fixture)
        if occ:
            outcome = outcome.model_copy(update={"state_key": occ})
        stuck = False
        if self.memory is not None and hasattr(self.memory, "observe"):
            outcome = self.memory.observe(outcome)
            stuck = bool(outcome.banned)
            planner = self._planner_vlm()
            if planner is not None and hasattr(self.memory, "maybe_condense"):
                from avatar.experiments.common import safe_call

                def _condense(**kwargs: Any) -> dict[str, Any]:
                    call = safe_call(planner, **kwargs)
                    parsed = None if call is None else call.parsed
                    return parsed if isinstance(parsed, dict) else {}

                self.memory.maybe_condense(_condense)
        self._last_outcome = outcome
        if gsm_ran:
            moved = outcome.verdict == "success"
            if moved:
                self._sticky_gsm = result.invoked_gsm
                self._gsm_noop_streak = 0
            else:
                self._gsm_noop_streak += 1
                if self._gsm_noop_streak >= 2 or stuck or outcome.verdict == "noop":
                    self._sticky_gsm = None
        else:
            self._sticky_gsm = None
            self._gsm_noop_streak = 0
        if gsm_ran and self.gsms is not None and getattr(self.gsms, "persist_path", None):
            save = getattr(self.gsms, "save", None)
            if callable(save):
                try:
                    save()
                except Exception:  # noqa: BLE001 — persist must not abort the turn
                    pass

        previous_task = ""
        historical_summary = ""
        action_design = ""
        reflect_noop = (
            sticky
            and self.memory is not None
            and outcome is not None
            and outcome.verdict in {"noop", "failure", "fail"}
        )
        if reflect_noop or (not injected_gsm and not sticky):
            previous_task, historical_summary, action_design = self._reflect_planner(
                frame=frame,
                task=task,
                plan=plan,
                result=result,
                see=see_after or see_before,
                outcome=outcome,
            )

        opt_iter = None
        if gsm_ran:
            opt_iter = self._maybe_optimize_labels(result, outcome)

        eyes_ms = None
        brain_ms = None
        latency = None
        cost = None
        # Planner/See cost is still real when a GSM then runs. Inner ticks never call a VLM.
        if eyes_call is not None:
            eyes_ms = getattr(eyes_call, "latency_ms", None)
        if see_after_call is not None:
            extra = getattr(see_after_call, "latency_ms", None) or 0.0
            eyes_ms = (eyes_ms or 0.0) + extra
        if plan_call is not None:
            latency = getattr(plan_call, "latency_ms", None)
            cost = getattr(plan_call, "cost_usd", None)
            if self.brain is not None:
                brain_ms = latency
            elif eyes_ms is None:
                eyes_ms = latency
        if eyes_call is not None:
            extra_cost = getattr(eyes_call, "cost_usd", None)
            if extra_cost:
                cost = (cost or 0.0) + extra_cost
        self._log_turn(
            screenshot_ref=screenshot_ref,
            frame=frame,
            plan=plan,
            result=result,
            model=model,
            extra_result=extra_result,
            skipped=skipped,
            skipped_inference=skipped_inference,
            scouted=scouted,
            settle_ms=settle_ms,
            latency=latency,
            cost=cost,
            outcome=outcome,
            eyes_ms=eyes_ms,
            brain_ms=brain_ms,
            see_before=see_before,
            see_after=see_after,
            stuck=stuck,
            previous_task=previous_task,
            historical_summary=historical_summary,
            action_design=action_design,
            opt_iter=opt_iter,
        )
        return TurnOutcome(
            result=result,
            frame=frame,
            skipped=skipped,
            screenshot_ref=screenshot_ref,
            latency_ms=latency,
            cost_usd=cost,
            skipped_inference=skipped_inference,
            scout=scouted,
            settle_ms=settle_ms,
            outcome=outcome,
            eyes_ms=eyes_ms,
            brain_ms=brain_ms,
            stuck=stuck,
        )

    def _dump_parsed(self, parsed: Any) -> str:
        if parsed is None:
            return ""
        if hasattr(parsed, "model_dump"):
            try:
                return json.dumps(parsed.model_dump(), default=str)
            except Exception:  # noqa: BLE001
                return str(parsed)
        if isinstance(parsed, dict):
            return json.dumps(parsed, default=str)
        return str(parsed)

    def _reflect_planner(
        self,
        *,
        frame: EncodedFrame,
        task: str,
        plan: ActResponse,
        result: CycleResult,
        see: SeeResponse | None,
        outcome: TurnOutcomeRecord | None = None,
    ) -> tuple[str, str, str]:
        previous_task = ""
        historical_summary = ""
        action_design = ""
        planner = self._planner_vlm()
        if planner is None or not hasattr(planner, "complete_json"):
            return previous_task, historical_summary, action_design
        from avatar.experiments.common import safe_call

        def _call(vlm: Any, system: str, user: str, frames: list[EncodedFrame] | None = None) -> Any:
            call = safe_call(vlm, system=system, user=user, frames=frames or [])
            return None if call is None else call.parsed

        # Pixel evidence belongs on eyes. Dual-brain's text planner cannot see frames.
        vision = self.eyes if self.eyes is not None else planner
        parsed = _call(
            vision,
            previous_task_system(),
            previous_task_user(
                self.fixture, task, result.executed, outcome=outcome
            ),
            [frame],
        )
        try:
            rec = PreviousTaskReflection.model_validate(parsed or {})
            previous_task = self._dump_parsed(rec)
        except Exception:  # noqa: BLE001 — reflection must not abort the turn
            previous_task = ""

        if self.reflect_every > 0 and self._turn % self.reflect_every == 0:
            last_ten = ""
            if self.memory is not None:
                epi = getattr(self.memory, "episodic", None)
                if epi is not None and hasattr(epi, "last_ten_summary"):
                    last_ten = epi.last_ten_summary()
            parsed = _call(planner, historical_summary_system(), historical_summary_user(last_ten))
            try:
                hist = HistoricalSummary.model_validate(parsed or {})
                historical_summary = self._dump_parsed(hist)
            except Exception:  # noqa: BLE001
                historical_summary = ""

            gsm_last = result.invoked_gsm is not None
            labels: list[str] = []
            if gsm_last and self.gsms is not None:
                spec = self.gsms.get(result.invoked_gsm)
                if spec is not None:
                    labels = list(spec.labels)
            parsed = _call(
                planner,
                action_design_system(),
                action_design_user(
                    self.fixture,
                    gsm_last=gsm_last,
                    labels=labels,
                    executed=result.executed,
                ),
            )
            try:
                design = ActionDesignReflection.model_validate(parsed or {})
                action_design = self._dump_parsed(design)
            except Exception:  # noqa: BLE001
                action_design = ""

        if self.memory is not None and hasattr(self.memory, "record_reflections"):
            try:
                self.memory.record_reflections(
                    previous_task=previous_task,
                    historical_summary=historical_summary,
                    action_design=action_design,
                    see=compact_see(see),
                )
            except Exception:  # noqa: BLE001 — memory-rag may land later
                pass
        return previous_task, historical_summary, action_design

    def _maybe_optimize_labels(
        self, result: CycleResult, outcome: TurnOutcomeRecord | None = None
    ) -> int | None:
        if self.gsms is None or not result.invoked_gsm:
            return None
        spec = self.gsms.get(result.invoked_gsm)
        if spec is None:
            return None
        rt = getattr(self.gsms, "runtime", None)
        last_boxes = list(getattr(rt, "last_boxes", []) or [])
        if not last_boxes and result.dino_labels:
            last_boxes = list(result.dino_labels)
        family = str(
            getattr(getattr(rt, "detector", None), "family", "") or spec.detector_family or ""
        )
        if family == "pixel-blob":
            return spec.rewrites
        reason = "no-piece" if not last_boxes else ""
        occupancy_cap = 16 if spec.fixture == "2048" else 8
        if len(last_boxes) > occupancy_cap:
            reason = "bad-occupancy"
        verdict = str(getattr(outcome, "verdict", "") or "")
        if verdict == "noop":
            reason = "noop"
        elif getattr(outcome, "banned", None):
            reason = reason or "stuck"
        executed = [name for name in (result.executed or []) if name]
        if executed and reason:
            reason = f"{reason} after {executed}"
        planner = self._planner_vlm()
        if planner is None or not hasattr(planner, "complete_json"):
            return spec.rewrites
        from avatar.experiments.common import safe_call
        from avatar.gsm.optimize import maybe_rewrite_labels

        return maybe_rewrite_labels(
            self.gsms,
            spec,
            last_boxes=last_boxes,
            reason=reason,
            complete_json=lambda **kwargs: safe_call(planner, **kwargs),
        )

    def _log_turn(
        self,
        *,
        screenshot_ref: str,
        frame: EncodedFrame,
        plan: ActResponse,
        result: CycleResult,
        model: str,
        extra_result: dict[str, Any] | None,
        skipped: bool,
        skipped_inference: bool,
        scouted: bool,
        settle_ms: float | None,
        latency: float | None,
        cost: float | None,
        outcome: TurnOutcomeRecord | None = None,
        eyes_ms: float | None = None,
        brain_ms: float | None = None,
        see_before: SeeResponse | None = None,
        see_after: SeeResponse | None = None,
        stuck: bool = False,
        previous_task: str = "",
        historical_summary: str = "",
        action_design: str = "",
        opt_iter: int | None = None,
    ) -> None:
        if self.logger is None:
            return
        payload: dict[str, Any] = {
            "executed": result.executed,
            "skipped_frame": skipped,
            "skipped_inference": skipped_inference,
            "stuck": stuck,
            "eyes_ms": eyes_ms,
            "brain_ms": brain_ms,
            "gsm_name": result.invoked_gsm,
            "gsm_ticks": result.gsm_ticks,
            "vlm_calls_avoided": result.gsm_ticks if result.invoked_gsm else 0,
            "gsm_inner_vlm_calls": 0 if result.invoked_gsm else None,
            "detector_family": result.detector_family,
            "dino_labels": result.dino_labels,
            "gsm_state_source": result.state_source,
        }
        if outcome is not None:
            payload.update(
                {
                    "verdict": outcome.verdict,
                    "pixel_changed": outcome.pixel_changed,
                    "board_changed": outcome.board_changed,
                    "score_delta": outcome.score_delta,
                    "banned": outcome.banned,
                    "evidence": outcome.evidence,
                }
            )
        compact_before = compact_see(see_before)
        compact_after = compact_see(see_after)
        if compact_before is not None:
            payload["see_before"] = compact_before
        if compact_after is not None:
            payload["see_after"] = compact_after
        if extra_result:
            payload.update(extra_result)
        if previous_task:
            payload["previous_task"] = previous_task
        if historical_summary:
            payload["historical_summary"] = historical_summary
        if action_design:
            payload["action_design"] = action_design
        if opt_iter is not None:
            payload["opt_iter"] = opt_iter
        elif result.invoked_gsm and self.gsms is not None:
            spec = self.gsms.get(result.invoked_gsm)
            if spec is not None:
                payload["opt_iter"] = spec.rewrites
        settings: CaptureSettings
        try:
            settings = settings_from_frame(
                frame,
                self.controller.capture.profile,
                scout=scouted,
                settle_ms=settle_ms,
                skipped_inference=skipped_inference,
            )
        except Exception:  # noqa: BLE001 — dry-run / missing profile
            settings = CaptureSettings(
                longest_side=frame.longest_side,
                jpeg_quality=frame.jpeg_quality or 85,
                format="jpeg" if "jpeg" in frame.mime else frame.mime,
                width=frame.width,
                height=frame.height,
                bytes=frame.nbytes,
                scout=scouted,
                settle_ms=settle_ms,
                skipped_inference=skipped_inference,
            )
        self.logger.write(
            TurnLog(
                screenshot_ref=screenshot_ref,
                capture_settings=settings,
                model=model,
                harness_level=self.harness_level,  # type: ignore[arg-type]
                fixture=self.fixture,
                paused=self.paused,
                prompt_mode=self.prompt_mode,  # type: ignore[arg-type]
                skills="on" if self.skills else "off",
                reasoning=plan.reasoning,
                action=plan.model_dump(),
                result=payload,
                latency_ms=latency,
                cost_usd=cost,
                stage="act",
            )
        )

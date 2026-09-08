from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from avatar.schema import ActResponse, GsmSpec


@dataclass
class GsmStep:
    actions: list[str]
    done: bool
    reason: str = ""


class GsmRuntime:
    """Closed-loop controller. No VLM per tick."""

    def __init__(self, spec: GsmSpec) -> None:
        self.spec = spec
        self.ticks = 0

    def reset(self) -> None:
        self.ticks = 0

    def step(self, frame: Any = None) -> GsmStep:
        raise NotImplementedError


class StubGsmRuntime(GsmRuntime):
    """G0 stub: emit a short named-action sequence, then done. Reads nothing."""

    def __init__(self, spec: GsmSpec) -> None:
        super().__init__(spec)
        self._names = list(spec.action_space) if spec.action_space else ["hard_drop"]
        self._queue = list(self._names)

    def reset(self) -> None:
        self.ticks = 0
        self._queue = list(self._names)

    def step(self, frame: Any = None) -> GsmStep:
        _ = frame
        self.ticks += 1
        if not self._queue:
            return GsmStep(actions=[], done=True, reason="empty")
        name = self._queue.pop(0)
        done = not self._queue
        return GsmStep(actions=[name], done=done, reason="stub" if done else "")


def make_runtime(
    spec: GsmSpec,
    *,
    detector: Any = None,
    rl_policy: Any = None,
) -> GsmRuntime:
    if spec.kind == "rl":
        from avatar.gsm.rl.policy_runtime import RlGsmRuntime
        from avatar.gsm.rule.detector import default_detector

        det = detector if detector is not None else default_detector(spec)
        return RlGsmRuntime(spec, policy=rl_policy, detector=det)
    if spec.kind == "rule":
        from avatar.gsm.rule.detector import default_detector
        from avatar.gsm.rule.runtime import RuleGsmRuntime

        det = detector if detector is not None else default_detector(spec)
        return RuleGsmRuntime(spec, detector=det)
    if detector is not None:
        from avatar.gsm.rule.runtime import RuleGsmRuntime

        return RuleGsmRuntime(spec, detector=detector)
    return StubGsmRuntime(spec)


_EXTRACTED_FAMILIES = frozenset({"grounding-dino", "open-vocab", "yolo-world"})


def attach_live_runtime(registry: Any, *, rl_policy: Any = None) -> GsmRuntime | None:
    """Build one runtime for the first enabled spec and hang it on the registry."""
    spec = next((s for s in getattr(registry, "specs", {}).values() if s.enabled), None)
    if spec is None:
        return None
    runtime = make_runtime(spec, rl_policy=rl_policy)
    registry.runtime = runtime
    det = getattr(runtime, "detector", None)
    family = str(getattr(det, "family", "") or "")
    if family:
        spec.detector_family = family
        if spec.state_source == "extracted" and family not in _EXTRACTED_FAMILIES:
            spec.state_source = "pixels"
    return runtime


def run_gsm_loop(
    spec: GsmSpec,
    table_name: str,
    execute_named: Callable[..., None],
    *,
    grab: Callable[[], Any] | None = None,
    max_ticks: int = 64,
    runtime: GsmRuntime | None = None,
    plan: ActResponse | None = None,
    on_invoke: Callable[[str], None] | None = None,
) -> Any:
    """Inner GSM ticks. Must not call eyes/brain. Does not clip to 5 actions."""
    from avatar.harness.executor import CycleResult

    rt = runtime or make_runtime(spec)
    rt.reset()
    executed: list[str] = []
    ticks = 0
    while ticks < max_ticks:
        frame = None
        if grab is not None:
            try:
                frame = grab()
            except Exception:  # noqa: BLE001 — GSM must still emit if capture is a dummy
                frame = None
        step = rt.step(frame)
        ticks += 1
        for name in step.actions:
            execute_named(table_name, name)
            executed.append(name)
        if step.done:
            break
    if on_invoke is not None:
        on_invoke(spec.name)
    parsed = plan or ActResponse(invoke_gsm=spec.name, actions=[], reasoning="gsm")
    detector = getattr(rt, "detector", None)
    family = getattr(detector, "family", "") or spec.detector_family
    labels = [getattr(b, "label", "") for b in getattr(rt, "last_boxes", [])]
    return CycleResult(
        parsed,
        executed,
        None,
        parsed.model_dump(),
        invoked_gsm=spec.name,
        gsm_ticks=ticks,
        detector_family=family or None,
        dino_labels=labels,
        state_source=spec.state_source,
    )

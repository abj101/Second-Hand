from __future__ import annotations

from pathlib import Path

from PIL import Image

from avatar.config import load_config
from avatar.gsm.registry import GsmRegistry, hash_embed
from avatar.gsm.rule.detector import FakeDetector
from avatar.gsm.rule.runtime import RuleGsmRuntime
from avatar.gsm.runtime import StubGsmRuntime, run_gsm_loop
from avatar.gsm.sandbox import sandbox_reward, sandbox_rule, sandbox_spec
from avatar.harness.capture import encode_image
from avatar.harness.cycle import AgentCycle, filter_banned
from avatar.harness.executor import OsController
from avatar.prompts.library import act_system, brain_system
from avatar.schema import ActResponse, GsmSpec, SeeResponse


class _CountingGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.model = "fake"
        self.provider = "test"

    def complete_json(self, **kwargs) -> object:
        self.calls += 1
        from avatar.models import ModelCall

        return ModelCall(
            text="{}",
            parsed={"screen_kind": "gameplay", "status": "ok"},
            latency_ms=1.0,
            cost_usd=0.0,
            model="fake",
            provider="test",
        )


class _FakeController:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.capture = _FakeCapture()

    def execute_named(self, table_name: str, action_name: str, **kwargs) -> None:
        self.executed.append(action_name)

    def execute_plan(self, plan, table_name: str, **kwargs) -> object:
        gsms = kwargs.get("gsms")
        runtime = kwargs.get("runtime")
        if plan.invoke_gsm and gsms is not None:
            spec = gsms.get(plan.invoke_gsm)
            if spec is not None and getattr(spec, "enabled", False):
                rt = runtime if runtime is not None else getattr(gsms, "runtime", None)
                return self.run_gsm(spec, table_name, plan=plan, runtime=rt)
        for step in plan.actions:
            self.execute_named(table_name, step.name)
        from avatar.harness.executor import CycleResult

        return CycleResult(plan, list(self.executed), None, plan.model_dump())

    def run_gsm(self, spec, table_name: str, **kwargs) -> object:
        return run_gsm_loop(
            spec,
            table_name,
            self.execute_named,
            grab=lambda: None,
            plan=kwargs.get("plan"),
            runtime=kwargs.get("runtime"),
        )


class _FakeCapture:
    skip_streak = 0
    last_meta = type("M", (), {"changed": True})()
    layout_ready = True
    profile = type(
        "P",
        (),
        {"scout_every": 8, "scout_longest_side": 384, "jpeg_quality": 85, "fmt": "jpeg", "force_every": 8},
    )()

    def refresh(self) -> None:
        return None

    def diagnose_and_apply(self, scout_dir=None) -> None:
        return None

    def grab(self, *, full: bool = False, fresh: bool = False) -> tuple:
        _ = full, fresh
        frame = encode_image(Image.new("RGB", (8, 8)), longest_side=8)
        return frame, False

    def grab_image(self, *, full: bool = False) -> Image.Image:
        _ = full
        return Image.new("RGB", (8, 8))

    def grab_roi_thumb(self) -> Image.Image:
        return self.grab_image()

    def refresh_layout(self, image=None) -> bool:
        _ = image
        return False

    def select_preset(self, screen_kind: str) -> None:
        _ = screen_kind

    def wait_settled(self) -> object:
        return type("S", (), {"elapsed_ms": 0.0, "changed": True})()


def test_parse_act_invoke_gsm_roundtrip() -> None:
    from avatar.experiments.common import parse_act

    act = parse_act({"invoke_gsm": "place_piece", "actions": []})
    assert act.invoke_gsm == "place_piece"


def test_filter_banned_clears_invoke_gsm() -> None:
    plan = ActResponse(invoke_gsm="place_piece", actions=[])
    out = filter_banned(plan, {"place_piece"})
    assert out.invoke_gsm is None


def test_stub_gsm_hard_drop_bypasses_clip_plan() -> None:
    spec = GsmSpec(name="place_piece", action_space=["hard_drop"], enabled=True)
    controller = _FakeController()
    result = run_gsm_loop(
        spec,
        "tetris",
        controller.execute_named,
        runtime=StubGsmRuntime(spec),
        plan=ActResponse(invoke_gsm="place_piece", actions=[]),
    )
    assert controller.executed == ["hard_drop"]
    assert result.gsm_ticks == 1
    assert result.invoked_gsm == "place_piece"


def test_rule_gsm_fake_detector_emits_named_actions() -> None:
    spec = GsmSpec(
        name="place_piece",
        kind="rule",
        labels=["T tetromino"],
        action_space=["shift_left", "rotate", "hard_drop"],
        enabled=True,
    )
    runtime = RuleGsmRuntime(spec, detector=FakeDetector())
    seen: list[str] = []
    for _ in range(8):
        step = runtime.step(None)
        seen.extend(step.actions)
        if step.done:
            break
    assert "hard_drop" in seen
    assert "shift_left" in seen


def test_gsm_turn_skips_vlm_calls(tmp_path: Path) -> None:
    gateway = _CountingGateway()
    controller = _FakeController()
    gsms = GsmRegistry("tetris")
    spec = GsmSpec(name="place_piece", action_space=["hard_drop"], enabled=True)
    gsms.specs["place_piece"] = spec
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        gateway=gateway,
        brain=object(),
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="place_piece", actions=[], reasoning="gsm")
    outcome = cycle.turn("place piece", injected=injected, model="dry-run")
    assert gateway.calls == 0
    assert outcome.result.invoked_gsm == "place_piece"
    assert outcome.eyes_ms is None
    assert outcome.brain_ms is None
    assert "hard_drop" in outcome.result.executed


def test_empty_registry_ignores_invoke_gsm() -> None:
    controller = _FakeController()
    gsms = GsmRegistry("2048")
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
    )
    plan = ActResponse(invoke_gsm="place_piece", actions=[{"name": "swipe_left"}], reasoning="x")
    controller.executed.clear()
    result = cycle.act(plan)
    assert result.invoked_gsm is None
    assert controller.executed == ["swipe_left"]


def test_registry_merge_not_spawn() -> None:
    lib = GsmRegistry("tetris", retrieve_threshold=0.99)
    a = GsmSpec(name="place_piece", kind="rule", recognize="active tetromino", enabled=True)
    b = GsmSpec(name="place_piece", kind="rule", recognize="active tetromino", enabled=True)
    assert lib.propose(a) == "spawned"
    assert lib.propose(b) == "merged"
    assert len(lib.specs) == 1


def test_registry_rewrite_cap() -> None:
    lib = GsmRegistry("tetris", max_rewrites=3)
    lib.propose(GsmSpec(name="place_piece", kind="rule", recognize="piece", enabled=True))
    for i in range(3):
        assert lib.rewrite("place_piece", GsmSpec(name="place_piece", labels=[f"L{i}"])) == "rewritten"
    assert lib.rewrite("place_piece", GsmSpec(name="place_piece", labels=["L9"])) == "rejected"
    assert lib.get("place_piece").rewrites == 3


def test_registry_rejects_unknown_kind() -> None:
    lib = GsmRegistry("tetris")
    lib.allowed_kinds = frozenset({"rule"})
    out = lib.propose(GsmSpec(name="weird", kind="rl", enabled=True))
    assert out == "rejected"


def test_sandbox_rejects_broken_reward() -> None:
    ok, reason = sandbox_reward({"dodge_bonus": 100.0, "progress_scale": 0.0})
    assert not ok
    assert "dodge" in reason.lower() or "progress" in reason.lower()


def test_sandbox_rule_passes() -> None:
    spec = GsmSpec(
        name="place_piece",
        kind="rule",
        labels=["T tetromino"],
        action_space=["shift_left", "rotate", "hard_drop"],
        enabled=True,
    )
    ok, _ = sandbox_rule(spec)
    assert ok


def test_hash_embed_cosine_merge() -> None:
    a = hash_embed("active tetromino needs placement")
    b = hash_embed("active tetromino needs placement before lock")
    from avatar.gsm.registry import cosine

    assert cosine(a, b) > 0.85


def test_prompt_has_gsm_only_when_registry_nonempty() -> None:
    lib = GsmRegistry("2048")
    assert "invoke_gsm" in act_system("swipe", gsm_block=lib.prompt_block())
    assert "Available GSMs" not in act_system("swipe", gsm_block=lib.prompt_block())
    tetris = GsmRegistry.from_config(load_config(), "tetris")
    block = tetris.prompt_block()
    assert "place_piece" in block
    assert "place_piece" in act_system("tetris", gsm_block=block)


def test_from_config_2048_choose_swipe() -> None:
    lib = GsmRegistry.from_config(load_config(), "2048")
    spec = lib.get("choose_swipe")
    assert spec is not None
    assert spec.enabled
    assert spec.kind == "rule"
    assert "swipe_down" in spec.action_space
    block = lib.prompt_block()
    assert "choose_swipe" in block
    assert "choose_swipe" in act_system("swipe", gsm_block=block)


def test_from_config_overlay_labels_ignores_unknown_kind(tmp_path: Path) -> None:
    from avatar.gsm.spec import save_specs

    path = tmp_path / "registry.json"
    save_specs(
        path,
        [
            GsmSpec(name="choose_swipe", kind="rule", labels=["tile", "2048"], rewrites=2, end="packed"),
            GsmSpec(name="invented_kind", kind="rule", labels=["nope"]),
        ],
    )
    lib = GsmRegistry.from_config(load_config(), "2048")
    lib.persist_path = path
    lib._overlay_persisted()
    spec = lib.get("choose_swipe")
    assert spec is not None
    assert spec.labels == ["tile"]
    assert spec.rewrites == 2
    assert spec.end == "packed"
    assert lib.get("invented_kind") is None


def test_from_config_overlay_skips_garbage_miss_labels(tmp_path: Path) -> None:
    from avatar.gsm.spec import save_specs

    path = tmp_path / "registry.json"
    save_specs(
        path,
        [
            GsmSpec(
                name="choose_swipe",
                kind="rule",
                labels=["miss"],
                rewrites=3,
                detector_family="fake",
            ),
        ],
    )
    lib = GsmRegistry.from_config(load_config(), "2048")
    lib.persist_path = path
    lib._overlay_persisted()
    spec = lib.get("choose_swipe")
    assert spec is not None
    assert spec.labels == ["tile"]
    assert spec.rewrites == 0
    assert spec.detector_family != "fake"


def test_from_config_overlay_skips_tetris_reflection_labels(tmp_path: Path) -> None:
    from avatar.gsm.spec import save_specs

    path = tmp_path / "registry.json"
    save_specs(
        path,
        [
            GsmSpec(
                name="place_piece",
                kind="rule",
                labels=["placement error", "missed opportunity"],
                rewrites=3,
                detector_family="fake",
            ),
        ],
    )
    lib = GsmRegistry.from_config(load_config(), "tetris")
    lib.persist_path = path
    lib._overlay_persisted()
    spec = lib.get("place_piece")
    assert spec is not None
    assert spec.labels != ["placement error", "missed opportunity"]
    assert all("tetromino" in lab.lower() for lab in spec.labels)
    assert spec.rewrites == 0
    assert spec.detector_family != "fake"


def test_sandbox_reject_does_not_enable() -> None:
    lib = GsmRegistry("tetris")
    spec = GsmSpec(
        name="place_piece",
        kind="rl",
        enabled=False,
        reward_ref={"dodge_bonus": 100.0, "progress_scale": 0.0},
        action_space=["hard_drop"],
    )
    assert lib.propose(spec) == "spawned"
    assert lib.rewrite("place_piece", GsmSpec(name="place_piece", labels=["T tetromino"])) == "rewritten"
    stored = lib.get("place_piece")
    ok, _ = sandbox_spec(stored)
    assert not ok
    assert stored is not None and stored.enabled is False


def test_rl_train_sandbox_and_invoke(tmp_path: Path) -> None:
    from avatar.gsm.rl.env import FakeGsmEnv
    from avatar.gsm.rl.loop import train_analyze_optimize

    spec = GsmSpec(
        name="place_piece",
        kind="rl",
        fixture="tetris",
        state_source="extracted",
        action_space=["shift_left", "shift_right", "rotate", "hard_drop"],
    )
    result = train_analyze_optimize(
        spec,
        env=FakeGsmEnv(state_source="extracted", max_steps=8),
        max_opt_iters=1,
        episodes=4,
        run_dir=tmp_path / "train",
    )
    assert result["sandbox_ok"]
    agent = result["agent"]
    spec.enabled = True
    from avatar.gsm.rl.policy_runtime import RlGsmRuntime

    rl_rt = RlGsmRuntime(spec, policy=agent)
    step = rl_rt.step(None)
    assert step.actions
    ok, _ = sandbox_spec(spec)
    assert ok


def test_gsm_lift_table_tags() -> None:
    from avatar.eval.scoring import gsm_lift_table

    rows = [
        {"harness_level": "GSM", "gsm": "rule", "state_source": "extracted", "success": True, "vlm_calls_per_episode": 0},
        {"harness_level": "GSM", "gsm": "rl", "state_source": "pixels", "success": False, "vlm_calls_per_episode": 0},
    ]
    table = gsm_lift_table(rows)
    assert "GSM" in table


def test_simple_browser_uri_encodes_url() -> None:
    from avatar.fixtures.browser_window import simple_browser_uri

    uri = simple_browser_uri("http://127.0.0.1:5173/?fall_ms=2500")
    assert uri.startswith("vscode://vscode.simple-browser/show?url=")
    assert "127.0.0.1" in uri


def test_stub_runtime_resets_between_invokes() -> None:
    spec = GsmSpec(name="place_piece", action_space=["hard_drop"], enabled=True)
    runtime = StubGsmRuntime(spec)
    first = run_gsm_loop(spec, "tetris", lambda *a, **k: None, runtime=runtime)
    second = run_gsm_loop(spec, "tetris", lambda *a, **k: None, runtime=runtime)
    assert first.executed == ["hard_drop"]
    assert second.executed == ["hard_drop"]
    assert first.gsm_ticks == 1
    assert second.gsm_ticks == 1


def test_pixel_blob_finds_top_piece() -> None:
    from PIL import Image, ImageDraw

    from avatar.gsm.rule.detector import PixelBlobDetector

    img = Image.new("RGB", (100, 200), (10, 10, 14))
    draw = ImageDraw.Draw(img)

    def cell(col: int, row: int) -> None:
        x0, y0 = col * 10, row * 10
        draw.rectangle([x0 + 1, y0 + 1, x0 + 8, y0 + 8], fill=(80, 220, 220))

    cell(1, 1)
    cell(2, 1)
    cell(3, 1)
    cell(2, 2)
    boxes = PixelBlobDetector().detect(img, ["T tetromino"])
    assert len(boxes) == 1
    assert 0.1 < boxes[0].cx < 0.4
    assert boxes[0].label == "T tetromino"


def test_2048_boxes_to_swipes_and_detector() -> None:
    from PIL import Image, ImageDraw

    from avatar.gsm.rule.detector import DetectedBox, G2048BlobDetector
    from avatar.gsm.rule.policy import boxes_to_swipes, legal_swipes
    from avatar.schema import GsmSpec

    assert boxes_to_swipes([])[0] == "swipe_left"
    high = [DetectedBox(label="tile", x=0.7, y=0.1, w=0.2, h=0.2)]
    assert boxes_to_swipes(high)[0] == "swipe_down"
    assert boxes_to_swipes(high, banned={"swipe_down"})[0] == "swipe_left"
    packed = [DetectedBox(label="tile", x=0.1, y=0.7, w=0.2, h=0.2)]
    assert boxes_to_swipes(packed)[0] != "swipe_down"
    assert boxes_to_swipes(packed)[0] == "swipe_up"
    assert boxes_to_swipes([], banned={"swipe_down", "swipe_left"})[0] == "swipe_up"
    assert boxes_to_swipes(packed, banned={"swipe_left"})[0] == "swipe_up"
    all_banned = {"swipe_up", "swipe_down", "swipe_left", "swipe_right"}
    assert boxes_to_swipes([], banned=all_banned)[0] != "swipe_down"
    assert boxes_to_swipes([], banned=all_banned)[0] == "swipe_up"

    bottom_wall = [DetectedBox(label="tile", x=0.1, y=0.75, w=0.2, h=0.2)]
    assert boxes_to_swipes(bottom_wall)[0] != "swipe_down"
    top_sparse = [DetectedBox(label="tile", x=0.1, y=0.05, w=0.2, h=0.2)]
    assert boxes_to_swipes(top_sparse)[0] == "swipe_down"
    merge_pair = [
        DetectedBox(label="tile", x=0.05, y=0.75, w=0.2, h=0.2, tint=(14, 14, 12)),
        DetectedBox(label="tile", x=0.30, y=0.75, w=0.2, h=0.2, tint=(14, 14, 12)),
    ]
    assert "swipe_left" in legal_swipes(merge_pair)
    assert boxes_to_swipes(merge_pair)[0] == "swipe_left"
    mid = [DetectedBox(label="tile", x=0.4, y=0.4, w=0.2, h=0.2)]
    assert boxes_to_swipes(mid)[0] == "swipe_down"
    assert boxes_to_swipes(mid, rates={"swipe_left": 1.0, "swipe_down": 0.0})[0] == "swipe_left"

    empty = Image.new("RGB", (80, 80), (187, 173, 160))
    assert G2048BlobDetector().detect(empty, ["tile"]) == []

    img = Image.new("RGB", (80, 80), (187, 173, 160))
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 42, 18, 58], fill=(237, 224, 200))
    boxes = G2048BlobDetector().detect(img, ["tile"])
    assert len(boxes) == 1
    assert boxes[0].tint is not None

    header = Image.new("RGB", (80, 80), (187, 173, 160))
    draw = ImageDraw.Draw(header)
    draw.rectangle([0, 0, 79, 16], fill=(250, 248, 239))
    assert G2048BlobDetector().detect(header, ["tile"]) == []

    spec = GsmSpec(name="choose_swipe", fixture="2048", enabled=True)
    from avatar.gsm.rule.detector import FallbackDetector, default_detector

    assert default_detector(spec).family == "pixel-blob"

    full = Image.new("RGB", (80, 80), (187, 173, 160))
    draw = ImageDraw.Draw(full)
    fill = (237, 224, 200)
    for i in range(9):
        row, col = divmod(i, 4)
        x0, y0 = col * 20 + 2, row * 20 + 2
        draw.rectangle([x0, y0, x0 + 16, y0 + 16], fill=fill)
    assert len(G2048BlobDetector().detect(full, ["tile"])) == 9

    class _EmptyPrimary:
        family = "grounding-dino"

        def detect(self, frame, labels):
            _ = frame, labels
            return []

    class _HitFallback:
        family = "pixel-blob"

        def detect(self, frame, labels):
            _ = frame, labels
            return [DetectedBox(label="tile", x=0.0, y=0.0, w=0.25, h=0.25)]

    wrapped = FallbackDetector(_EmptyPrimary(), _HitFallback())  # type: ignore[arg-type]
    assert wrapped.family == "grounding-dino"
    assert wrapped.detect(None, ["tile"])[0].label == "tile"


def test_gsm_settle_noop_bans_next_swipe(tmp_path: Path) -> None:
    from avatar.gsm.rule.detector import DetectedBox, FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.memory.store import AgentMemory

    mem = AgentMemory("2048", root=tmp_path)
    controller = _FakeController()
    controller.capture.wait_settled = lambda: type("S", (), {"elapsed_ms": 0.0, "changed": False})()
    spec = GsmSpec(
        name="choose_swipe",
        fixture="2048",
        kind="rule",
        action_space=["swipe_down", "swipe_left", "swipe_up", "swipe_right"],
        enabled=True,
    )
    gsms = GsmRegistry("2048")
    gsms.specs["choose_swipe"] = spec
    gsms.runtime = RuleGsmRuntime(
        spec,
        detector=FakeDetector(
            boxes=[DetectedBox(label="tile", x=0.7, y=0.1, w=0.2, h=0.2)]
        ),
    )
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
        memory=mem,
        settle=True,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="choose_swipe", actions=[], reasoning="gsm")
    first = cycle.turn("slide", injected=injected, model="dry-run")
    assert first.outcome is not None
    assert first.outcome.verdict == "noop"
    assert first.result.executed == ["swipe_down"]
    second = cycle.turn("slide", injected=injected, model="dry-run")
    assert "swipe_down" in mem.banned
    assert second.outcome is not None and "swipe_down" in second.outcome.banned
    controller.executed.clear()
    third = cycle.turn("slide", injected=injected, model="dry-run")
    assert third.result.executed == ["swipe_left"]


def test_gsm_pre_post_thumb_beats_settle_lie(tmp_path: Path) -> None:
    from avatar.gsm.rule.detector import DetectedBox, FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.memory.store import AgentMemory

    mem = AgentMemory("2048", root=tmp_path)
    controller = _FakeController()
    thumbs = [
        Image.new("RGB", (16, 16), (10, 10, 10)),
        Image.new("RGB", (16, 16), (200, 200, 200)),
    ]
    controller.capture.grab_roi_thumb = lambda: thumbs.pop(0) if thumbs else Image.new("RGB", (16, 16), (200, 200, 200))
    controller.capture.wait_settled = lambda: type("S", (), {"elapsed_ms": 0.0, "changed": False})()
    spec = GsmSpec(
        name="choose_swipe",
        fixture="2048",
        kind="rule",
        detector_family="pixel-blob",
        action_space=["swipe_down", "swipe_left", "swipe_up", "swipe_right"],
        enabled=True,
    )
    gsms = GsmRegistry("2048")
    gsms.specs["choose_swipe"] = spec
    gsms.runtime = RuleGsmRuntime(
        spec,
        detector=FakeDetector(boxes=[DetectedBox(label="tile", x=0.7, y=0.1, w=0.2, h=0.2)]),
    )
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
        memory=mem,
        settle=True,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="choose_swipe", actions=[], reasoning="gsm")
    outcome = cycle.turn("slide", injected=injected, model="dry-run")
    assert outcome.outcome is not None
    assert outcome.outcome.verdict == "noop"
    assert spec.rewrites == 0


def test_gsm_stays_invoked_after_swipe_noops(tmp_path: Path) -> None:
    from avatar.gsm.rule.detector import DetectedBox, FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.memory.store import AgentMemory

    mem = AgentMemory("2048", root=tmp_path)
    controller = _FakeController()
    controller.capture.wait_settled = lambda: type("S", (), {"elapsed_ms": 0.0, "changed": False})()
    spec = GsmSpec(
        name="choose_swipe",
        fixture="2048",
        kind="rule",
        action_space=["swipe_down", "swipe_left", "swipe_up", "swipe_right"],
        enabled=True,
    )
    gsms = GsmRegistry("2048")
    gsms.specs["choose_swipe"] = spec
    gsms.runtime = RuleGsmRuntime(
        spec,
        detector=FakeDetector(
            boxes=[DetectedBox(label="tile", x=0.1, y=0.7, w=0.2, h=0.2)]
        ),
    )
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
        memory=mem,
        settle=True,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="choose_swipe", actions=[], reasoning="gsm")
    cycle.turn("slide", injected=injected, model="dry-run")
    cycle.turn("slide", injected=injected, model="dry-run")
    assert mem.banned
    controller.executed.clear()
    third = cycle.turn("slide", injected=injected, model="dry-run")
    assert third.result.invoked_gsm == "choose_swipe"
    assert third.result.executed
    assert third.result.executed[0] not in mem.banned
    assert third.result.executed[0] in {"swipe_up", "swipe_right", "swipe_left", "swipe_down"}


def test_2048_nine_boxes_do_not_rewrite(tmp_path: Path) -> None:
    from avatar.gsm.rule.detector import DetectedBox, FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.memory.store import AgentMemory

    eyes = _CountingGateway()
    mem = AgentMemory("2048", root=tmp_path)
    controller = _FakeController()
    spec = GsmSpec(
        name="choose_swipe",
        fixture="2048",
        kind="rule",
        detector_family="grounding-dino",
        action_space=["swipe_down", "swipe_left", "swipe_up", "swipe_right"],
        enabled=True,
    )
    gsms = GsmRegistry("2048")
    gsms.specs["choose_swipe"] = spec
    boxes = [
        DetectedBox(label="tile", x=(i % 4) / 4, y=(i // 4) / 4, w=0.25, h=0.25)
        for i in range(9)
    ]
    det = FakeDetector(boxes=boxes)
    det.family = "grounding-dino"
    gsms.runtime = RuleGsmRuntime(spec, detector=det)
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
        memory=mem,
        eyes=eyes,
        settle=False,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="choose_swipe", actions=[], reasoning="gsm")
    outcome = cycle.turn("slide", injected=injected, model="dry-run")
    assert outcome.outcome is not None
    assert outcome.outcome.verdict == "noop"
    assert spec.rewrites == 0
    assert spec.labels == []


def test_2048_occupancy_delta_beats_luma_miss(tmp_path: Path) -> None:
    from avatar.gsm.rule.detector import DetectedBox
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.memory.store import AgentMemory

    mem = AgentMemory("2048", root=tmp_path)
    controller = _FakeController()
    same = Image.new("RGB", (16, 16), (10, 10, 10))
    controller.capture.grab_roi_thumb = lambda: same.copy()
    controller.capture.wait_settled = lambda: type("S", (), {"elapsed_ms": 0.0, "changed": False})()

    class _FlipDetector:
        family = "pixel-blob"

        def __init__(self) -> None:
            self.n = 0

        def detect(self, frame, labels):
            _ = frame, labels
            self.n += 1
            x = 0.0 if self.n <= 2 else 0.5
            return [DetectedBox(label="tile", x=x, y=0.0, w=0.25, h=0.25)]

    spec = GsmSpec(
        name="choose_swipe",
        fixture="2048",
        kind="rule",
        detector_family="pixel-blob",
        action_space=["swipe_down", "swipe_left", "swipe_up", "swipe_right"],
        enabled=True,
    )
    gsms = GsmRegistry("2048")
    gsms.specs["choose_swipe"] = spec
    gsms.runtime = RuleGsmRuntime(spec, detector=_FlipDetector())  # type: ignore[arg-type]
    cycle = AgentCycle(
        table_name="swipe",
        controller=controller,  # type: ignore[arg-type]
        fixture="2048",
        gsms=gsms,
        memory=mem,
        settle=True,
        harness_level="GSM",
    )
    injected = ActResponse(invoke_gsm="choose_swipe", actions=[], reasoning="gsm")
    outcome = cycle.turn("slide", injected=injected, model="dry-run")
    assert outcome.outcome is not None
    assert outcome.outcome.verdict == "success"
    assert outcome.outcome.pixel_changed is True
    assert not mem.banned


def test_gsm_live_registry_does_not_roi_skip() -> None:
    gateway = _CountingGateway()
    controller = _FakeController()
    controller.capture.last_meta = type("M", (), {"changed": False})()
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = GsmSpec(name="place_piece", action_space=["hard_drop"], enabled=True)
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        gateway=gateway,
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
    )
    outcome = cycle.turn("place piece", model="dry-run")
    assert gateway.calls >= 1
    assert outcome.skipped_inference is False


def test_gsm_planner_latency_logged_on_handoff() -> None:
    class _PlanGsm(_CountingGateway):
        def complete_json(self, **kwargs):
            self.calls += 1
            from avatar.models import ModelCall

            return ModelCall(
                text="{}",
                parsed={"invoke_gsm": "place_piece", "actions": [], "reasoning": "handoff"},
                latency_ms=12.0,
                cost_usd=0.0,
                model="fake",
                provider="test",
            )

    gateway = _PlanGsm()
    controller = _FakeController()
    controller.capture.profile = type(
        "P",
        (),
        {"scout_every": 0, "scout_longest_side": 384, "jpeg_quality": 85, "fmt": "jpeg", "force_every": 8},
    )()
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = GsmSpec(
        name="place_piece", action_space=["hard_drop"], enabled=True
    )
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        gateway=gateway,
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
    )
    outcome = cycle.turn("place piece", model="dry-run")
    assert gateway.calls >= 1
    assert outcome.result.invoked_gsm == "place_piece"
    assert outcome.latency_ms == 12.0
    assert outcome.eyes_ms == 12.0
    assert "hard_drop" in outcome.result.executed


def test_realtime_gsm_sticky_skips_vlm_after_success() -> None:
    from avatar.gsm.rule.detector import DetectedBox, FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime

    class _Moving:
        family = "fake"

        def __init__(self) -> None:
            self.n = 0

        def detect(self, frame, labels):
            _ = frame, labels
            self.n += 1
            x = min(0.9, 0.08 * self.n)
            return [DetectedBox(label="T tetromino", x=x, y=0.1, w=0.1, h=0.1)]

    gw = _CountingGateway()
    controller = _FakeController()

    def _no_thumb() -> Image.Image:
        raise RuntimeError("no thumb")

    controller.capture.grab_roi_thumb = _no_thumb  # type: ignore[method-assign]
    spec = GsmSpec(
        name="place_piece",
        fixture="tetris",
        kind="rule",
        action_space=["shift_left", "shift_right", "rotate", "hard_drop"],
        enabled=True,
    )
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = spec
    gsms.runtime = RuleGsmRuntime(spec, detector=_Moving())  # type: ignore[arg-type]
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        eyes=gw,
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
        settle=True,
        reflect_every=0,
    )
    injected = ActResponse(invoke_gsm="place_piece", actions=[], reasoning="force")
    first = cycle.turn("place", injected=injected, model="dry-run")
    assert first.result.invoked_gsm == "place_piece"
    assert first.outcome is not None
    assert first.outcome.verdict == "success"
    assert cycle._sticky_gsm == "place_piece"
    assert gw.calls == 0
    second = cycle.turn("place", model="dry-run")
    assert second.result.invoked_gsm == "place_piece"
    assert gw.calls == 0
    assert second.result.executed


def test_occupancy_noop_clears_sticky() -> None:
    from avatar.gsm.rule.detector import FakeDetector
    from avatar.gsm.rule.runtime import RuleGsmRuntime

    gw = _CountingGateway()
    controller = _FakeController()
    spec = GsmSpec(
        name="place_piece",
        fixture="tetris",
        kind="rule",
        action_space=["shift_left", "shift_right", "rotate", "hard_drop"],
        enabled=True,
    )
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = spec
    gsms.runtime = RuleGsmRuntime(spec, detector=FakeDetector())
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        eyes=gw,
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
        settle=True,
        reflect_every=0,
    )
    injected = ActResponse(invoke_gsm="place_piece", actions=[], reasoning="force")
    first = cycle.turn("place", injected=injected, model="dry-run")
    assert first.outcome is not None
    assert first.outcome.verdict == "noop"
    assert cycle._sticky_gsm is None


def test_encode_extracted_uses_boxes() -> None:
    from avatar.gsm.rl.dqn import encode_extracted
    from avatar.gsm.rule.detector import DetectedBox

    empty = encode_extracted([])
    assert empty == [0.0] * 16
    boxes = [
        DetectedBox(label="T tetromino", x=0.2, y=0.1, w=0.1, h=0.1),
        DetectedBox(label="O tetromino", x=0.7, y=0.8, w=0.1, h=0.1),
    ]
    vec = encode_extracted(boxes)
    assert vec[0] == 2.0
    assert vec != empty
    assert any(v > 0 for v in vec[5:])


def test_boxes_to_actions_packs_left_not_spawn_dump() -> None:
    from avatar.gsm.rule.detector import DetectedBox
    from avatar.gsm.rule.policy import boxes_to_actions

    spawn = DetectedBox(label="T tetromino", x=0.30, y=0.05, w=0.1, h=0.1)
    assert boxes_to_actions([spawn]) == ["shift_left"]
    banned = boxes_to_actions([spawn], banned={"shift_left"})
    assert "shift_left" not in banned


def test_rl_runtime_detects_frame() -> None:
    from avatar.gsm.rl.dqn import DoubleDQN, encode_extracted
    from avatar.gsm.rl.policy_runtime import RlGsmRuntime
    from avatar.gsm.rule.detector import FakeDetector

    spec = GsmSpec(
        name="place_piece",
        kind="rl",
        labels=["T tetromino"],
        enabled=True,
        state_source="extracted",
    )
    policy = DoubleDQN(n_obs=16, n_act=4, epsilon=0.0)
    rt = RlGsmRuntime(spec, policy=policy, detector=FakeDetector())
    step = rt.step(object())
    assert rt.last_boxes
    assert step.actions
    assert encode_extracted(rt.last_boxes)[0] > 0


def test_tetris_env_hard_drop_is_not_done() -> None:
    from avatar.gsm.rl.env import TetrisGsmEnv
    from avatar.gsm.rule.detector import DetectedBox

    class _Det:
        family = "fake"

        def __init__(self) -> None:
            self.n = 0

        def detect(self, frame, labels):
            _ = frame, labels
            self.n += 1
            y = 0.8 if self.n > 1 else 0.2
            return [DetectedBox(label="T tetromino", x=0.3, y=y, w=0.1, h=0.1)]

    executed: list[str] = []
    env = TetrisGsmEnv(
        lambda table, name: executed.append(name),
        lambda: object(),
        detector=_Det(),
        max_steps=8,
    )
    env.reset()
    p0 = env.progress
    obs, reward, done, info = env.step(3)  # hard_drop
    assert executed[-1] == "hard_drop"
    assert done is False
    assert info["progress"] != p0
    assert obs != [0.0] * 16


def test_train_requires_env() -> None:
    from avatar.gsm.rl.loop import train_analyze_optimize

    spec = GsmSpec(name="place_piece", kind="rl", enabled=False)
    try:
        train_analyze_optimize(spec, max_opt_iters=1, episodes=1)
    except ValueError as exc:
        assert "TetrisGsmEnv" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_ui_see_named_start_not_gsm() -> None:
    from avatar.gsm.rule.runtime import RuleGsmRuntime
    from avatar.models import ModelCall

    class _StartGateway:
        model = "fake"
        provider = "test"

        def complete_json(self, **kwargs) -> object:
            return ModelCall(
                text="{}",
                parsed={"actions": [{"name": "start"}], "invoke_gsm": None, "reasoning": "menu"},
                latency_ms=1.0,
                cost_usd=0.0,
                model="fake",
                provider="test",
            )

    controller = _FakeController()
    spec = GsmSpec(name="place_piece", kind="rule", enabled=True, action_space=["hard_drop"])
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = spec
    gsms.runtime = RuleGsmRuntime(spec, detector=FakeDetector())
    cycle = AgentCycle(
        table_name="tetris",
        controller=controller,  # type: ignore[arg-type]
        fixture="tetris",
        eyes=_StartGateway(),
        realtime=True,
        gsms=gsms,
        harness_level="GSM",
        settle=False,
        reflect_every=0,
    )
    injected = ActResponse(invoke_gsm="place_piece", actions=[], reasoning="sticky")
    ui = SeeResponse(screen_kind="ui", status="idle")
    outcome = cycle.turn("play", injected=injected, injected_see=ui, model="dry-run")
    assert outcome.result.invoked_gsm is None
    assert "start" in outcome.result.executed
    assert "hard_drop" not in outcome.result.executed

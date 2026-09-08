from PIL import Image, ImageDraw

from loop.agent.discovery import (
    DiscoveryObject,
    DiscoveryWorkflow,
    difference_box,
    inspection_contract,
    inventory_unknowns,
)
from loop.memory.screen_objects import ScreenObject
from loop.os.ocr import OcrWord


def _object(mark: int, label: str, role: str, x: float) -> ScreenObject:
    if role == "enemy":
        y, y0, y1 = .63, .56, .70
    else:
        y, y0, y1 = .82, .75, .90
    return ScreenObject(
        mark, x, y, x - .04, y0, x + .04, y1,
        label, "other", "test", "combat", "", f"obj_{mark}", role,
    )


def test_inventory_excludes_duplicate_enemy_but_keeps_duplicate_cards() -> None:
    items = [_object(1, "unknown_enemy", "enemy", .40), _object(2, "unknown_enemy", "enemy", .41), _object(3, "unknown_card", "card", .30), _object(4, "unknown_card", "card", .30)]
    queued = inventory_unknowns(items, energy=3)
    assert [item.object_id for item in queued] == ["obj_1", "obj_3", "obj_4"]


def test_inventory_skips_typed_player_sprite() -> None:
    player = ScreenObject(11, .24, .58, .20, .47, .30, .69, "player", "other", "test", "combat", "", "obj_11", "player")
    enemy = ScreenObject(9, .74, .63, .68, .56, .80, .69, "Nibbit", "other", "test", "combat", "", "obj_9", "enemy")
    queued = inventory_unknowns([player, enemy], energy=3)
    assert [item.object_id for item in queued] == []


def test_difference_box_finds_changed_tooltip() -> None:
    before = Image.new("RGB", (200, 100), "black")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((130, 10, 190, 45), fill="white")
    box = difference_box(before, after)
    assert box is not None and box[0] > .5 and box[1] < .3


def test_discovery_binds_crop_ocr_to_active_contract(tmp_path) -> None:
    item = DiscoveryObject("enemy_1", 7, "enemy", (.2, .2, .4, .5), (.3, .35))
    contract = inspection_contract(run_id="run", step_id=4, item=item)
    before = Image.new("RGB", (200, 100), "black")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((100, 10, 190, 40), fill="white")
    def reader(image):
        if image.convert("L").getextrema()[1] < 40:
            return []
        return [OcrWord("Jaw Worm", .95, .1, .1, .8, .3)]

    result = DiscoveryWorkflow(artifacts_root=tmp_path, ocr_reader=reader).inspect_frames(
        contract, before=before, after_frames=[after, after],
    )
    assert result.status == "identified" and result.name == "Jaw Worm"
    assert (tmp_path / "run" / "4" / "discovery" / "enemy_1" / "result.json").exists()


def test_inventory_skips_hp_chrome_named_cards_keeps_unidentified_sprite() -> None:
    player = ScreenObject(11, .24, .58, .20, .47, .30, .69, "player", "other", "test", "combat", "", "player_1", "player")
    hp = ScreenObject(8, .75, .35, .70, .32, .80, .38, "44/44", "other", "test", "combat", "", "hp_1", "enemy")
    chrome = ScreenObject(2, .10, .06, .04, .02, .16, .10, "Deck", "other", "test", "combat", "", "deck_1", "button")
    strike = ScreenObject(4, .40, .85, .30, .78, .50, .95, "Strike", "other", "test", "combat", "", "card_1", "card")
    sprite = ScreenObject(9, .74, .63, .68, .56, .80, .69, "unidentified_enemy", "other", "test", "combat", "", "enemy_1", "enemy")
    queued = inventory_unknowns([player, hp, chrome, strike, sprite], energy=3)
    assert [item.object_id for item in queued] == ["enemy_1"]


def test_inventory_does_not_infer_role_from_layout() -> None:
    player = ScreenObject(11, .25, .58, .20, .47, .30, .69, "unknown_character", "other", "test", "combat", "", "enemy_2", "enemy")
    sprite = ScreenObject(9, .74, .63, .68, .56, .80, .69, "unidentified_enemy", "other", "test", "combat", "", "enemy_1", "enemy")
    queued = inventory_unknowns([player, sprite], energy=3)
    assert [item.object_id for item in queued] == ["enemy_2", "enemy_1"]


def test_novel_words_drop_before_hud_text() -> None:
    from loop.agent.discovery import _novel_words

    before = [OcrWord("Deck", .9, .05, .05, .15, .1), OcrWord("44/44", .9, .7, .3, .15, .08)]
    after = [
        OcrWord("Deck", .9, .05, .05, .15, .1),
        OcrWord("Jaw Worm", .9, .55, .12, .25, .1),
        OcrWord("44/44", .9, .7, .3, .15, .08),
    ]
    novel = _novel_words(after, before)
    assert [word.text for word in novel] == ["Jaw Worm"]


def test_ocr_variants_native_first_skips_upscale_when_named() -> None:
    from loop.agent.discovery import _ocr_variants

    calls: list[tuple[int, int]] = []

    def reader(image):
        calls.append(image.size)
        return [OcrWord("Nibbit", .9, .1, .1, .5, .2)]

    words = _ocr_variants(Image.new("RGB", (40, 20), "white"), reader)
    assert [word.text for word in words] == ["Nibbit"]
    assert calls == [(40, 20)]


def test_inspect_ignores_before_hud_on_tooltip_crop(tmp_path) -> None:
    item = DiscoveryObject("enemy_1", 7, "enemy", (.2, .2, .4, .5), (.3, .35))
    contract = inspection_contract(run_id="run", step_id=5, item=item)
    before = Image.new("RGB", (200, 100), "black")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((100, 10, 190, 40), fill="white")

    def reader(image):
        if image.convert("L").getextrema()[1] < 40:
            return []
        return [OcrWord("Jaw Worm", .95, .1, .1, .8, .3)]

    result = DiscoveryWorkflow(artifacts_root=tmp_path, ocr_reader=reader).inspect_frames(
        contract, before=before, after_frames=[after],
    )
    assert result.status == "identified"
    assert result.name == "Jaw Worm"


def test_inspect_binds_sprite_name_not_intent_tooltip(tmp_path) -> None:
    item = DiscoveryObject("enemy_1", 11, "enemy", (.65, .55, .90, .80), (.78, .68))
    contract = inspection_contract(run_id="run", step_id=6, item=item)
    before = Image.new("RGB", (200, 100), "black")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((12, 48, 95, 78), fill="white")
    ImageDraw.Draw(after).rectangle((132, 56, 178, 78), fill=(80, 80, 80))

    def reader(image):
        lo, hi = image.convert("L").getextrema()
        if hi < 40:
            return []
        if lo > 160:
            return [
                OcrWord("Aggressive", .99, .1, .15, .7, .25),
                OcrWord("This enemy intends to Attack", .99, .1, .45, .85, .25),
            ]
        return [
            OcrWord("45/45", .99, .2, .45, .35, .15),
            OcrWord("Nibbit", .99, .2, .70, .45, .18),
        ]

    result = DiscoveryWorkflow(artifacts_root=tmp_path, ocr_reader=reader).inspect_frames(
        contract, before=before, after_frames=[after],
    )
    assert result.status == "identified"
    assert result.name == "Nibbit"


def _agent_for_discovery(tmp_path, objects, *, controller=None, reader=None):
    from types import SimpleNamespace

    from loop.agent.entities import EntityTracker
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.memory.screen_objects import ScreenObjectStore

    store = ScreenObjectStore(tmp_path / "obj.db")
    store._run_id = "run"
    store._step_id = 1
    store._objects = list(objects)
    frames = {
        "dark": Image.new("RGB", (200, 100), "black"),
        "lit": Image.new("RGB", (200, 100), "black"),
    }
    ImageDraw.Draw(frames["lit"]).rectangle((110, 8, 190, 36), fill="white")

    class _Fix:
        def __init__(self) -> None:
            self.controller = controller
            self._n = 0

        def grab_image(self):
            self._n += 1
            return frames["dark"] if self._n % 2 else frames["lit"]

        def observe_image(self, image):
            return image

    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    agent.fixture = _Fix()
    agent.session_id = "run"
    agent._step_id = 1
    agent._screen_objects = store
    agent._entity_tracker = EntityTracker()
    agent._discovery = DiscoveryWorkflow(
        artifacts_root=tmp_path / "frames",
        ocr_reader=reader or (lambda image: (
            [OcrWord("Jaw Worm", .95, .1, .1, .8, .3)]
            if image.convert("L").getextrema()[1] >= 40 else []
        )),
    )
    agent._discovery_attempts = {}
    agent._selected = None
    agent._DISCOVERY_SETTLE_MS = 0
    agent._obs_metrics = {"screen": "combat", "energy": 3}
    agent._host_stats = {"screen": "combat"}
    agent._aim = None
    agent._frame_model = None
    agent._last_click = None
    agent._cached_perceive_scene = None
    agent.semantic_memory = SimpleNamespace(remember_identity=lambda **_: None)
    return agent, store


def test_mechanical_discovery_binds_only_hovered_object(tmp_path) -> None:
    from loop.agent.perceive import SceneDescription
    from loop.log.schema import ActionLog

    class _Ctl:
        def __init__(self) -> None:
            self.moves: list[tuple[float, float]] = []
            self.last_xy = None

        def release_all(self) -> None:
            return None

        def execute(self, prims) -> None:
            for prim in prims:
                xy = (float(prim.x), float(prim.y))
                self.moves.append(xy)
                self.last_xy = xy

    enemy_a = ScreenObject(9, .55, .63, .50, .56, .60, .70, "unidentified_enemy", "other", "test", "combat", "", "enemy_1", "enemy")
    enemy_b = ScreenObject(10, .78, .63, .73, .56, .83, .70, "unidentified_enemy", "other", "test", "combat", "", "enemy_2", "enemy")
    ctl = _Ctl()

    def reader(image):
        if image.convert("L").getextrema()[1] < 40:
            return []
        name = "Nibbit" if ctl.last_xy and ctl.last_xy[0] > 0.7 else "Jaw Worm"
        return [OcrWord(name, .95, .1, .1, .8, .3)]

    agent, store = _agent_for_discovery(tmp_path, [enemy_a, enemy_b], controller=ctl, reader=reader)
    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
        enemies=[
            {"label": "unidentified_enemy", "role": "enemy", "entity_id": "enemy_1", "inspectable": "true", "bbox": [500, 560, 600, 700]},
            {"label": "unidentified_enemy", "role": "enemy", "entity_id": "enemy_2", "inspectable": "true", "bbox": [730, 560, 830, 700]},
        ],
    )
    agent._run_mechanical_discovery(scene)
    assert [obj.label for obj in store.objects if obj.object_id == "enemy_1"] == ["Jaw Worm"]
    assert [obj.label for obj in store.objects if obj.object_id == "enemy_2"] == ["Nibbit"]
    assert ctl.moves[0] == (.55, .63)
    assert ctl.moves[1] == (.78, .63)
    assert ctl.moves[-1] == (.50, .92)
    assert agent._two_stage_reason_route(scene)[0] == "act"
    strike = ActionLog(mark=4, click_x=.40, click_y=.85, target=9, click_x2=.74, click_y2=.63, source_object_id="card_1", target_object_id="enemy_1")
    strike.name = strike.derive_name()
    assert agent._next_discovery_action() is None
    assert strike.source_object_id == "card_1"
    assert strike.target_object_id == "enemy_1"
    store.close()


def test_named_strike_not_replaced_by_player_hover(tmp_path) -> None:
    player = ScreenObject(11, .24, .58, .20, .47, .30, .69, "player", "other", "test", "combat", "", "player_1", "player")
    enemy = ScreenObject(9, .74, .63, .68, .56, .80, .69, "Jaw Worm", "other", "test", "combat", "", "enemy_1", "enemy")
    strike = ScreenObject(4, .40, .85, .30, .78, .50, .95, "Strike", "other", "test", "combat", "", "card_1", "card")
    agent, store = _agent_for_discovery(tmp_path, [player, enemy, strike])
    assert agent._next_discovery_action() is None
    from loop.agent.perceive import SceneDescription
    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high", "entity_id": "card_1"}],
        enemies=[{"label": "Jaw Worm", "role": "enemy", "entity_id": "enemy_1", "inspectable": False, "name_source": "hover_ocr", "bbox": [680, 560, 800, 700]}],
        characters=[{"label": "player", "role": "player", "entity_id": "player_1", "bbox": [200, 470, 300, 690]}],
    )
    assert agent._two_stage_reason_route(scene)[0] == "act"
    store.close()

import numpy as np
from PIL import Image
from agent.track import Tracker

W, H = 160, 144
RNG = np.random.default_rng(1)
def blocks(h, w, n): return np.kron(RNG.integers(0, 255, (h // n, w // n, 3), dtype=np.uint8), np.ones((n, n, 1), np.uint8))  # tile-like flat blocks
BG, SPRITE = blocks(H, 400, 8), blocks(16, 16, 4)

def frame(cam, sprite=None, static=None):
    world = BG.copy()
    for pos, tex in ((sprite, SPRITE), (static, SPRITE[::-1])):
        if pos: world[pos[1]:pos[1] + 16, pos[0]:pos[0] + 16] = tex
    return Image.fromarray(world[:, cam:cam + W])

def run(frames_n=40, sprite_from=(60, 60), static=(110, 40)):
    tr = Tracker(); cam = 20
    tr.seed([("npc", ((sprite_from[0] - cam + 8) / W, (sprite_from[1] + 8) / H), True),
             ("sign", ((static[0] - cam + 8) / W, (static[1] + 8) / H), False)], frame(cam, sprite_from, static))
    errs = []
    for f in range(1, frames_n + 1):
        cam, sx = 20 + f, sprite_from[0] + f // 2  # camera 1 px/frame, NPC half a px/frame in world space
        tr.update(frame(cam, (sx, sprite_from[1]), static))
        t, s = tr.tracks["npc"], tr.tracks["sign"]
        errs.append(max(abs(t["x"] - (sx - cam + 8)), abs(t["y"] - (sprite_from[1] + 8))))
        assert s["status"] != "live" or abs(s["x"] - (static[0] - cam + 8)) <= 1e-6 and s["y"] == static[1] + 8
    return tr, errs

def test_walker_keeps_one_track_on_scrolling_background():
    tr, errs = run()
    assert max(errs) <= 2 and tr.tracks["npc"]["status"] == "live" and tr.tracks["npc"]["lost"] == 0

def test_track_leaving_screen_goes_offscreen():
    tr, _ = run(60, static=(60, 40))  # camera moves right 60 px: the static sign at screen x ~ 48 -> -12
    assert tr.tracks["sign"]["status"] == "offscreen" and "sign" not in tr.gone()

def test_near_matches_within_a_tile_and_skips_used():
    tr = Tracker(); tr.seed([("a", (.5, .5), True)], frame(0))
    assert tr.near((.5 + 15 / W, .5)) == "a" and tr.near((.5 + 17 / W, .5)) is None and tr.near((.5, .5), used={"a"}) is None

def test_vanished_sprite_is_lost_then_gone():
    tr = Tracker(); tr.seed([("a", (68 / W, 68 / H), True)], frame(0, (60, 60)))
    tr.update(frame(0, (60, 60)))
    for _ in range(30): tr.update(frame(0))  # first frame still shows it, then it is gone
    assert tr.tracks["a"]["lost"] >= 24 and tr.gone() == {"a"}


def _world(tmp_path):
    from agent.memory import Memory
    from agent.world import World
    mem = Memory(tmp_path); mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    world = World(mem, 16); world.current_place = "room"
    return mem, world

def _per(npc_at, name, text=()):
    return {"screen": "world", "text": list(text), "entities": [{"name": "player", "kind": "player", "at": [88 / W, 72 / H]},
                                                                  {"name": name, "kind": "person", "at": [npc_at[0] / W, npc_at[1] / H]}]}

def test_walking_npc_renamed_between_perceptions_is_one_record(tmp_path):
    mem, world = _world(tmp_path)
    world.tracker.update(frame(0, (32, 16)))
    world.before(_per((40, 24), "girl"), "room", None, (W, H), 1)
    first = list(world.entity_ids.values())
    for x in range(33, 81): world.tracker.update(frame(0, (x, 16)))  # walks 48 px = 3 tiles
    world.before(_per((88, 24), "woman"), "room", "room", (W, H), 2)
    npcs = [r for r in mem.rows("entities") if r.get("kind") == "person"]
    assert len(npcs) == 1 and npcs[0]["id"] == first[-1] and "woman" in npcs[0]["aliases"] and npcs[0]["status"] == "seen"

def test_npc_lost_in_view_goes_gone_unless_text_box_is_up(tmp_path):
    mem, world = _world(tmp_path)
    world.tracker.update(frame(0, (32, 16)))
    world.before(_per((40, 24), "girl"), "room", None, (W, H), 1)
    ident = world.entity_ids[1]
    for _ in range(30): world.tracker.update(frame(0))  # she vanished from the same background
    solo = {"screen": "world", "text": [], "entities": [_per((0, 0), "x")["entities"][0]]}
    world.before(_per((88, 24), "x", text=["hello"]), "room", "room", (W, H), 2)
    assert mem.get("entities", ident)["status"] != "gone"
    world.before(solo, "room", "room", (W, H), 3)
    assert mem.get("entities", ident)["status"] == "gone"


def test_self_moving_npc_is_not_camera_drift(tmp_path):
    for tracked in (True, False):
        mem, world = _world(tmp_path / str(tracked)); world.tile_px = 16
        world.tracker.update(frame(0, (32, 16)))
        world.before(_per((40, 24), "girl"), "room", None, (W, H), 1)
        for x in range(33, 81): world.tracker.update(frame(0, (x, 16)))
        if not tracked: world.tracker.clear()
        world.observe_after(_per((88, 24), "girl"), frame(0, (80, 16)), False, "room", (W, H), 2)
        assert world.drift is None


def test_phase_snaps_seed_and_near_to_sprite_centres():
    tr = Tracker(); tr.phase = (8, 4)
    tr.seed([("a", (79 / W, 89 / H), True)], frame(0))  # 7 px off the centre (72, 84)
    assert (tr.tracks["a"]["x"], tr.tracks["a"]["y"]) == (72, 84)
    assert tr.near((82 / W, 84 / H)) == "a" and tr.near((104 / W, 84 / H)) is None  # 10 px off matches; 32 px off (two cells) does not

def test_no_phase_keeps_raw_point():
    tr = Tracker(); tr.seed([("a", (79 / W, 89 / H), True)], frame(0))
    assert (tr.tracks["a"]["x"], tr.tracks["a"]["y"]) == (79, 89)

def test_world_sets_phase_from_motion_locked_player(tmp_path):
    mem, world = _world(tmp_path)
    world._self_from_motion, world.self_spot = True, (72 / W, 68 / H)
    world.before(_per((40, 24), "girl"), "room", None, (W, H), 1)
    assert world.tracker.phase == (8, 4)
    world._self_from_motion = False
    world.before(_per((40, 24), "girl"), "room", "room", (W, H), 2)
    assert world.tracker.phase is None

def test_npc_beside_chair_keeps_own_record(tmp_path):
    mem, world = _world(tmp_path)
    world.tracker.update(frame(0, (32, 16)))
    def per(npc_x): return {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [88 / W, 72 / H]},
                                                                         {"name": "chair", "kind": "chair", "at": [40 / W, 24 / H]},
                                                                         {"name": "girl", "kind": "person", "at": [npc_x / W, 24 / H]}]}
    world.before(per(56), "room", None, (W, H), 1)
    chair, npc = world.entity_ids[1], world.entity_ids[2]
    world.before(per(56), "room", "room", (W, H), 2)
    assert npc != chair and mem.get("entities", npc)["name"] == "girl"
    assert "girl" not in (mem.get("entities", chair).get("aliases") or []) and mem.get("entities", chair)["name"] == "chair"

def test_pixel_track_hit_does_not_depend_on_perceived_kind(tmp_path):
    _, world = _world(tmp_path)
    world.tracker.update(frame(0, (32, 16)))
    world.tracker.seed([("npc", (56 / W, 24 / H), True)], frame(0))  # a live NPC track one tile from the chair
    assert world._hits({"entities": [{"name": "chair", "kind": "chair", "at": [40 / W, 24 / H]}]}) == {0: "npc"}
    assert world._hits({"entities": [{"name": "girl", "kind": "person", "at": [56 / W, 24 / H]}]}) == {0: "npc"}


def test_live_action_callbacks_and_idle_catchup_update_each_sequence_once():
    from types import SimpleNamespace

    class Live:
        def __init__(self):
            self.images = [Image.new("RGB", (W, H), (i, 0, 0)) for i in range(5)]
            self.items = [SimpleNamespace(generation="g", frame_seq=i, captured_monotonic=float(i), image=self._image(i)) for i in range(5)]
        def _image(self, i):
            image = self.images[i].copy(); image.info.update(generation="g", frame_seq=i, captured_monotonic=float(i)); return image
        def snapshot(self): return self.items[0]
        def wait_frame(self, after, *, timeout): return self.items[after.frame_seq + 1]
        def frames_after(self, after):
            frames = tuple(self.items[i] for i in range(after.frame_seq + 1, 5))
            return SimpleNamespace(frames=frames, latest=self.items[4], dropped=False)
        def press(self, key, frames=8, on_frame=None):
            for i in (1, 2): on_frame(self._image(i))
        def tick(self, frames=1): pass
        def screenshot(self): return self.items[0].image

    env, tracker, seen = Live(), Tracker(), []
    original_update = tracker.update
    def record(frame):
        seen.append(frame.info["frame_seq"]); original_update(frame)
    tracker.update = record
    tracker.wrap(env).press("A")
    env.snapshot()
    assert seen == [1, 2, 3, 4]


def test_live_history_gap_clears_tracks_before_using_latest_frame():
    from types import SimpleNamespace

    class Live:
        def __init__(self):
            self.images = [Image.new("RGB", (W, H), color) for color in ((1, 2, 3), (4, 5, 6))]
            self.items = [SimpleNamespace(generation="g", frame_seq=i, captured_monotonic=float(i), image=self.images[i]) for i in range(2)]
            for item in self.items: item.image.info.update(generation="g", frame_seq=item.frame_seq, captured_monotonic=item.captured_monotonic)
        def snapshot(self): return self.items[0]
        def wait_frame(self, after, *, timeout): return self.items[-1]
        def frames_after(self, after): return SimpleNamespace(frames=(self.items[-1],), latest=self.items[-1], dropped=True)
        def press(self, key, frames=8, on_frame=None): pass
        def tick(self, frames=1): pass
        def screenshot(self): return self.items[0].image

    env, tracker = Live(), Tracker()
    tracker.wrap(env)
    tracker.seed([("npc", (.5, .5), True)], env.items[0].image)
    env.snapshot()
    assert tracker.tracks == {}
    assert np.array_equal(tracker.arr, np.asarray(env.items[-1].image).astype(np.int16))


def test_live_snapshot_and_wait_consume_exact_returned_sequences_during_race():
    from types import SimpleNamespace

    class Live:
        def __init__(self):
            self.items = []
            for seq in range(4):
                image = Image.new("RGB", (W, H), (seq, 0, 0))
                image.info.update(generation="g", frame_seq=seq, captured_monotonic=float(seq))
                self.items.append(SimpleNamespace(generation="g", frame_seq=seq, captured_monotonic=float(seq), image=image))
            self.latest = 0
            self.race = False
        def snapshot(self): return self.items[self.latest]
        def wait_frame(self, after, *, timeout):
            self.latest = 3
            return self.items[3]
        def frames_after(self, after):
            latest = self.items[self.latest]
            frames = tuple(self.items[after.frame_seq + 1:self.latest + 1])
            if not self.race:
                self.latest = 2  # a newer frame arrives after the batch snapshot, before a second snapshot call
                self.race = True
            return SimpleNamespace(frames=frames, latest=latest, dropped=False)
        def press(self, key, frames=8, on_frame=None): pass
        def tick(self, frames=1): pass
        def screenshot(self): return self.snapshot().image

    env, tracker, seen = Live(), Tracker(), []
    original_update = tracker.update
    def record(frame):
        seen.append(frame.info["frame_seq"]); original_update(frame)
    tracker.update = record
    env = tracker.wrap(env)
    env.latest = 1
    snapshot = env.snapshot()
    assert snapshot.frame_seq == 1 and seen == [1]
    waited = env.wait_frame(snapshot, timeout=1)
    assert waited.frame_seq == 3 and seen == [1, 2, 3]
    assert tracker.live_seq == 3


def test_phase_seed_moves_off_repeating_floor_onto_the_sprite():
    floor = np.tile(blocks(16, 16, 4), (9, 10, 1)).astype(np.uint8)  # one floor tile repeated over the whole screen
    floor[64:80, 80:96] = SPRITE  # a sprite one tile right of the perceived cell (72, 72)
    tr = Tracker(); tr.phase = (8, 8)
    tr.seed([("a", (72 / W, 72 / H), True), ("s", (72 / W, 72 / H), False)], Image.fromarray(floor))
    assert (tr.tracks["a"]["x"], tr.tracks["a"]["y"]) == (88, 72) and (tr.tracks["s"]["x"], tr.tracks["s"]["y"]) == (72, 72)

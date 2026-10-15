"""Object tracks: pixels keep one id per thing between perceptions (0 requests). The vision model names things; this follows them.
Every emulated frame goes through Tracker.update (wrap() hooks an env). A static track moves with the camera only; an actor track also
centres on the pixels that changed beyond the camera shift near it (its own motion); lost = still and its patch is gone."""
import numpy as np
from agent.observer import changed, scroll

LOST_SCORE, GONE_FRAMES, BLOB = 30.0, 24, 6  # mean abs diff above which a still patch is no longer there; frames lost before "gone"; changed pixels that count as motion

class Tracker:
    def __init__(self, tile_px: int = 16):
        self.tile_px, self.tracks, self.frame, self.arr, self.ignore = tile_px, {}, None, None, []  # ignore: boxes (x0, y0, x1, y1) of screen-fixed motion, set by World
        self.phase = None  # (px, py): pixel phase of sprite centres, set by World from the player's motion-locked spot; None = unknown
    def clear(self): self.tracks = {}; self.frame = self.arr = None
    def _snap(self, x, y):
        # ponytail: assumes sprites at rest sit on the player's tile grid (true for grid games like Pokemon); a free-moving game needs a pixel search instead.
        if self.phase is None: return x, y
        t, (px, py) = self.tile_px, self.phase
        return px + round((x - px) / t) * t, py + round((y - py) / t) * t
    def _patch(self, x, y):
        h, w = self.arr.shape[:2]; r = self.tile_px // 2; x, y = round(x), round(y)
        return self.arr[y - r:y + r, x - r:x + r] if r <= x <= w - r and r <= y <= h - r else None
    def seed(self, items, frame=None):
        """items: (id, (fx, fy) screen fractions, is_actor). A live track near its perceived point stays as is; else it restarts there."""
        if frame is not None: self.frame, self.arr = frame, np.asarray(frame).astype(np.int16)
        if self.arr is None: return
        h, w = self.arr.shape[:2]
        for ident, (fx, fy), actor in items:
            x, y = self._snap(fx * w, fy * h); t = self.tracks.get(ident)
            if t and t["status"] == "live" and max(abs(t["x"] - x), abs(t["y"] - y)) <= self.tile_px / 2: t["actor"], t["own"] = actor, (0, 0); continue
            self.tracks[ident] = {"x": x, "y": y, "actor": actor, "status": "live", "lost": 0, "own": (0, 0), "patch": self._patch(x, y)}
    def near(self, at, used=()):
        """The live track within a tile of the screen fraction `at` (nearest first), or None."""
        if self.arr is None: return None
        h, w = self.arr.shape[:2]; x, y = self._snap(at[0] * w, at[1] * h)
        hits = [(max(abs(t["x"] - x), abs(t["y"] - y)), i) for i, t in self.tracks.items() if t["status"] == "live" and i not in used]
        d, i = min(hits, default=(2 * self.tile_px, None))
        return i if d <= self.tile_px else None
    def moved(self):  # ids whose own motion (camera removed) since the last seed passes a quarter tile
        return {i for i, t in self.tracks.items() if max(map(abs, t["own"])) > self.tile_px / 4}
    def gone(self): return {i for i, t in self.tracks.items() if t["lost"] >= GONE_FRAMES}
    def update(self, frame):
        arr = np.asarray(frame).astype(np.int16)
        if self.arr is None or not self.tracks:
            self.frame, self.arr = frame, arr; return
        if np.array_equal(arr, self.arr):  # still screen: nothing moves, but a thing already lost stays lost one more frame
            for t in self.tracks.values(): t["lost"] += t["lost"] > 0
            return
        shift = scroll(self.frame, frame, 2)
        if shift is None and changed(self.frame, frame) > .5: self.clear(); self.frame, self.arr = frame, arr; return
        dx, dy = shift or (0, 0)  # ponytail: ambiguous scroll = still camera; a flat screen mid-scroll would drift
        old, self.frame, self.arr = self.arr, frame, arr
        h, w = arr.shape[:2]; sx, sy = w - abs(dx), h - abs(dy)
        moved = np.zeros((h, w), bool)  # pixels the camera shift does not explain: things moving on their own
        if sx > 0 and sy > 0:
            moved[max(dy, 0):max(dy, 0) + sy, max(dx, 0):max(dx, 0) + sx] = np.abs(old[max(-dy, 0):max(-dy, 0) + sy, max(-dx, 0):max(-dx, 0) + sx] - arr[max(dy, 0):max(dy, 0) + sy, max(dx, 0):max(dx, 0) + sx]).max(-1) > 16
        for x0, y0, x1, y1 in self.ignore: moved[y0:y1, x0:x1] = False  # the player's own animation
        r = self.tile_px
        for t in self.tracks.values():
            if t["status"] != "live": continue
            px, py = t["x"] + dx, t["y"] + dy
            if t["actor"]:
                ys, xs = np.nonzero(moved[max(0, round(py) - r):round(py) + r, max(0, round(px) - r):round(px) + r])
                nx, ny = (max(0, round(px) - r) + xs.mean(), max(0, round(py) - r) + ys.mean()) if len(xs) >= BLOB else (px, py)  # it moved: its changed pixels centre on it
                c = self._patch(nx, ny)
                if t["patch"] is None or c is None or float(np.abs(c - t["patch"]).mean()) <= LOST_SCORE:  # still looks like itself
                    t["own"] = (t["own"][0] + nx - px, t["own"][1] + ny - py); px, py, t["lost"], t["patch"] = nx, ny, 0, c
                else: t["lost"] += 1  # gone from here: ride the camera until it turns up
            t["x"], t["y"] = px, py
            if not (0 <= px < w and 0 <= py < h): t["status"] = "offscreen"
    def wrap(self, env):
        """Feed every emulated frame to update(): press via on_frame, tick via a screenshot. Composes with later press wrappers."""
        press, tick = env.press, env.tick
        def tracked_press(key, frames=8, on_frame=None):
            def both(image): self.update(image); on_frame and on_frame(image)
            return press(key, frames, on_frame=both)
        def tracked_tick(frames=1):
            tick(frames)
            self.update(env.screenshot())
        env.press, env.tick = tracked_press, tracked_tick
        return env

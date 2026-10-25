# Frozen contracts

The single source of truth for every signature and data shape that a phase builds against. Phase packets link to these anchors and never restate them.

Orchestrators **never edit this file**. If a contract needs to change, they stop and propose the change in their packet's Result section. Only the user applies it.

**Conventions:**
- Points are `[x, y]` screen fractions in `[0, 1]`, with `[0, 0]` at top-left. Models answer on a 0–1000 grid, and code converts.
- Ids are short strings: `e_` for entities, `p_` for places, `r` for relations, `pt` for patterns, `k` for knowledge, `t` for tasks, `s` for shortcuts.
- `Image` means `PIL.Image.Image`, in RGB.
- Each section names the phase that builds it. A later phase may only **add** optional parameters or fields. Anything else is a contract change.

| Section | File | Built in |
|---|---|---|
| [model](#model) | `agent/model.py` | E1, hardened in E2 |
| [perception](#perception) | `agent/perceive.py` | E1 (E1b additions deferred) |
| [locate](#locate) | `agent/locate.py` | E1 (E1b additions deferred) |
| [env](#env) | `agent/env.py`, `agent/env_pyboy.py` | E2 (window adapter in E5) |
| [observer](#observer) | `agent/observer.py` | E2 |
| [skills](#skills) | `agent/skills.py` | E2 (`type_text` in E3) |
| [brief](#brief) | `agent/brief.py`, `agent/briefs/*.json` | E2 |
| [trace](#trace) | `agent/trace.py` | E2 |
| [memory](#memory) | `agent/memory.py` | E3 (patterns and shortcuts in E4) |
| [predict](#predict) | `agent/predict.py` | E2 (`effects`, `verdict`), E3 (`predict`), E4 (patterns) |
| [tasks](#tasks) | `agent/tasks.py` | E3 |
| [chooser and orchestrator](#chooser-and-orchestrator) | `agent/decide.py` | E3 (reflection in E4) |
| [run directory and truth](#run-directory-and-truth) | `agent/loop.py`, `scripts/` | E2 (truth) and E3 (loop) |
| [notebook and act](#notebook-and-act) | `agent/notebook.py`, `agent/act.py` | R1 P1 (W1 changes) |
| [world](#world) | `agent/world.py` | W0–W4 ([program-r1-wm.md](program-r1-wm.md)) |

## model

```python
@dataclass
class Reply:
    text: str
    data: dict | None          # parsed JSON object, or None
    ms: float
    prompt_tokens: int | None
    output_tokens: int | None

class ModelUnavailable(RuntimeError): ...       # E2: raised once the retry budget is spent

class Ollama:
    def __init__(self, model: str, *, temperature: float = 0.2, max_tokens: int = 4096,
                 num_ctx: int = 16384, timeout_s: float | None = None,
                 log: Callable[[dict], None] | None = None,          # `log` added in E2
                 cache_dir: Path | None = None) -> None              # E2: response cache
    calls: int                                                         # E2: successful calls so far
    def ask(self, system: str, user: str, images: list[Image] = (), schema: dict | None = None,
            *, role: str = "") -> Reply                                # `role` added in E2
```

E2 hardens `ask`:
- **Retries:** on HTTP 429, HTTP 5xx, connect errors and timeouts, it backs off 5, 10, 20 … seconds, capped at 300 s per wait, and honours `Retry-After`. Once `MAX_WAIT_S = 900` of total waiting is spent, it raises `ModelUnavailable`.
- **Logging:** each finished call sends `log` one row: `{"role", "model", "ms", "prompt_tokens", "output_tokens", "retries", "ok", "cached"}`.
- **Response cache (E2).** With `cache_dir` set, `ask` keys each call by `sha256(model, system, user, schema, image bytes)` and stores the reply as one JSON file. A hit returns the stored `Reply` with no network call, and is not counted in `calls`. Replies with `data is None` are **not** cached. Writes go to a temp file, then `os.replace` (atomic). Small frames (long side < 1024) are sent as PNG, which is lossless for pixel art; larger frames as JPEG. The emulator is deterministic, so re-running from a checkpoint costs nothing until the run diverges. All runners use `.cache/model/`, which is gitignored.

## perception

```python
def perceive(model: Ollama, image: Image, context: str = "", *,
             with_rulers: bool = False) -> tuple[Perception, Reply]

Perception = {"screen": str, "text": list[str],
              "entities": list[{"kind": str, "name": str, "at": [x, y]}],
              "affordances": list[Affordance]}
Affordance = {"verb": str, "target": str, "at": [x, y], "why": str, "to": [x, y],   # "to" only for drag
              "placement": str}   # optional (E3R3, approved 2026-10-11): top wall | bottom wall | left wall | right wall | room edge | screen edge | middle
VERBS = ("approach", "interact", "select", "click", "drag", "advance", "cancel", "type", "press")
```

- `normalize()` never raises, and drops any row without a usable point.
- The player character should come back as an entity with `kind == "player"`. The E2 prompt line asks for it, and `approach` relies on it.
- For `verb == "press"`, `target` holds the input name, for example `"START"`.
- E2 adds one prompt line: walkable paths or exits at the screen edges are listed as `approach` affordances, with the target `"exit <direction>"`. Without that line, curiosity never finds the way out of a town.

**Added in E1b (deferred, not built yet):**

```python
EDGE_STRIPS: tuple[Box, ...]                 # ((0, 0, 1, .15), (0, .85, 1, 1)): game-neutral HUD regions
DEDUP_DIST = 0.03
def second_look(model, image, box: Box, known: list[Affordance]) -> tuple[list[Affordance], Reply]
def merge(base: list[Affordance], extra: list[Affordance], min_dist: float = DEDUP_DIST) -> list[Affordance]
```

- `second_look` re-lists one region in a magnified view. It returns only rows that `merge` would add, with points in full-frame fractions.
- `merge` is pure. It appends each extra row that is farther than `min_dist` from every base point and whose target name differs (case-insensitive).
- Whether `second_look` runs at runtime is decided by the E1b gate. Check `handoff-notes.md` for the outcome.

## locate

```python
Box = tuple[float, float, float, float]            # x0, y0, x1, y1 as frame fractions
ZOOM_FRAC = 0.25; ZOOM_LONG_SIDE = 768
def crop_box(size, at, frac=ZOOM_FRAC) -> tuple[int, int, int, int]
def magnify(image, box: Box) -> tuple[Image, tuple[int, int, int, int]]                 # E1b (deferred)
def to_frame(p: list[float], px_box: tuple[int, int, int, int], size) -> list[float]    # E1b (deferred): 0-1000 crop point -> frame fractions
def zoom(image, at, frac=ZOOM_FRAC) -> tuple[Image, tuple[int, int, int, int]]
def refine(model, image, target: str, at: list[float], *,
           retry_frac: float | None = None) -> tuple[list[float] | None, Reply]
```

- (E1b, deferred: today's `refine` has no `retry_frac`.) If the first answer is "not visible" and `retry_frac` is set, `refine` retries once with a crop of side `retry_frac`. The returned `Reply` is the last one, with `ms` summed over both calls.
- Rulers and `snap_to_text` exist only to reproduce E1. They are never used at runtime.
- Tile games snap with `observer.snap` rather than `refine`; [observer](#observer) has it.

## env

Defined in `agent/env.py`:

```python
class Env(Protocol):
    name: str                      # game id, matching the brief file name
    pointer: bool                  # True: click() works (mouse games); False: pad or keys only
    def screenshot(self) -> Image  # native resolution, RGB, no scaling
    def inputs(self) -> tuple[str, ...]
    def press(self, key: str, frames: int = 8, on_frame: Callable[[Image], None] | None = None) -> None
        # hold, then release; never leaves a key held. on_frame(screenshot) runs after every
        # emulated frame while held, plus 8 frames after release, so callers can track scroll frame by frame
    def click(self, at: list[float]) -> None             # pointer envs only, else NotImplementedError
    def tick(self, frames: int = 1) -> None              # advance time (emulator: run frames; window: sleep frames/60 s)
    def listen(self, fn: Callable[[Image | None, int], None]) -> None   # optional, r18 (approved 2026-10-24)
        # fn(frame, seq) after EVERY emulated frame, in emulation order: press frames, every frame of tick(n), and
        # (live) the frames emulated while the agent thinks. seq rises by 1 per frame. fn(None, seq) marks a
        # discontinuity (load, restore, new generation). Live envs call fn on the emulator thread.
```

- **r18 (approved 2026-10-24):** an env without `listen` is still valid; the tracker then feeds the odometer from `press` on_frame and `tick`, and frames emulated between calls are unknown motion (a gap), never zero.

- `press` and `click` may raise (for example when the window lost focus, or the kill switch is on). The loop then writes a checkpoint and exits; it never retries input blindly.
- **Pad input names** are `UP DOWN LEFT RIGHT A B START SELECT`.
- **Keyboard input names** (E5) are virtual-key names such as `ENTER`, `ESCAPE`, `SPACE`, `LEFT` and `A`.

Defined in `agent/env_pyboy.py`:

```python
def find_rom() -> str              # $POKERED_ROM if set, else the first Rom/*.gb (Rom/ is gitignored); raises if none

class PyBoyEnv:                    # implements Env; pointer = False
    def __init__(self, rom: str | None = None, *, state: str | None = None, name: str = "pokemon_red") -> None   # rom defaults to find_rom()
    def save(self, path: str) -> None    # not on Env: the runner checkpoints, never agent/loop.py
    def load(self, path: str) -> None    # runner only (resume)
    def ram(self, addr: int) -> int      # EVALUATOR ONLY: not on the Env protocol
    def close(self) -> None
```

**Rule, enforced by `tests/agent/test_boundaries.py`:**
- Outside `agent/env_pyboy.py`, no file under `agent/` may contain `.ram(`, `.memory[`, `load_state`, `save_state`, `.save(` or `.load(` on an env.
- No `agent/` file may import from `loop/`. Check import statements with a regex such as `^\s*(from|import)\s+loop\b`, not a substring.
- PyBoy notes: `screen.image` is a live view, so always use `.convert("RGB")` (a copy). `save_state` and `load_state` take file objects. Run with `window="null"` and `set_emulation_speed(0)`.

## observer

Defined in `agent/observer.py`:

```python
@dataclass
class Settle:
    frame: Image
    settled: bool                  # False means max_frames was hit
    waited: int                    # frames ticked
    blank_seen: bool               # a near-uniform frame (fade or map load) occurred while waiting

def changed(a: Image, b: Image, thresh: int = 16) -> float      # share of pixels whose max channel diff > thresh
def wait_settled(env: Env, *, quiet: int = 24, max_frames: int = 600) -> Settle
def scroll(a: Image, b: Image, max_shift: int = 4) -> tuple[int, int] | None
    # shift of b relative to a, by brute-force search over [-max_shift, max_shift]^2 minimising mean abs diff
    # on the overlap. None = unsure (the best and second-best shifts are too close). (0, 0) = confidently no shift.
    # Meant for consecutive frames: callers add the shifts up during a move (via Env.press on_frame).
def for_model(img: Image, long_side: int = 640) -> Image        # nearest-neighbour upscale; no-op if already larger
def snap(at: list[float], size: tuple[int, int], tile_px: int, phase: tuple[int, int] = (0, 0)) -> list[float]
def cut(a: Image, b: Image, blank_seen: bool = False) -> bool    # scene cut: blank_seen, or changed(a, b) > 0.5 with no consistent scroll
```

- **Settling:** a frame is quiet if its hash equals a frame already seen in the last `quiet` frames. Animated tiles cycle through the same frames, while text printing and fades don't. The screen is settled after `quiet` consecutive quiet frames. **Never** settle on a near-uniform frame (≥ 95% one colour).
- **r18 (approved 2026-10-24):** a `Settle` is evidence for the skill that waited (quiet, blank, arrival frame); the player's position never comes from a `Settle`. Position comes from the per-frame odometer (see [world](#world)), so a settle that stops at `max_frames` mid-walk loses no motion.
- **Places before E3 memory exists:** callers keep a counter and start a new place id on every `cut()`. From E3 on, `Memory.match_place` uses the same `cut` as its trigger (see [memory](#memory)).

## skills

Defined in `agent/skills.py`:

```python
@dataclass
class SkillResult:
    status: Literal["done", "failed", "blocked"]
    reason: str
    inputs: int                    # presses or clicks sent
    calls: int                     # model calls made inside the skill
    before: Image                  # settled frame before
    after: Image                   # settled frame after
    scroll: tuple[int, int]        # total view shift during the skill, in pixels (summed frame by frame)
    blank_seen: bool               # a fade or blank frame occurred (feeds cut())
    moves: list[tuple[str, tuple[int, int] | None]] = field(default_factory=list)
        # E3R4: one entry per direction press, (direction, measured content shift in px or None if unsure);
        # when the skill ends in a scene cut, the last entry is the press that caused it
        # r18 (approved 2026-10-24): moves are press evidence only (dir, shift, no_response, self_at, plus "at" = the
        # odometer frame seq when the press started). World uses them for bumps, walls, cut direction and self-lock;
        # the position comes from the odometer. Forced moves made from a Settle ("forced", "after_cut") are removed.

@dataclass
class Ctx:
    env: Env
    model: Ollama
    tile_px: int | None = None     # the smallest non-zero |scroll| seen per step; None until known
    move_frames: int = 16          # press length that moves one tile. Calibrated in E2, recorded in handoff-notes
    max_inputs: int = 30           # hard cap per skill call

def approach(ctx, target_at: list[float], player_at: list[float] | None) -> SkillResult
def interact(ctx, target_at: list[float], player_at: list[float] | None) -> SkillResult
def select(ctx, item: str) -> SkillResult
def advance(ctx) -> SkillResult
def cancel(ctx) -> SkillResult
def press(ctx, key: str) -> SkillResult
def type_text(ctx, text: str) -> SkillResult         # E3
def wait_until(ctx, max_frames: int = 240) -> SkillResult

def run_affordance(ctx, aff: Affordance, perception: Perception, text: str | None = None, *, exit: bool = False, route: dict | None = None) -> SkillResult
    # route=None (E3R4): {"dirs": [direction, ...], "push": direction | None}. Press dirs in order (no greedy walk);
    #   a confident (0, 0) on a route press returns blocked "route blocked". In exit mode, push the given direction
    #   first, then the existing side-step sequence around it. route=None keeps the E3R3 greedy walk.
    # exit=True (E3R3): approach in exit mode (step onto, push outward) whatever the target is called; no refine call (user-approved 2026-10-11: walk the tile-snapped point, a miss shows as no scene cut)
    # verb -> skill dispatch; `text` is used by verb "type"
```

- On a pointer env, `approach` and `interact` click (after `refine`), and `player_at` is ignored.
- On a pad env, `approach` returns `blocked` if `player_at` is None. A move whose scroll is `None` is re-measured, never treated as blocked. A confident `(0, 0)` scroll is blocked.
- When the delta is 0 and no cut happened, `approach` presses once more in the last direction, because door mats and edge warps trigger on a push.
- `select` uses one model call (read the items and cursor), then the presses, then A. The step's own post-perception verifies it.
- A skill never loops past `ctx.max_inputs`. It returns `failed` or `blocked` with a reason instead.

## brief

```python
GENERAL: str                                  # general prompt (spec §3)
DEFAULTS = {"name": "Hand"}                  # the player's own name, for every game
def load_brief(game_id: str) -> str           # agent/briefs/<game_id>.json -> compact text, ≤ 8 KB
def defaults(game_id: str) -> dict            # DEFAULTS merged with the brief's optional "defaults" object, e.g. {"rival": "Blue"}
```

A brief is JSON `{"controls": [str], "notes": [str], "defaults": {role: name}}`, ≤ 8 KB. It has no objective or strategy fields, so it can't drift into walkthrough text. `GENERAL` plus the brief go once into the Perceiver context. The Chooser gets `GENERAL`, the brief's controls and `defaults` (E3R3, approved 2026-10-11).

## trace

`agent/trace.py`:

```python
class Trace:
    def __init__(self, path: Path) -> None
    def write(self, row: dict) -> None                   # append + flush, one JSON line
    def tail(self, n: int) -> list[dict]                 # last n rows, without loading the whole file
```

A trace row, one per step. Every key is always present; use `null` when a value doesn't apply.

```json
{"step": 331, "t": 1730000000.1, "place": "p_pallet", "screen": "overworld", "task": "t4",
 "frame": "frames/000331.png", "options": 6,
 "choice": {"i": 2, "verb": "approach", "target": "lab door", "id": "e_lab_door"},
 "pred": {"basis": "pattern", "id": "pt3", "effect": "place_changes", "detail": "p_lab", "conf": 0.67},
 "used": ["pt3", "r17"],
 "skill": {"status": "done", "reason": "", "inputs": 5, "calls": 0},
 "outcome": {"effects": ["view_scrolled", "place_changes"], "basis": {"view_scrolled": "pixel", "place_changes": "pixel"}, "to": "p_lab"},
 "verdict": "hit", "calls": 2, "ms": 7400, "shortcut": null}
```

E3R4 adds `"tile": [x, y]` (the player's place-frame tile after the step, or null) and `"drift_fix": [dx, dy]` or null. W0 (approved 2026-10-12) adds `"entities": [{"id", "name", "kind", "at"}]` (the before-perception entities) and `"scroll": [dx, dy]` (the skill's total content scroll) or null; W4 replays anchors from them. r18 (approved 2026-10-24) adds `"odometer": {"frames": int, "px": [dx, dy], "uncertain": int, "cuts": int, "gap": bool, "dropped": int}` (what the odometer drained this step; `dropped` = segments drained but not folded: middle segments of a multi-cut step and the part after a cut found in the start-of-step drain; amended 2026-10-24) or null. E3R5 (approved 2026-10-11) adds `"ranker": {"state": ..., "answer": ...}` (what the local ranker was asked and replied) on steps where it was asked, else null.

- `effects` and `pred.effect` come from `EFFECTS = ("place_changes", "text_appears", "screen_changes", "entity_appears", "entity_disappears", "view_scrolled", "no_change")`.
- `verdict` is one of `hit`, `partial`, `surprise`, `miss` or `unobservable`.
- `basis` per effect: `pixel` (from scroll, cut, a blank frame or a changed region) or `model` (only from a difference between the VLM's lists). Only `pixel` effects may produce `src = "observed"` facts.
- A step with zero affordances runs `wait_until`, records the verdict `unobservable`, and counts toward `Stuck`.

## memory

`agent/memory.py`. Stores are JSONL upsert files under `<run>/memory/`. The last row for an `id` wins. Files are replayed on start. **Any store name is accepted**, so E4 adds stores without changing the code. Record shapes follow spec §8, with the changes below:

```
entities   {"id","kind","name","place","at","tile":[x, y] | absent,"attrs":{k:{"v","src","step"}},"first","last","seen","src"}   # tile: E3R4, place frame
tiles      {"id":"<place>@x,y","place","x","y","floor":true|false|null,"blocked":[dirs],"pushed":[dirs],
            "exit":{"dir","to","arrive":[x, y],"src":"observed|inferred"} | null,"step"}                           # E3R4, see space
places     {"id","name","screen","state":"candidate|place","visits","landmarks":[[name, kind]],"edges":[{"verb","via","to","n","ok"}],"blocked":[...]}
relations  {"id","s","p","o","src","sup","con","ev":[steps],"state":"live|refuted|expired","why","step"}
knowledge  {"id","text","src":"game_text|observed|model","step"}
patterns   {"id","s","p","o","verb","sup","con","inst":[relation ids],"state":"candidate|promoted|demoted","name"}   # E4; s/o are normalized kinds (casefold, text before ":", singular, game-neutral aliases; a place id -> "place"); only observed pixel hits count, never model or inferred rows
shortcuts  {"id","place","seq":[{"verb","target"}],"runs","state":"live|broken"}                                # deferred (was E4)
```

`src` is one of `observed`, `text` or `model`.

```python
class Memory:
    def __init__(self, root: Path) -> None
    def get(self, store: str, id: str) -> dict | None
    def rows(self, store: str) -> list[dict]
    def upsert(self, store: str, row: dict) -> dict
    def match_place(self, perception: Perception, step: int, *, prev: str | None, cut: bool,
                    via: str | None = None, exit: tuple[int, int, str] | None = None, via_id: str | None = None) -> str
        # E3R5, approved 2026-10-11: `via` names compare without an `exit:` prefix (casefolded). On a cut, before the landmark
        # fallback: if `via_id` (the entity id the loop resolved for the via) has a live `leads_to` relation to a known place, that place wins.
        # E3R4: on a cut, a known exit tile (prev, x, y, dir) in the tiles store wins first, before the via-name edge.
        # no cut -> prev (continuity). On a cut: the known edge (prev, via) -> its destination; else
        # landmark (name, kind) overlap >= 0.6 with a known place of the same screen type; else a new
        # *candidate* place. A candidate becomes a place on its first view_scrolled; until then it is a
        # screen (battle, menu, naming) and a move into it records screen_changes, not place_changes.
    def observe(self, perception: Perception, place: str, step: int, tiles: dict[int, tuple[int, int]] | None = None) -> dict[int, str]
        # perception entity index -> entity id. E3R4: with tiles, a same-name record whose stored tile is > 1 tile away
        # is a different thing; the matched record's tile is updated. Records without a tile match by name as before.
    def resolve(self, name: str, place: str, tile: tuple[int, int] | None = None) -> str | None   # affordance target string -> entity id
        # E3R5, approved 2026-10-11: when several records match and `tile` is given, the one whose stored tile is nearest and within 1 tile; else None as before.
    def add_edge(self, src_place: str, verb: str, via: str, dst_place: str, ok: bool) -> None
    def relate(self, s: str, p: str, o: str, *, src: str, step: int, hit: bool | None = None) -> dict
    def know(self, text: str, *, src: str, step: int) -> str                               # dedup by normalized text
    def expire(self, step: int) -> None                                                    # model claims > 200 steps unobserved
    def view(self, caller: Literal["perceiver", "chooser", "orchestrator"], ctx: dict) -> tuple[str, list[str]]
        # ctx = {"place", "task", "on_screen": [entity ids], "recent": Trace.tail(5), "step"}
        # The trace-derived parts (last actions, already-failed affordances here, surprises) come from ctx["recent"].

def conf(rel: dict) -> float     # (sup + 1) / (sup + con + 2)
```

- **Identity:**
  - World things are keyed by `(name, place)`, with `kind` kept as an attribute (the model's kinds drift: "mom", "woman", "npc").
  - Recurring types (`menu_item`, `move`, `item`, `option`) are keyed by `(kind, name)` globally.
  - Edit distance ≤ 1 counts as the same name. A tie creates a `#2` record.
  - The Perceiver view lists the known names and kinds for this place, so the model reuses them.
  - `route()` and `compact()` are deferred until something calls them.
- **Relations from effects:** `PRED = {"place_changes": "leads_to", "text_appears": "says", "screen_changes": "opens", "entity_appears": "reveals", "entity_disappears": "removes", "view_scrolled": "moves_view", "no_change": "does_nothing"}`. The relation is `(target id, PRED[effect], to or detail)`, and `predict` reads `PRED` in reverse. It lives in `agent/predict.py`.
- **Refuted** means `con ≥ 2` and `con > sup`. An observed fact with `sup ≥ 3` is not overturned by one contradiction.
- **2026-10-12 (approved, W1/W3/W4):** entities gain `status` (`seen|remembered|stale|gone`), `misses`, `tier` (`interest|landmark|transient`) in W3, and `anchor` (`screen|world|actor|self|unknown`), `region`, `transient` in W4. Places gain `offset` on merged rows (W1). New store `seen` `{"id": place, "cells": ["x,y"], "step"}` (W3). `match_place` stops importing `agent.space` (W0). Merging established places lives in `World.merge` (W1).
- **`view`:**
  - It returns the rendered text and the list of ids it showed.
  - Budgets: perceiver ≈ 400 tokens, chooser ≈ 1.2k, orchestrator ≈ 3k. Estimate tokens as `len(text) / 4`.
  - Model claims render with "(unverified)". At most two refuted items render, under "don't believe".
  - Selection order: on screen or in this place, then tied to the task, then recent, then important.

**OM, OM2, OM3 additions (2026-10-13; OM3 is the final behaviour):**
- `match_place` returns `prev` for any screen other than `world`; before the first navigable screen one unnamed run-start place (no `screen`) stands in; navigable places mint as `place`, `place_2`, ...; `Memory.excursion` (a dialogue closing with a scene cut returns to the place it left); `place_name(row)`, `place_aliases(row)`, `SCREEN_NAMES`.
- `_nameable`: a name equal to a screen type, of the form `<label> [<id>]`, or of the form `place <n>` is never stored as a name or alias.
- A failed action aimed at a remembered thing (or at the remembered thing on the tile a button press faces) writes `(thing, does_nothing, "")` with `hit=False`; it is refuted by the existing rule. A later non-failed use of that thing resets it to live (con 0, ev empty). `says` rows get `why = "says the same as before (step N)"` on a repeat.
- Store `crossings`: `{"id": "<place>@x,y><other>", "n", "last"}`, one row per exit crossing, counted on both sides by `World` (see world).

## space

E3R4 (approved 2026-10-11). `agent/space.py`, pure functions over `Memory`'s `tiles` store. No model calls. Design and rationale: [spatial-memory.md](spatial-memory.md).

```python
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
def tile_of(at: list[float], player_at: list[float], pos: tuple[int, int], size: tuple[int, int], tile_px: int) -> tuple[int, int]
    # place-frame tile of a perceived point: pos + (screen tile of at - screen tile of player_at), screen tiles via observer.snap
def fold(mem, place: str, pos: tuple[int, int], moves: list, tile_px: int, step: int) -> tuple[int, int]
    # walks the moves from pos: a shift of k tiles in dir -> k floor tiles; a confident (0, 0) -> dir added to "blocked"
    # on the current tile; None -> nothing recorded. Returns the end tile. The player moves opposite to the content shift.
def mark_exit(mem, place: str, tile: tuple[int, int], dir: str, to: str, arrive: tuple[int, int], step: int) -> None
    # observed exit on place@tile, plus an inferred reverse exit on to@arrive with the opposite dir
def exit_to(mem, place: str, tile: tuple[int, int], dir: str) -> dict | None
def route(mem, place: str, start: tuple[int, int], goal: tuple[int, int]) -> list[str] | None
    # BFS over tiles; unknown tiles are passable, blocked dirs are not; the goal tile is always enterable
def outward(mem, place: str, tile: tuple[int, int], placement: str | None = None) -> str
    # top/bottom/left/right wall placement -> UP/DOWN/LEFT/RIGHT; otherwise away from the centroid of known floor
def frontier(mem, place: str) -> list[tuple[tuple[int, int], str]]
    # known floor tiles on the map boundary whose outward dir is neither blocked nor pushed
def describe(mem, place: str, pos: tuple[int, int]) -> str
    # one line for views: "Here: <place> (x,y). Exits: (x,y) DIR -> <place name>. Untried edges: n."
```

- **Player position** `pos` is a place-frame tile held by the loop and checkpointed in `harness.json`. The first arrival in a place sets `pos = (0, 0)`; a known exit sets `pos` to its `arrive` tile.
- **Drift re-anchor:** after `observe`, the median (stored tile - projected tile) over matched non-player records; if it is non-zero on two consecutive perceptions, shift `pos` by it and set the trace `drift_fix`.
- **Exit filter:** a navigation candidate whose tile is within 1 of a known exit tile in this place leading to an already visited place is excluded, alongside the relation filter.
- **2026-10-12 (approved, W0–P4):** `agent/world.py` becomes the only caller of these functions on the act path (W0). W2 adds `fold(..., actors: frozenset[tuple[int, int]] = frozenset())`: a confident `(0, 0)` press into an actor tile records nothing. P4 removes `describe` and the exit filter below together with the gated path.
- **Not in E3R4:** landmark matching by tile; perception skipping on known views; fixed-camera games (pos changes only from scroll).
- **r18 (approved 2026-10-24), odometer:** `pos` changes only from the World's odometer drain: camera scroll measured on every emulated frame, whoever caused it (the agent's press or the game), split into segments at blank frames, instant cuts and gaps. World turns the summed pixels into tile steps by rounding the running sum (never per frame or per press) and feeds them to `fold`, merged in frame order with press bumps (confident `(0, 0)` moves) at their "at" seq. Each drained segment carries public `"start"` and `"end"` (first and last frame seq it covers, inclusive; amended 2026-10-24) so drains `through` a seq and bump merges by `"at"` are exact. Fixed-camera games stay out of scope: the odometer reports no camera motion there, and the self-sprite term is a later round.

## predict

`agent/predict.py`:

```python
EFFECTS: tuple[str, ...]                        # as in [trace](#trace)
def predict(mem: Memory, aff: Affordance, target_id: str | None, place: str) -> dict | None
    # {"basis": "instance"|"pattern", "id", "effect", "detail", "conf"}, or None (the chooser guesses).
    # Model-sourced relations are never used.
def effects(before: Perception, after: Perception, res: SkillResult, place_before: str, place_after: str) -> dict
    # E2. {"effects": [...], "basis": {effect: "pixel"|"model"}, "to": place_after or None}. Pure code, no model call.
    # text_appears is "pixel" only if the frame also changed in the region where the new text sits.
def verdict(pred: dict, outcome: dict) -> str
    # E2. hit | partial | surprise | miss | unobservable
```

## tasks

`agent/tasks.py`. Task records live in `<run>/memory/tasks.jsonl` and use the same upsert format.

```
task  {"id","goal","done_when":[Cond],"fail_when":[Cond],"parent","status":"active|done|failed|blocked",
       "budget","used","attempts","last_failure","src":"default|orchestrator|user"}
Cond  {"place_is": place id or name} | {"entity_known": name} | {"text_seen": substring}
    | {"relation": [s, p, o]} | {"steps_over": n}
    | {"new_place": true}   # E3R3: true once a pixel-verified ok edge reaches a place beyond those known when the task was created; latches
```

```python
class Tasks:
    def __init__(self, mem: Memory) -> None
    def add(self, goal: str, done_when: list, fail_when: list = (), *, parent: str | None = None,
            budget: int = 100, src: str = "orchestrator") -> dict
    def active(self) -> dict                       # deepest active task; creates the default curiosity task if none
        # E3R3: goals are set by the orchestrator model (decide.plan). The fallback default, used only when no model goal is active,
        # is "Reach a place not visited yet", done_when [{"new_place": true}], budget 40, re-armed when done or failed.
        # An active model goal sets the default to "blocked". Every model goal carries fail_when steps_over (default 40).
    def check(self, step: int) -> list[dict]       # status changes; done needs the condition true on 2 consecutive settled steps
    # wake: REVISED 2026-10-11 (user-approved E4 re-sequencing). Tasks.wake is not created. The wake reasons live in
    # the loop's existing plan trigger (pending_plan_reason): goal ended | Stuck reason | new game text (E3R3), plus
    # "surprise" | "new_place" (verified edge to a new place) | "periodic" (every 50 steps) in E4; 15-step throttle kept.
    # The trace row gets "wake": reason | null.
class Stuck:
    def update(self, row: dict) -> str | None      # "no_change" (8 steps), "same_action" (3 times on the same frame), "budget",
        # "no_progress": 20 steps with no first visit of a world place and no first verified (place, next_place) edge
        # (QS 2026-10-15; was 25 with any verified edge). State adds seen_edges (old checkpoints: empty set).
    def progress(self) -> None                     # QS: resets the no_progress counter (game text, quest closure)
```

## chooser and orchestrator

`agent/decide.py`:

```python
@dataclass
class Choice:
    i: int                          # index into perception["affordances"]
    effect: str                     # one of EFFECTS
    detail: str
    used: list[str]                 # memory ids the model says it used
    why: str
    called: bool                    # False when no model call was needed
    text: str | None = None         # for verb "type": the text to enter, taken from `defaults`

def choose(model: Ollama, view: str, perception: Perception, task: dict,
           preds: list[dict | None], defaults: dict) -> Choice
def code_choice(perception: Perception, view_ids: list[str], recent: list[dict]) -> Choice
    # E3: 0-call baseline (untried first, then least tried); used by compare_chooser
def escalate(model: Ollama, view: str, reason: str) -> list[dict]
    # E3: new subtasks as Tasks.add kwargs
def plan(model: Ollama, view: str, reason: str, last_goal: str = "", last_end: str = "", screen_text="") -> list[dict]
    # E3R3: model-set goals (<= 2 tasks). E4 (REVISED 2026-10-11): reflection is this same call. Its schema also accepts
    # "pattern_names": {pattern_id: name} and "notes": str, returned alongside the tasks; there is no separate reflect().
    # The prompt states the JSON answer shape (the cloud model ignores the format schema).
```

- `choose` skips the model call when there is exactly one non-`press` affordance (most dialogue boxes).
- For a curiosity task, untried affordances rank first in the view. That is a ranking rule, not extra task records.
- Reflection (`plan`'s `pattern_names`) never promotes a pattern; it only names existing pattern ids.
- `escalate` has no callers since E3R3's `plan`; it is a deletion candidate (E4 reports it).

## run directory and truth

```
runs/<run_id>/
  frames/NNNNNN.png      settled frame before each step (native resolution)
  trace.jsonl            the agent's trace (see trace above)
  calls.jsonl            one row per model call (Ollama `log`)
  chooser.jsonl          E3: {step, view, affordances, task, preds, choice} for each step where choose() called the model; used for the offline Chooser comparison
  memory/*.jsonl         the stores
  truth.jsonl            EVALUATOR ONLY: written by the runner from env.ram(); never read by agent/
  checkpoint/<step:06d>/ pyboy.state + memory/ copy + harness.json (step, tile_px, move_frames, prev place, Stuck counters, active task, E3R4 pos); every 25 steps
  summary.json           written by scripts/eval_run.py
```

- **A Pokémon truth row** is `{"step", "map", "x", "y", "party", "battle", "text_box", "player_name", "rival_name"}`. Names are decoded from the game's charmap.
- **Every runner** takes `--max-calls N`. When `Ollama.calls` reaches N, the runner writes a checkpoint and exits with **code 3**. On `ModelUnavailable` it checkpoints and exits with **code 4**: the orchestrator reports to the user and stops, rather than polling a free-tier window that may last hours.
- **`--steps N` always means N more steps.** `--resume [--from-step K]` loads the newest checkpoint, or the newest at or before K, then **truncates** `trace`, `chooser`, `calls` and `truth` rows with `step >` the checkpoint step before continuing.
- **Checkpointing belongs to the runner** (`scripts/run_agent.py`), not to `agent/loop.py`. The loop exposes its harness state through `state()` and `restore()`.
- **RAM addresses** live only in `scripts/truth_pokered.py`. They are taken from the pokered disassembly symbol file, and E2 verifies them.
- **2026-10-12 (approved):** `harness.json` gains World state (`World.state()`: the existing `pos`, `drift`, `drift_ref`, `entity_tiles`, `entity_ids`, `anchors` keys, plus `cam_px` and anchor evidence from W4). Truth rows gain `map_h`, `map_w` (P3, evaluation only).
- **Resuming** is allowed only when the PyBoy state and the memory are loaded together from one checkpoint. That continues the same run. Loading a state without its memory is debugging, not a run.


## notebook and act

R1 P1 (built 2026-10-12, behind `--act`; P4 makes it the only path). W1 changes approved 2026-10-12. Notebook replaced by QS quest state (merged 2026-10-15 from [QS.md](QS.md) §9; design [quest-state.md](quest-state.md)).

```python
# agent/notebook.py — quest state at <memory root>/notebook.json (v2) + notebook.history op log; a v1 file migrates in memory until a mutation
SECTIONS = ("facts", "beliefs", "quests", "notes", "chapters")  # plus the code-written event `log`
MAX_OPEN = 5                                                     # extra open quests are parked
class Notebook:
    def __init__(self, root: Path, *, buttons=(), labels=None) -> None   # buttons: env.inputs(); labels(): current canonical place label(s)
    focus: str | None                                            # read-only: key of the first open quest
    def apply_plan(self, plan: "Plan", step: int, *, buttons=()) -> dict  # reconcile + invariants; {"quests_changed", "beliefs_changed", "flags"}; reorders count
    def run_checks(self, step: int) -> list[str]                 # reach/speak/text checks on every open quest; keys closed this step
    def apply_chapter(self, chapter: dict, step: int) -> bool    # guard: every open quest and held belief kept or in open_leads; False keeps notes
    def note(self, step: int, text: str) -> None                 # code event log (unchanged)
    def render(self, limit: int = 6000) -> str                   # planner view: facts, beliefs (incl. ruled out), quests, chapters, current notes
    def render_focus(self, limit: int = 4000) -> str             # act view: top quest, other open quests one line each, facts, recent log

# agent/act.py — one structured model call per step
@dataclass
class Act: place: str; ops: list; action: dict | None; text: str | None; expect: str; why: str
def act(model, image, perception: dict, notebook: str, facts: str, flags: list[str], controls: str,
        prefs: dict, buttons: list[str], places: list[str] = ()) -> Act     # W1 adds `places` (known place names)
    # action: {"i": option index} | {"go_to": name} | {"buttons": [<= 8 input names]}; anything else -> None
    # W2: go_to may name an on-screen thing, a remembered thing, or a known place (resolved by World)
```

- The reply's `place` becomes an alias on a code-owned place via `World.name` (W1).
- `flags` carries code observations as text, never commands: loop/stall detectors, `merge_pending: <name>`, `go_to "<name>": not in memory`, `travel: <reason>`.

**OM, OM2, OM3 additions (2026-10-13; OM3 is the final behaviour):**
- `act(..., options=None)`: with options the prompt lists `Options:` (replaces `Things on screen:`). Each option is printed as `<n>. <text>`, where text is the label alone when its first three words already hold the tool word (spaced or raw), else `<tool> <label>` (`_option_text`); the tool word is printed once.
- Reply `{"i": n}` returns that option's `action` (a copy) and sets `Act.option`; anything else parses as before. `why`/`expect` are read from inside `action` when absent at the top level. Rows with `action: None` are answered with `{"go_to": "<name>"}` or `{"buttons": [...]}`.
- Trace rows carry `"option": {"n","tool","label","notes"} | null`. The `failed here before` flag is gone; its content is in option notes.
- `Loop` passes the compact `World.view` as the act facts and the default view to the planner (see world).
- Reply-format text (`REPLY`, `REPLY_OPTIONS`, `OPTION_NOTE`, `SYSTEM`) was shortened without removing any field or action shape. Limit for the whole act prompt: 1,600 tokens (len/4, dry gate).

**QS quest state (merged 2026-10-15, gate 266681e):**
- `Notebook.data`: `facts` and `beliefs` are keyed dicts; `quests`, `notes`, `chapters` are ordered lists. Invariants (tested as `test_inv1`…`test_inv8` in `tests/agent/test_notebook.py`):
  - omitted heard quests are kept and flagged;
  - done/dropped need evidence (dropping a heard quest needs a fact or belief reference);
  - at most 5 open quests;
  - door, coordinate, button and direction text is rejected;
  - belief revisions keep their history and reason;
  - re-sets merge (`evidence`, `opened` and check history are preserved);
  - facts are add-only;
  - nothing is deleted.

  Facts and open quests are never dropped to fit a render budget.
- `agent.plan`: `Plan(quests: list[dict], beliefs: list[dict], facts: list[dict], note: str, why: str)`. `plan(model, trigger, notebook, view, screen_text, recent, evidence: str | None = None)` (`evidence=` replaces `failure=`). `chapter(model, notebook) -> dict | None`. The planner sees persisted state, including chapters and the current chapter's notes.
- Loop:
  - **Triggers:** only T1 game text, T2 first navigable place, T3 quest closed, T4 stall (`Stuck` `no_progress`), T5 no open quest. Priority T3 > T1 > T2 > T5 > T4; T2 and T4 keep the 3-step floor. An action failure no longer sets planner urgency; `cause` stays act-facing and goes into the evidence packet (≤ 600 chars).
  - **Plan trace rows:** `{"trigger", "focus", "quests_changed", "beliefs_changed", "flags", "why"}`.
  - **After each step:** checks run on all open quests after settled outcomes. A chapter compacts when a heard or top quest closes, or when there are more than 12 notes.
  - **Checkpoint state:** `stall_unchanged` persists (old checkpoints default to 0).
- Act/decide output shape is unchanged. `Act.ops` is parsed but never applied to the notebook: closure belongs to checks and the planner.

## world

World Memory, approved 2026-10-12. Design: [world-memory.md](world-memory.md); packets W0–W4. `agent/world.py`. No model calls; never reads RAM; never picks goals or filters options.

```python
class World:
    def __init__(self, mem: Memory, tile_px: int | None) -> None
    # W0 (as built 2026-10-12)
    def before(self, per: dict, place: str, prior: str | None, size: tuple[int, int], step: int) -> tuple[Callable, dict[int, tuple[int, int]], dict[int, str]]
        # projection fn, entity index -> tile, entity index -> id (calls mem.observe); resets pos on a place change; sets .pa, .entities
    def prepare(self, skill: SkillResult, place: str, scene_cut: bool, step: int) -> dict
        # folds the skill's moves (all but the cut move); returns {"cut_dir", "exit_key"}; call before matching the next place
    def after(self, skill: SkillResult, place: str, next_place: str, scene_cut: bool, step: int) -> dict
        # requires prepare() for the same step; known exit + arrival pos reset; returns {"known_exit", "scroll"}
        # r18 (approved 2026-10-24): prepare folds the odometer's pre-cut segment into place; after folds the LAST
        # post-cut segment from the arrival tile; middle segments of a multi-cut step are dropped and flagged in the
        # trace. Amended 2026-10-24: a segment folds only when the screen it shows is world-type (perceived "world", or a
        # dialogue/menu screen over a world place); others are drained, not folded, and counted in "dropped".
        # Motion drained at the start of a step (frames emulated while the agent thought) folds into the
        # current place after perception and before World.before (amended 2026-10-24). after() stops returning "scroll" (no reader).
        # r18-fix (approved 2026-10-24): when a cut's after-screen is not "world" and the place match is deferred
        # (cut_carry), segments after the first cut are held, not folded into the old place, until the next world screen
        # re-identifies the place. A new place resolves at arrival = located − held tiles, then folds the held segments
        # from there and records the crossing and both exit sides once (unseen_cross is skipped while held); the same
        # place folds them as before. A no-response press (shift None) reaches space.fold like a bump (silent, then an
        # unconfirmed wall), as before r18. World.state carries the held segments without frames.
    def mark_exit(self, place: str, direction: str, to: str, arrive: tuple[int, int], step: int) -> None   # from the prepared exit tile
    def observe_after(self, per: dict, frame, reused: bool, place: str, size: tuple[int, int], step: int) -> tuple[int, int] | None
        # drift re-anchor (returns drift_fix) and the after-perception observe
    def state(self) -> dict
    def restore(self, state: dict) -> None
    # W1
    def name(self, place: str, name: str, step: int) -> str          # model-asserted name -> alias, new-arrival merge, or merge()
    def merge(self, src: str, dst: str, step: int) -> bool           # needs >= 2 agreeing same-name entity pairs for the tile offset
    # W2
    def resolve(self, name: str, per: dict, place: str) -> dict | None
        # {"kind": "screen"|"memory"|"place", "place", "tile", "id", "stale": bool}; order: on screen, remembered, place
    def plan(self, target: dict) -> list[dict] | None                # legs {"place", "dirs", "push", "expect": place id | None}
    def travel(self, ctx: Ctx, legs: list[dict], step: int) -> SkillResult   # all legs in one loop step; stops on the first surprise
    # W3
    def view(self, goal_text: str, place: str) -> str                # <= VIEW_TOKENS; deterministic
    # W4
    def classify(history: list[tuple]) -> str                        # module-level pure function: "screen"|"world"|"actor"|"self"|"unknown"
```

- **Constants (calibration knobs, module level):** `POS_TOL = 1`, `ANCHOR_EVIDENCE = 2`, `MISS_TO_STALE = 2`, `MISS_TO_GONE = 4`, `ACTOR_STALE_STEPS = 20`, `LANDMARKS_PER_PLACE = 8`, `MAP_W, MAP_H = 21, 15`, `VIEW_TOKENS = 500`, `TRAVEL_MAX_INPUTS = 120`; `ACTOR_KINDS` (W2 stand-in, used only while an anchor is `unknown` after W4).
- **Merges (W1, 2026-10-12):** `merge(src, dst)` writes shifted/unioned tiles and entities under `dst`, redirects edges, tile exits (`to`, shifted `arrive`) and relations, and marks `src` `{"state": "merged", "into": dst, "offset": [dx, dy]}`. `src` tile and entity rows are left as history (no tombstone field). Readers that scan across places skip rows whose `place` is a merged place; readers filtered by a live place need nothing.
- **Ownership:** World is the only writer of `tiles`, `seen` and the status/tier/anchor fields; `Memory` keeps identity (`match_place`, `name_place`, `observe`, `resolve`).
- **Misses** count only on distinct frames, on `screen == "world"`, with no modal open, inside the view rectangle shrunk by 1 tile.
- **View order:** Here line + cropped map, things here, screen/HUD, other places (hops, goal-word overlap, recency), flags. Gone things never render.
- **Removed in P4 (after the user's STOP 3 decision):** the `tasks` and `chooser and orchestrator` sections, `space.describe`, the space exit filter, `--act`.

**OM, OM2, OM3 additions (2026-10-13; OM3 is the final behaviour):**
- `World.place_label(place)` = the model name, else `place <n>`. `fail_key(place, verb, target, inputs=())` is a module function (the key `Loop._fail_key` returns).
- `World.options(per, place, fails, step, inputs=()) -> list[{"n","tool","label","action","notes"}]` is pure. Modal choices come first when a modal is open, then free input. Otherwise the order is: exits; characters and things with history; explore; known places (one row, or one `go to another known place:` row when over `OPTION_CAP = 10`); `look at or go to a thing here: <names>` (action None); free buttons. The `buttons` and things rows have `action: None` and take button failures of this place as notes. The old `info` row is gone.
- Exits: `World._exits(pid, live, screen=()) -> list[{"tile","dir","dest","name","head","idx","ident","pushed","uses","last","status","label"}]` is the one source of exit records for `options` and `view`. `status` is `verified` (a crossing counted in either direction, a confirmed `leads_to`, or an observed exit), `refuted` (existing rule; only when never crossed) or `unverified`. Records with the same destination within Chebyshev 1 are one exit.
- `_exits` merge rule: a record with no verified evidence (no crossing, no `leads_to` support, not arrived on, not observed) and no destination, whose tile is within Chebyshev 1 of a verified exit, is that exit (kept: the verified record's name, tile and counts). An unverified record with a different known destination stays its own exit. Two verified exits with distinct destinations are never merged.
- One name per exit: the record's `head` is the only name printed for it in option labels, view exit lines (`E at (x,y) <head> -> <place> used N×`) and the map legend (an exit-kind thing owned by a record prints the record's head, once per record). `_exit_rows` is removed.
- Exit labels: `exit <head> → <place> (used N×[, last step S])`, `possible exit <head> (unverified, never crossed)`, `not an exit: <head> (walked into N×[, steps a–b])`; each verified one is followed by `_beyond_suffix`.
- `World.beyond(place, screen=()) -> {"exits": {tile: {"unused_exits","unexplored","places"}}, "dead_end", "through"}`. Its counts and `dead_end` use `unseen_edges` (below).
- `unseen_edges(mem, pid) -> [((x, y), dir)]`: the untried frontier edges of place pid whose target cell is not in its `seen` store. "Unexplored" means exactly these edges everywhere: the explore option count, `World.beyond` counts and `dead_end`, and the view's `N untried edges (M into unseen)`. Explore label: `walk to an unexplored part of this place (N spots lead somewhere unseen)` / `(1 spot leads somewhere unseen)` / `(0 spots lead anywhere unseen)`. The option is offered whenever the place has untried edges.
- Use counts are symmetric. `World._cross(src, tile, dst, arrive, step)` counts one crossing on both sides, `(src, tile, dst)` and `(dst, arrive, src)` (rows in store `crossings`, id `"<place>@x,y><other>"`), recovering a missing tile from the tiles store (`_partner`). For the first crossing (the reverse exit tile is written later in the same step) it remembers `_lone = (step, dst, src)`; `World.mark_exit(place, direction, to, arrive, step)` then counts the arrival side (`_count`).
- `World.unseen_cross(src, dst, step)`: `Loop.step` calls it when the place changed between steps (seen only at the next observation). It counts the crossing under the single exit tile of src leading to dst within Chebyshev 1 of `world.pos`, else counts nothing.
- Things row: `look at or go to a thing here: <names>` lists only things of this place (on screen now or remembered in this place; not exits, not TRANSIENT kinds, not status `gone`, not anchor `screen`/`self`). Names are grouped by base name (trailing `#<n>` or ` <n>` stripped, case-insensitive); a group of 2+ prints `<base> ×<n>`. Order: smallest position key, then label. `World.resolve` strips a trailing ` ×<n>` from the requested name.
- `World.view` exit table: per place, `Exit DIR -> place used N x | never used` with a status per exit; `Don't believe:` line (max 2 refuted things); only `screen == "world"` places; `Everything unexplored from here is through: <exit>`. The beyond suffix is on the current place's view lines only, and a refuted exit's view line is its option label.
- `World.view(goal_text, place, compact=False)`: the default output is the planner's view. `compact=True` is the ACT prompt's facts: no legend (`Thing`/`Landmark`) lines, no map letters (only `@ E . , #`), no `E at` lines, no dead-end line, no `Things` bundles, no `Edges` lines, no `Don't believe`, no `Flag:` lines, map rows right-stripped, alias text only when it differs from the place name. The `Here:` header, the map and key, and other places' `Place:` + `  Exit` lines stay.

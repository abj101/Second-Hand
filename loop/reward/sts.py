from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image

from loop.os.ocr import read_text
from loop.reward.hud import is_black_frame

TurnGate = Literal["ours", "theirs", "unknown"]
STALE_MAD = 0.08

FAIL_MARKERS = (
    "you died",
    "you have died",
    "game over",
    "defeated",
    "slain",
)
WIN_MARKERS = ("victory", "you win", "heart of the spire")
_SCREEN_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pause", ("paused",)),
    ("death", ("you died", "you have died", "game over")),
    (
        "tutorial",
        (
            "combat basics",
            "map basics",
            "got it",
            "(1 of",
            "(2 of",
            "(3 of",
            "of 2)",
            "of 3)",
            "learn the controls",
            "potion slot",
            "view upgrades",
            "you start each combat with",
            "cards here are shuffled",
            "how far up you have climbed",
            "hit points (hp)",
            "if you run out of hp",
        ),
    ),
    ("reward", ("choose a relic", "choose a card", "skip relic", "skip card")),
    ("map", ("act i", "act ii", "act iii", "act iv", "legend")),
    ("shop", ("merchant",)),
    ("rest", ("campfire", "smith")),
    ("combat", ("end turn",)),
    ("title", ("singleplayer", "choose a save")),
)
_MAP_LEGEND_WORDS = ("unknown", "treasure", "elite", "merchant")
_COMBAT_NUMBERS = frozenset({"combat"})
_META_NUMBERS = frozenset({"combat", "map", "shop", "rest", "event", "reward"})
_SELECT_MARKERS = (
    "burning blood",
    "against his will",
    "choose a character",
    "choose your character",
    "at the end of combat, heal",
    "the last soldier",
    "unknown relic",
    "unlock this character",
    "unlock character",
    "play a single run",
)
SCREEN_KINDS = (
    "combat",
    "map",
    "loot",
    "rest",
    "shop",
    "event",
    "reward",
    "death",
    "title",
    "select",
    "tutorial",
    "pause",
    "other",
)
_INT_KEYS = ("hp", "hp_max", "gold", "floor", "act", "energy", "energy_max", "block")
_STR_KEYS = ("screen", "choice")
_HP_PAIR = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
_GOLD = re.compile(r"\b(?:gold|g)\s*[:=]?\s*(\d{1,5})\b", re.I)
_FLOOR = re.compile(r"\bfloor\s*[:=]?\s*(\d{1,3})\b", re.I)
_BLOCK = re.compile(r"\bblock\s*[:=]?\s*(\d{1,3})\b", re.I)
_ENERGY_WORD = re.compile(r"\benergy\s*[:=]?\s*(\d{1,2})(?:\s*/\s*(\d{1,2}))?\b", re.I)
_DEAD_TRUTH = frozenset({"1", "true", "yes", "dead", "died", "fail"})
_DEAD_EXACT = frozenset({"death", "dead", "died", "defeat", "fail", "game over", "gameover"})
_WIN_EXACT = frozenset({"victory", "win", "won"})


def _truthy_dead(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _DEAD_TRUTH
    return False


def _screen_is_dead(raw: str) -> bool:
    compact = " ".join(raw.lower().split())
    if compact in _DEAD_EXACT:
        return True
    return any(phrase in compact for phrase in ("you died", "you have died", "game over"))


def extract_sts_hud(parsed: dict[str, Any] | None) -> dict[str, Any]:
    """Pull HUD / choice fields from the planner JSON. Not device names."""
    out: dict[str, Any] = {}
    if not parsed:
        return out
    for key in _INT_KEYS:
        value = parsed.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        try:
            out[key] = int(float(value))
        except (TypeError, ValueError):
            continue
    for key in _STR_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            token = value.strip().lower() if key == "screen" else value.strip()
            if key == "screen":
                raw = token
                token = token if token in SCREEN_KINDS else "other"
                if _screen_is_dead(raw):
                    token = "death"
                    out["fail_screen"] = True
                elif raw in _WIN_EXACT or "heart of the spire" in raw:
                    out["win_screen"] = True
            out[key] = token[:160]
    screen = str(out.get("screen") or "").lower()
    if screen in {"death", "dead", "fail"}:
        out["fail_screen"] = True
    elif _truthy_dead(parsed.get("dead")) and screen not in {"title", "select"}:
        out["fail_screen"] = True
        if "screen" not in out or out.get("screen") == "other":
            out["screen"] = "death"
    hp, hp_max = out.get("hp"), out.get("hp_max")
    if isinstance(hp, int) and hp <= 0 and isinstance(hp_max, int) and hp_max > 0:
        out["fail_screen"] = True
    return out


def merge_sts_hud(base: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        if key == "fail_screen":
            out[key] = bool(out.get(key) or value)
            continue
        if key == "win_screen":
            out[key] = bool(out.get(key) or value)
            continue
        if key in _STR_KEYS or out.get(key) in (None, "", False):
            out[key] = value
    hp, hp_max = out.get("hp"), out.get("hp_max")
    if isinstance(hp, int) and isinstance(hp_max, int) and hp_max > 0:
        out["health_frac"] = max(0.0, min(1.0, hp / hp_max))
        if hp <= 0:
            out["fail_screen"] = True
    return out


def parse_turn_gate(blob: str) -> TurnGate:
    """Player-turn vs enemy-turn from OCR. Check 'enemy turn' first."""
    text = " ".join((blob or "").lower().split())
    if "enemy turn" in text:
        return "theirs"
    if "end turn" in text:
        return "ours"
    return "unknown"


def _ocr_turn_roi(image: Image.Image) -> str:
    """Lower-right End Turn button plus a center band for an Enemy Turn banner."""
    w, h = image.size
    if w < 16 or h < 16:
        return ""
    button = image.crop((int(w * 0.58), int(h * 0.70), w, h))
    banner = image.crop((int(w * 0.22), int(h * 0.36), int(w * 0.78), int(h * 0.62)))
    parts: list[str] = []
    for crop in (button, banner):
        try:
            text = read_text(crop)
        except Exception:  # noqa: BLE001 — gate must never abort collect
            text = ""
        if text:
            parts.append(text)
    return " ".join(parts)


def combat_turn_gate(image: Image.Image) -> TurnGate:
    """Cheap act-gate for STS2 combat. Unknown on menus/map (quiet is enough)."""
    return parse_turn_gate(_ocr_turn_roi(image))


def stale_to_act(
    *,
    obs_gate: str,
    now_gate: str,
    mad: float,
    stale_mad: float = STALE_MAD,
) -> str | None:
    """Skip a planned click when the world moved during VLM think. None = still safe to act."""
    if now_gate == "theirs":
        return "enemy-turn"
    if obs_gate == "theirs" and now_gate != "theirs":
        return "turn-changed"
    if mad > stale_mad and (obs_gate == "ours" or now_gate == "ours"):
        return "motion"
    return None


def score_sts(image: Image.Image) -> dict[str, Any]:
    """OCR-only HUD. Planner JSON must not fill energy/HP/screen/fail/win."""
    from loop.os.sts_stats import extract_sts_stats, stats_to_score_metrics

    stats = extract_sts_stats(image)
    blob = str(stats.get("ocr") or "").lower()
    fail = bool(stats.get("fail_screen"))
    win = (not fail) and any(marker in blob for marker in WIN_MARKERS)
    scored = stats_to_score_metrics(stats)
    scored["win_screen"] = win
    return merge_sts_hud(scored, None)


def _fill_hud_numbers(scored: dict[str, Any], blob: str, kind: str | None) -> None:
    """HP/energy only on combat. Gold/floor on map-like screens. Never on unknown OCR."""
    if kind == "select" or kind not in _META_NUMBERS:
        return
    if kind in _COMBAT_NUMBERS:
        pair = _best_hp_pair(blob)
        if pair is not None:
            scored["hp"], scored["hp_max"] = pair
        energy = _energy_pair(blob, pair)
        if energy is not None:
            scored["energy"], scored["energy_max"] = energy
        block = _BLOCK.search(blob)
        if block:
            scored["block"] = int(block.group(1))
    gold = _GOLD.search(blob)
    if gold:
        scored["gold"] = int(gold.group(1))
    floor = _FLOOR.search(blob)
    if floor:
        scored["floor"] = int(floor.group(1))


def _is_pause_ocr(blob: str) -> bool:
    """Pause / abandon menus mention Give Up; that is not a death screen."""
    if "paused" in blob:
        return True
    if "resume" in blob and ("give up" in blob or "settings" in blob or "save and quit" in blob):
        return True
    return False


def _fail_from_ocr(blob: str) -> bool:
    if _is_pause_ocr(blob):
        return False
    return any(marker in blob for marker in FAIL_MARKERS)


def _is_character_select(blob: str) -> bool:
    """Starting-class panel: flavor + relic text, not End Turn combat HUD."""
    if "end turn" in blob:
        return False
    return any(marker in blob for marker in _SELECT_MARKERS)


def _looks_like_map_legend(blob: str) -> bool:
    if "legend" in blob:
        return True
    hits = sum(1 for word in _MAP_LEGEND_WORDS if word in blob)
    return hits >= 3


def _screen_from_ocr(blob: str) -> str | None:
    if _is_pause_ocr(blob):
        return "pause"
    for kind, markers in _SCREEN_MARKERS:
        if kind == "shop" and _looks_like_map_legend(blob):
            return "map"
        if any(marker in blob for marker in markers):
            return kind
    if _looks_like_map_legend(blob):
        return "map"
    if _is_character_select(blob):
        return "select"
    return None


def _best_hp_pair(blob: str) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for match in _HP_PAIR.finditer(blob):
        cur, mx = int(match.group(1)), int(match.group(2))
        if mx <= 0 or cur > mx or mx > 999:
            continue
        if best is None or mx > best[1]:
            best = (cur, mx)
    return best


def _energy_pair(blob: str, hp_pair: tuple[int, int] | None) -> tuple[int, int] | None:
    named = _ENERGY_WORD.search(blob)
    if named:
        cur = int(named.group(1))
        mx = int(named.group(2)) if named.group(2) else max(cur, 3)
        if 0 <= cur <= mx <= 10:
            return cur, mx
    best: tuple[int, int] | None = None
    for match in _HP_PAIR.finditer(blob):
        cur, mx = int(match.group(1)), int(match.group(2))
        if mx <= 0 or cur > mx or mx > 10:
            continue
        if hp_pair is not None and (cur, mx) == hp_pair:
            continue
        if best is None or mx < best[1]:
            best = (cur, mx)
    return best


def _ocr_full(image: Image.Image) -> str:
    try:
        return read_text(image)
    except Exception:  # noqa: BLE001 — HUD OCR must never abort collect
        return ""


_SCREEN_RANK = {
    "title": 0,
    "select": 0,
    "tutorial": 1,
    "map": 2,
    "event": 2,
    "shop": 2,
    "rest": 2,
    "combat": 3,
    "reward": 4,
    "loot": 4,
}
_DANGER_CHOICE = ("give up", "save and quit", "quit")
_PROGRESS_CHOICE = ("continue", "got it", "resume", "confirm", "embark", "proceed")
TRANSITION_CAP = 0.35
_REWARD_ACTION_MARKERS = (
    "add card",
    "take gold",
    "skip card",
    "skip relic",
    "choose a card",
    "choose a relic",
)


@dataclass(frozen=True)
class CombatExitEvidence:
    """Structured evidence for combat → victory → reward/Loot."""

    score: int = 0
    confirmed: bool = False
    reasons: list[str] = field(default_factory=list)
    screen_reward: bool = False
    loot_heading: bool = False
    reward_action: bool = False
    no_combat_inventory: bool = False
    prior_enemy_hp_zero: bool = False
    black_frame: bool = False


def _normalized_screen(stats: dict[str, Any]) -> str:
    screen = str(stats.get("screen") or "").strip().lower()
    if screen == "loot":
        return "reward"
    return screen


def _loot_heading(blob: str) -> bool:
    low = " ".join((blob or "").lower().split())
    if not low:
        return False
    if "loot glow" in low or "golden trail" in low:
        return False
    if re.search(r"\bloot\b", low):
        return True
    return bool(re.search(r"\brewards?\b", low))


def _reward_action_present(stats: dict[str, Any], blob: str) -> bool:
    low = " ".join((blob or "").lower().split())
    if any(marker in low for marker in _REWARD_ACTION_MARKERS):
        return True
    markers = stats.get("markers") or []
    if isinstance(markers, list):
        joined = " ".join(str(item).lower() for item in markers)
        if any(marker in joined for marker in _REWARD_ACTION_MARKERS):
            return True
    return False


def _no_combat_inventory(stats: dict[str, Any]) -> bool:
    enemies = stats.get("enemies") or []
    if enemies:
        return False
    player = stats.get("player") or {}
    if isinstance(player, dict):
        if player.get("energy") is not None:
            return False
    if stats.get("energy") is not None:
        return False
    blob = str(stats.get("ocr") or "").lower()
    if "end turn" in blob:
        return False
    return True


def _prior_enemy_hp_zero(previous: dict[str, Any]) -> bool:
    enemies = previous.get("enemies") or []
    if not enemies:
        return False
    saw_hp = False
    for row in enemies:
        if not isinstance(row, dict):
            continue
        hp = row.get("hp")
        if hp is None:
            return False
        saw_hp = True
        if int(hp) > 0:
            return False
    return saw_hp


def combat_exit_evidence(previous: dict[str, Any], current: dict[str, Any]) -> CombatExitEvidence:
    """Score combat exit from host stats. Black/loading frames stay unconfirmed."""
    prev = previous or {}
    curr = current or {}
    if bool(curr.get("black")):
        return CombatExitEvidence(black_frame=True, reasons=["black-frame"])

    reasons: list[str] = []
    score = 0
    blob = str(curr.get("ocr") or "")

    screen_reward = _normalized_screen(curr) == "reward"
    if screen_reward:
        score += 3
        reasons.append("screen=reward")

    loot_heading = _loot_heading(blob)
    if loot_heading:
        score += 3
        reasons.append("loot-heading")

    reward_action = _reward_action_present(curr, blob)
    if reward_action:
        score += 2
        reasons.append("reward-action")

    no_combat_inventory = _no_combat_inventory(curr)
    if no_combat_inventory:
        score += 1
        reasons.append("no-combat-inventory")

    prior_enemy_zero = _prior_enemy_hp_zero(prev)
    if prior_enemy_zero:
        score += 1
        reasons.append("prior-enemy-hp-zero")

    has_reward_signal = screen_reward or loot_heading or reward_action
    confirmed = score >= 3 and has_reward_signal
    return CombatExitEvidence(
        score=score,
        confirmed=confirmed,
        reasons=reasons,
        screen_reward=screen_reward,
        loot_heading=loot_heading,
        reward_action=reward_action,
        no_combat_inventory=no_combat_inventory,
        prior_enemy_hp_zero=prior_enemy_zero,
    )


def _num(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _has_turn_point(action: Any) -> bool:
    if getattr(action, "mark", None) is not None or getattr(action, "target", None) is not None:
        return True
    if getattr(action, "click_x", None) is not None and getattr(action, "click_y", None) is not None:
        return True
    name = str(getattr(action, "name", None) or "").strip().lower()
    return bool(name and name not in {"idle", "end", "none", "null"})


def danger_turn_action(action: Any, curr: dict[str, Any]) -> bool:
    name = str(getattr(action, "name", None) or "").strip().lower()
    choice = str(curr.get("choice") or "").strip().lower()
    if name == "escape":
        return True
    blob = f"{name} {choice}"
    return any(token in blob for token in _DANGER_CHOICE)


def turn_reward_parts(
    prev: dict[str, Any],
    curr: dict[str, Any],
    action: Any,
    result: Any,
    *,
    terminal: str | None = None,
) -> dict[str, float]:
    """Split STS2 reward into capped transition vs combat. `total` is the write path."""
    prev = prev or {}
    curr = curr or {}
    empty = {"total": 0.0, "transition": 0.0, "combat": 0.0, "transition_raw": 0.0}
    if terminal == "win" or curr.get("win_screen"):
        return {**empty, "total": 1.0}
    if terminal == "fail" or curr.get("fail_screen"):
        return {**empty, "total": -1.0}
    if not _has_turn_point(action):
        return empty
    if danger_turn_action(action, curr):
        return {**empty, "total": -0.5}
    pixel_changed = bool(getattr(result, "pixel_changed", False))
    two_click = (
        getattr(action, "target", None) is not None
        or (
            getattr(action, "click_x2", None) is not None
            and getattr(action, "click_y2", None) is not None
        )
    )
    transition_raw = 0.0
    prev_s = str(prev.get("screen") or "")
    curr_s = str(curr.get("screen") or "")
    pr, cr = _SCREEN_RANK.get(prev_s), _SCREEN_RANK.get(curr_s)
    if pr is not None and cr is not None and cr > pr:
        transition_raw += 0.4
    if prev_s == "pause" and curr_s != "pause":
        transition_raw += 0.5
    if prev_s == "tutorial" and curr_s not in {"", "tutorial"}:
        transition_raw += 0.4
    choice = str(curr.get("choice") or "").strip().lower()
    if any(token in choice for token in _PROGRESS_CHOICE) and pixel_changed:
        transition_raw += 0.3
    energy0, energy1 = _num(prev, "energy"), _num(curr, "energy")
    block0, block1 = _num(prev, "block"), _num(curr, "block")
    hp0, hp1 = _num(prev, "hp"), _num(curr, "hp")
    gold0, gold1 = _num(prev, "gold"), _num(curr, "gold")
    floor0, floor1 = _num(prev, "floor"), _num(curr, "floor")
    combat = 0.0
    if energy0 is not None and energy1 is not None and energy1 < energy0:
        combat += 0.35
    if block0 is not None and block1 is not None and block1 > block0:
        combat += 0.25
    if hp0 is not None and hp1 is not None and hp1 != hp0:
        combat += 0.2
    if gold0 is not None and gold1 is not None and gold1 > gold0:
        combat += 0.2
    if floor0 is not None and floor1 is not None and floor1 > floor0:
        combat += 0.5
    y = getattr(action, "click_y", None)
    hand_only = isinstance(y, (int, float)) and float(y) >= 0.70 and not two_click
    if combat and not (two_click or not hand_only):
        combat = 0.0
    transition = min(float(transition_raw), TRANSITION_CAP)
    total = float(transition + combat)
    if total <= 0:
        return {**empty, "transition_raw": float(transition_raw)}
    return {
        "total": total,
        "transition": float(transition),
        "combat": float(combat),
        "transition_raw": float(transition_raw),
    }


def turn_reward(
    prev: dict[str, Any],
    curr: dict[str, Any],
    action: Any,
    result: Any,
    *,
    terminal: str | None = None,
) -> float:
    """STS2 step reward. Pixel flicker is not progress. Credit card+target as a pair."""
    return float(turn_reward_parts(prev, curr, action, result, terminal=terminal)["total"])


def sandbox_turn_reward() -> tuple[bool, str]:
    from loop.log.schema import ActionLog, StepResult

    resume = turn_reward(
        {"screen": "pause"},
        {"screen": "map", "choice": "Resume"},
        ActionLog(mark=2, click_x=0.5, click_y=0.55),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    escape = turn_reward(
        {"screen": "combat"},
        {"screen": "pause", "choice": "escape"},
        ActionLog(name="escape"),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    if resume <= escape:
        return False, f"Resume {resume} not greater than Escape {escape}"
    card = turn_reward(
        {"screen": "combat", "energy": 3, "block": 0, "hp": 80},
        {"screen": "combat", "energy": 3, "block": 0, "hp": 80, "choice": "Strike"},
        ActionLog(mark=3, click_x=0.40, click_y=0.85),
        StepResult(pixel_changed=False, executed_as_proposed=True),
    )
    pair = turn_reward(
        {"screen": "combat", "energy": 3, "block": 0, "hp": 80},
        {"screen": "combat", "energy": 2, "block": 5, "hp": 80, "choice": "Strike"},
        ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    if pair <= card:
        return False, f"card+target {pair} not greater than card-only {card}"
    cont = turn_reward(
        {"screen": "title"},
        {"screen": "map", "choice": "Continue"},
        ActionLog(mark=1, click_x=0.50, click_y=0.70),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    logo = turn_reward(
        {"screen": "title"},
        {"screen": "title", "choice": "logo"},
        ActionLog(mark=9, click_x=0.50, click_y=0.18),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    if cont <= logo:
        return False, f"Continue {cont} not greater than logo {logo}"
    if pair <= cont:
        return False, f"card+target {pair} not greater than Continue {cont}"
    parts = turn_reward_parts(
        {"screen": "title"},
        {"screen": "map", "choice": "Continue"},
        ActionLog(mark=1, click_x=0.50, click_y=0.70),
        StepResult(pixel_changed=True, executed_as_proposed=True),
    )
    if parts["transition_raw"] <= TRANSITION_CAP:
        return False, "expected uncapped Continue transition above cap"
    if parts["transition"] > TRANSITION_CAP:
        return False, f"transition {parts['transition']} exceeds cap {TRANSITION_CAP}"
    return True, "ok"

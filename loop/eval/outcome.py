from __future__ import annotations

from PIL import Image

from loop.log.schema import ActionLog, StepLog, StepResult, Terminal
from loop.os.chords import is_look_only, is_loco_walk, same_chord
from loop.os.profile import apply_crop, mean_absdiff
from loop.reward.hud import hud_visible

# Exclude top HUD / compass and the bottom prompt bar.
VIEW_CROP = (0.12, 0.18, 0.76, 0.52)
VIEW_MAD_TAU = 0.010
SHIFT_MIN = 1


def center_view(image: Image.Image) -> Image.Image:
    return apply_crop(image, VIEW_CROP)


def _column_means(image: Image.Image) -> list[float]:
    gray = image.convert("L")
    width, height = gray.size
    pix = gray.load()
    out: list[float] = []
    for x in range(width):
        total = 0.0
        for y in range(height):
            total += float(pix[x, y])
        out.append(total / max(height, 1))
    return out


def column_shift(before: Image.Image, after: Image.Image, *, max_lag: int = 16) -> int:
    """Lag aligning after onto before. Positive = scene moved left (camera yawed right)."""
    a = _column_means(before)
    b = _column_means(after)
    n = min(len(a), len(b))
    if n < 8:
        return 0
    a = a[:n]
    b = b[:n]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    a = [x - mean_a for x in a]
    b = [x - mean_b for x in b]
    lag_lim = min(max_lag, max(1, n // 4))
    best_lag = 0
    best = float("-inf")
    for lag in range(-lag_lim, lag_lim + 1):
        acc = 0.0
        count = 0
        for i in range(n):
            j = i + lag
            if 0 <= j < n:
                acc += b[i] * a[j]
                count += 1
        score = acc / max(count, 1)
        if score > best:
            best = score
            best_lag = lag
    return best_lag


def yaw_sign(direction: str | None) -> int:
    token = (direction or "").lower()
    if token in {"e", "ne", "se"}:
        return 1
    if token in {"w", "nw", "sw"}:
        return -1
    return 0


def view_changed(
    before: Image.Image,
    after: Image.Image,
    *,
    stick_r: str | None,
    mad_tau: float = VIEW_MAD_TAU,
) -> tuple[bool, float, int]:
    """Look verifier: center-crop MAD plus column-shift sign for yaw."""
    crop_b = center_view(before).convert("L")
    crop_a = center_view(after).convert("L")
    width = 48
    height = max(8, int(round(width * crop_b.size[1] / max(crop_b.size[0], 1))))
    crop_b = crop_b.resize((width, height), Image.Resampling.BILINEAR)
    crop_a = crop_a.resize((width, height), Image.Resampling.BILINEAR)
    mad = mean_absdiff(crop_b, crop_a)
    shift = column_shift(crop_b, crop_a)
    sign = yaw_sign(stick_r)
    if mad <= mad_tau:
        return False, mad, shift
    if sign == 0:
        return True, mad, shift
    if abs(shift) < SHIFT_MIN:
        return False, mad, shift
    if (shift > 0) != (sign > 0):
        return False, mad, shift
    return True, mad, shift


def compute_step_outcome(
    *,
    before: Image.Image,
    after: Image.Image,
    action: ActionLog,
    proposed: ActionLog | None,
    prompt_before: bool,
    prompt_after: bool,
    pixel_changed: bool,
) -> StepResult:
    look = is_look_only(action) or bool(action.stick_r)
    changed, view_mad, shift = view_changed(before, after, stick_r=action.stick_r)
    if not look:
        changed = False
    stall = is_loco_walk(action) and not changed and not pixel_changed
    prompt_cleared = bool(prompt_before) and not bool(prompt_after)
    executed_as_proposed = True if proposed is None else same_chord(proposed, action)
    return StepResult(
        view_changed=changed,
        view_mad=view_mad,
        column_shift=shift,
        prompt_cleared=prompt_cleared,
        stall=stall,
        executed_as_proposed=executed_as_proposed,
        pixel_changed=pixel_changed,
    )


def chapel_coverage(steps: list[StepLog]) -> bool:
    looks = 0
    walked = False
    for step in steps:
        action = step.action
        result = step.result
        if is_look_only(action) and result.view_changed:
            looks += 1
        if action.stick_l and (result.pixel_changed or result.view_changed):
            walked = True
    return looks >= 3 and walked


_FACE = frozenset({"a", "b", "x", "y"})


def cv_backed_prompt(step: StepLog) -> bool:
    """Chapel/SFT wins need a CV bar. DINO-only flicker is not a prompt clear."""
    return "cv" in (step.prompt_source or "")


def face_tap(action: ActionLog) -> str | None:
    """First face button on this chord, if any."""
    for tok in action.tapped or []:
        name = (tok or "").strip().lower()
        if name in _FACE:
            return name
    return None


def _lock_yield_before(steps: list[StepLog], index: int, face: str) -> bool:
    """True when a recent step looked or walked with the same CV bar still up."""
    start = max(0, index - 4)
    for prev in steps[start:index]:
        glyph = (prev.prompt_glyph or "").strip().lower()
        if not prev.prompt_on or glyph != face:
            continue
        if face_tap(prev.action):
            continue
        if prev.action.stick_l or prev.action.stick_r:
            return True
    return False


def prompt_was_cleared(steps: list[StepLog]) -> bool:
    """CV clear after a face tap, bar stays off two later steps.

    Walking out of interact range is not a win. A tap that follows a look/walk
    on the same still-up glyph is a lock probe, not Open/Talk.
    """
    for i, step in enumerate(steps):
        if not (step.result.prompt_cleared and cv_backed_prompt(step)):
            continue
        face = face_tap(step.action)
        if face is None:
            continue
        if _lock_yield_before(steps, i, face):
            continue
        later = steps[i + 1 :]
        if len(later) < 2:
            continue
        if later[0].prompt_on or later[1].prompt_on:
            continue
        return True
    return False


def rune_gained(steps: list[StepLog]) -> bool:
    """HUD-on rune bump that holds for two later HUD steps. Lone-counter flicker is not a win."""
    for i, step in enumerate(steps):
        start = _hud_runes(step)
        if start is None:
            continue
        later = steps[i + 1 :]
        if len(later) < 2:
            continue
        a = _hud_runes(later[0])
        b = _hud_runes(later[1])
        if a is None or b is None:
            continue
        if a > start and b >= a:
            return True
    return False


def _hud_runes(step: StepLog) -> float | None:
    if not hud_visible(step.metrics):
        return None
    value = step.metrics.get("runes")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_number(steps: list[StepLog], key: str) -> float | None:
    for step in steps:
        value = step.metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _last_number(steps: list[StepLog], key: str) -> float | None:
    for step in reversed(steps):
        value = step.metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def verified_chapel_success(steps: list[StepLog], terminal: Terminal) -> bool:
    """Timeout in a wall with no yaw is failure. Survive-K is not success."""
    if terminal == "fail":
        return False
    if rune_gained(steps) or prompt_was_cleared(steps) or chapel_coverage(steps):
        return True
    return terminal == "win"

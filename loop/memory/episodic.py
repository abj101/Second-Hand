from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field

_STICK_DIRS = frozenset({"n", "ne", "e", "se", "s", "sw", "w", "nw"})
_YAW_EAST = frozenset({"n", "ne", "e", "se"})
_YAW_WEST = frozenset({"w", "nw", "sw"})
_LOCAL_XY = {
    "n": (0.0, 1.0),
    "ne": (1.0, 1.0),
    "e": (1.0, 0.0),
    "se": (1.0, -1.0),
    "s": (0.0, -1.0),
    "sw": (-1.0, -1.0),
    "w": (-1.0, 0.0),
    "nw": (-1.0, 1.0),
}


def _parse_sticks(chord: str) -> tuple[str | None, str | None]:
    stick_l: str | None = None
    stick_r: str | None = None
    for part in (chord or "").split("+"):
        if part.startswith("stick_l_"):
            direction = part[8:]
            if direction in _STICK_DIRS:
                stick_l = direction
        elif part.startswith("stick_r_"):
            direction = part[8:]
            if direction in _STICK_DIRS:
                stick_r = direction
    return stick_l, stick_r


@dataclass
class Trace:
    chord: str
    pixels: bool
    mad: float | None = None
    glyphs: tuple[str, ...] = ()
    prompt: bool = False


@dataclass
class PoseTracker:
    """Coarse dead-reckoned pose used only as a short-window loop detector."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0  # 0 = north, + = yaw east / stick_r e
    visits: Counter[tuple[int, int]] = field(default_factory=Counter)
    last_cell: tuple[int, int] | None = None

    def update(
        self,
        *,
        stick_l: str | None,
        stick_r: str | None,
        duration_ms: int | None,
        column_shift: int | None,
        pixels: bool,
        mad: float | None,
    ) -> None:
        scale = (duration_ms or 400) / 400.0
        if stick_r in _YAW_EAST:
            self.yaw += scale * 0.5
        elif stick_r in _YAW_WEST:
            self.yaw -= scale * 0.5
        if column_shift:
            self.yaw += column_shift * 0.08

        local = _LOCAL_XY.get(stick_l or "")
        moved = pixels or (mad is not None and mad >= 0.015)
        if local is not None and moved:
            lx, ly = local
            norm = math.hypot(lx, ly) or 1.0
            lx, ly = lx / norm, ly / norm
            cos_y = math.cos(self.yaw)
            sin_y = math.sin(self.yaw)
            # Rotate stick_l's n/e/s/w vector by yaw into world frame.
            wx = lx * cos_y + ly * sin_y
            wy = -lx * sin_y + ly * cos_y
            step = scale * 0.4
            self.x += wx * step
            self.y += wy * step

        cell = (round(self.x), round(self.y))
        self.visits[cell] += 1
        self.last_cell = cell

    @property
    def cell(self) -> tuple[int, int]:
        return (round(self.x), round(self.y))

    def revisit_hint(self, *, threshold: int = 3) -> str:
        cell = self.last_cell
        if cell is None:
            return ""
        n = self.visits[cell]
        if n < threshold or len(self.visits) < 2:
            return ""
        cx, cy = cell
        return f"REVISIT: {n}x cell {cx},{cy} — look stick_r or walk a new heading"


class WorkingMemory:
    """Short last-N traces + pattern RAG. Not dump_all pixel cosine."""

    def __init__(self, window: int = 12, rag_k: int = 3) -> None:
        self.window = window
        self.rag_k = rag_k
        self._items: deque[Trace] = deque(maxlen=window)
        self.lessons: str = ""
        self.pose = PoseTracker()

    def add(
        self,
        *,
        chord: str,
        pixels: bool,
        glyphs: list[str] | None = None,
        note: str = "",
        mad: float | None = None,
        prompt: bool = False,
        stick_l: str | None = None,
        stick_r: str | None = None,
        duration_ms: int | None = None,
        column_shift: int | None = None,
    ) -> None:
        del note  # thoughts self-poison the bank; keep counts + glyphs only
        parsed_l, parsed_r = _parse_sticks(chord or "")
        if stick_l is None:
            stick_l = parsed_l
        if stick_r is None:
            stick_r = parsed_r
        self._items.append(
            Trace(
                chord=chord or "?",
                pixels=pixels,
                mad=mad,
                glyphs=tuple(glyphs or ()),
                prompt=prompt,
            )
        )
        self.pose.update(
            stick_l=stick_l,
            stick_r=stick_r,
            duration_ms=duration_ms,
            column_shift=column_shift,
            pixels=pixels,
            mad=mad,
        )
        if len(self._items) % 8 == 0:
            self.lessons = self.condense()

    def condense(self) -> str:
        if not self._items:
            return ""
        counts = Counter(row.chord for row in self._items)
        top = ", ".join(f"{name}×{n}" for name, n in counts.most_common(5))
        changed = sum(1 for row in self._items if row.pixels)
        mads = [row.mad for row in self._items if row.mad is not None]
        mad_bit = f" mad={sum(mads) / len(mads):.3f}" if mads else ""
        missed: list[str] = []
        for row in self._items:
            chord_parts = set(row.chord.split("+"))
            unused = [g for g in row.glyphs if g not in chord_parts]
            if unused:
                missed.extend(unused)
        miss = ""
        if missed:
            miss_counts = Counter(missed)
            miss = " unused_glyphs=" + ",".join(f"{g}×{n}" for g, n in miss_counts.most_common(4))
        blocked = self.blocked_hint()
        block = f"; {blocked}" if blocked else ""
        return f"last-{len(self._items)}: {top}; pixels={changed}/{len(self._items)}{mad_bit}{miss}{block}"

    def identical_nochange_streak(self, *, min_streak: int = 2) -> tuple[int, str]:
        """Consecutive identical actions with near-zero motion (click or walk)."""
        rows = list(self._items)
        if len(rows) < min_streak:
            return 0, ""
        last = rows[-1].chord
        click = last.startswith("pt_") or last.startswith("mk_") or last.startswith("hv_")
        walk = last.startswith("stick_l_") and "+" not in last
        if not click and not walk:
            return 0, ""
        streak = 0
        for row in reversed(rows):
            if row.chord != last:
                break
            if row.pixels:
                break
            if row.mad is not None and row.mad >= 0.015:
                break
            streak += 1
        if streak < min_streak:
            return 0, ""
        return streak, last

    def blocked_hint(self, *, selected: str | None = None) -> str:
        """Same control with near-zero motion → blocked hint (screen-agnostic)."""
        streak, last = self.identical_nochange_streak(min_streak=2)
        if streak < 2:
            return ""
        click = last.startswith("pt_") or last.startswith("mk_") or last.startswith("hv_")
        walk = last.startswith("stick_l_") and "+" not in last
        if not click and not walk:
            return ""
        if click:
            if selected:
                return (
                    f"BLOCKED: {streak}x {last} no-change — {selected} selected; "
                    "click an enemy mark, not a different gx,gy"
                )
            return (
                f"BLOCKED: {streak}x {last} no-change — "
                "try a different labeled mark"
            )
        return f"BLOCKED: {streak}x {last} no-change — turn stick_r or back stick_l_s"

    def cycle_hint(self) -> str:
        return self.pose.revisit_hint()

    def recall(self, glyphs: list[str], chord: str = "", *, k: int | None = None) -> list[str]:
        query = set(glyphs)
        if chord:
            query.update(part for part in chord.split("+") if part)
        if not query or len(self._items) < 2:
            return []
        ranked: list[tuple[float, Trace]] = []
        for row in list(self._items)[:-1]:
            keys = set(row.glyphs) | {part for part in row.chord.split("+") if part}
            overlap = len(query & keys)
            if overlap <= 0:
                continue
            union = len(query | keys) or 1
            score = overlap / union
            # Only treat motion as success when a prompt was actually on screen.
            if row.pixels and row.prompt:
                score += 0.15
            elif row.pixels and not row.prompt:
                score -= 0.05
            if row.prompt:
                score += 0.1
            ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        hits: list[str] = []
        for _, row in ranked[: k or self.rag_k]:
            pix = f"mad={row.mad:.3f}" if row.mad is not None else ("pixels" if row.pixels else "no-change")
            glyph = ",".join(row.glyphs) if row.glyphs else "-"
            hits.append(f"{row.chord} {pix} glyphs={glyph}")
        return hits

    def last(self) -> Trace | None:
        return self._items[-1] if self._items else None

    def prompt_block(self, *, glyphs: list[str] | None = None, chord: str = "", selected: str | None = None) -> str:
        if not self._items and not self.lessons:
            return ""
        lines: list[str] = ["Working memory:"]
        blocked = self.blocked_hint(selected=selected)
        if blocked:
            lines.append(blocked)
        cycled = self.cycle_hint()
        if cycled:
            lines.append(cycled)
        for row in list(self._items)[-8:]:
            pix = f"mad={row.mad:.3f}" if row.mad is not None else ("pixels" if row.pixels else "no-change")
            glyph = ",".join(row.glyphs) if row.glyphs else "-"
            prompt = " prompt" if row.prompt else ""
            lines.append(f"- {row.chord} {pix} glyphs={glyph}{prompt}")
        if self.lessons:
            lines.append("Lessons: " + self.lessons)
        hits = self.recall(glyphs or [], chord)
        if hits:
            lines.append("Similar traces:")
            lines.extend(f"- {hit}" for hit in hits)
        return "\n".join(lines)

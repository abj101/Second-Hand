from __future__ import annotations

import json

from avatar.gsm.rule.detector import DetectedBox
from avatar.gsm.registry import TETRIS_LABELS

TETROMINO = {lab.lower() for lab in TETRIS_LABELS}
SIDE = {"next piece", "hold piece"}
SWIPES = ("swipe_up", "swipe_down", "swipe_left", "swipe_right")
PACK_ORDER = ("swipe_down", "swipe_left", "swipe_up", "swipe_right")
UNJAM_ORDER = ("swipe_up", "swipe_right", "swipe_left", "swipe_down")
GRID = 4


def boxes_to_actions(
    boxes: list[DetectedBox],
    *,
    labels: list[str] | None = None,
    max_shift: int = 3,
    banned: set[str] | None = None,
) -> list[str]:
    """Map detector boxes on the board crop to Tetris named actions."""
    allowed = {lab.lower() for lab in labels} if labels else TETROMINO
    pieces = [
        b
        for b in boxes
        if b.label.lower() in allowed and b.label.lower() not in SIDE
    ]
    if not pieces:
        pieces = [b for b in boxes if b.label.lower() not in SIDE]
    if not pieces:
        return ["hard_drop"]
    piece = min(pieces, key=lambda b: (b.y, -b.score))
    col = min(9, max(0, int(piece.cx * 10)))
    target = _target_column(boxes, piece)
    blocked = banned or set()

    def pick(*names: str) -> list[str]:
        for name in names:
            if name not in blocked:
                return [name]
        return ["hard_drop"]

    if col > target:
        n = min(max_shift, col - target)
        return pick(*(["shift_left"] * n))
    if col < target:
        n = min(max_shift, target - col)
        return pick(*(["shift_right"] * n))
    return pick("hard_drop", "rotate", "shift_left", "shift_right")


def _target_column(boxes: list[DetectedBox], piece: DetectedBox) -> int:
    """Lowest of columns 0–8 (keep 9 empty for I). Leftmost on a tie."""
    heights = [0] * 10
    for box in boxes:
        if box is piece:
            continue
        col = min(9, max(0, int(box.cx * 10)))
        cell_h = max(1, int((1.0 - box.y) * 20))
        heights[col] = max(heights[col], cell_h)
    well = range(9)
    return min(well, key=lambda c: (heights[c], c))


def occupancy_grid(boxes: list[DetectedBox], *, cols: int = GRID, rows: int = GRID) -> list[list[object | None]]:
    """cells: tint tuple, unique occupancy sentinel, or None."""
    grid: list[list[object | None]] = [[None] * cols for _ in range(rows)]
    for box in boxes:
        col = min(cols - 1, max(0, int(box.cx * cols)))
        row = min(rows - 1, max(0, int((box.y + box.h / 2.0) * rows)))
        if box.tint is not None:
            grid[row][col] = box.tint
        else:
            grid[row][col] = ("occ", row, col)
    return grid


def occupancy_fingerprint(
    boxes: list[DetectedBox], *, cols: int = GRID, rows: int = GRID
) -> str:
    """Stable occupancy+tint key. Empty detector output is ''."""
    cells: list[list[object]] = []
    for r, row in enumerate(occupancy_grid(boxes, cols=cols, rows=rows)):
        for c, val in enumerate(row):
            if val is None:
                continue
            if isinstance(val, tuple) and val and val[0] == "occ":
                cells.append([r, c, "occ"])
            elif isinstance(val, tuple):
                cells.append([r, c, list(val)])
            else:
                cells.append([r, c, val])
    if not cells:
        return ""
    return json.dumps(cells)


def _slide_line(cells: list[object | None]) -> list[object | None]:
    tiles = [cell for cell in cells if cell is not None]
    out: list[object | None] = []
    i = 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i])
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    out.extend([None] * (len(cells) - len(out)))
    return out


def _lines(grid: list[list[object | None]], swipe: str) -> list[list[object | None]]:
    if swipe == "swipe_left":
        return [list(row) for row in grid]
    if swipe == "swipe_right":
        return [list(reversed(row)) for row in grid]
    if swipe == "swipe_up":
        return [[grid[r][c] for r in range(GRID)] for c in range(GRID)]
    return [[grid[r][c] for r in range(GRID - 1, -1, -1)] for c in range(GRID)]


def legal_swipes(boxes: list[DetectedBox]) -> set[str]:
    grid = occupancy_grid(boxes)
    if not any(cell is not None for row in grid for cell in row):
        return set()
    legal: set[str] = set()
    for name in SWIPES:
        if any(_slide_line(line) != line for line in _lines(grid, name)):
            legal.add(name)
    return legal


def _centroid_pick(boxes: list[DetectedBox], pick) -> list[str]:
    if not boxes:
        return pick("swipe_left", "swipe_up", "swipe_right", "swipe_down")
    cx = sum(b.cx for b in boxes) / len(boxes)
    cy = sum(b.y + b.h / 2.0 for b in boxes) / len(boxes)
    if cy < 0.62:
        return pick("swipe_down", "swipe_left", "swipe_up", "swipe_right")
    if cx > 0.38:
        return pick("swipe_left", "swipe_down", "swipe_up", "swipe_right")
    return pick("swipe_left", "swipe_up", "swipe_right", "swipe_down")


def boxes_to_swipes(
    boxes: list[DetectedBox],
    *,
    action_space: list[str] | None = None,
    banned: set[str] | None = None,
    rates: dict[str, float] | None = None,
) -> list[str]:
    """One 2048 swipe: legal occupancy move, packed toward the bottom-left."""
    space = [name for name in (action_space or SWIPES) if name in SWIPES]
    if not space:
        space = list(SWIPES)
    blocked = banned or set()
    unbanned = [name for name in space if name not in blocked]
    if unbanned:
        pool = unbanned
        unjam_first = False
    else:
        pool = list(space)
        unjam_first = True

    def pick(*names: str) -> list[str]:
        for name in names:
            if name in pool:
                return [name]
        return [pool[0]]

    def rank(names: tuple[str, ...] | list[str]) -> list[str]:
        seq = list(names)
        if not rates:
            return seq
        return sorted(seq, key=lambda n: (-float(rates.get(n, 0.0)), seq.index(n)))

    if unjam_first:
        return pick(*rank(UNJAM_ORDER))

    legal = {name for name in legal_swipes(boxes) if name in pool}
    if legal:
        order = rank([name for name in PACK_ORDER if name in legal]) or rank(PACK_ORDER)
        return pick(*order) or pick(*PACK_ORDER)

    return _centroid_pick(boxes, pick)

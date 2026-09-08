from __future__ import annotations

import math
import sqlite3
import uuid
from pathlib import Path

from PIL import Image

from loop.os.profile import roi_thumb


def _vec(image: Image.Image, size: int = 64) -> list[float]:
    thumb = roi_thumb(image, size)
    w, h = thumb.size
    data = [float(thumb.getpixel((x, y))) for y in range(h) for x in range(w)]
    norm = math.sqrt(sum(v * v for v in data)) or 1.0
    return [v / norm for v in data]


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(a[i] * b[i] for i in range(n))


class MemoryStore:
    """Session-scoped sqlite: thumb cosine recall. Not wiped on fail."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                thought TEXT,
                action TEXT,
                outcome TEXT,
                episode_id TEXT,
                step_id INTEGER,
                vec BLOB
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def size(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return int(row[0]) if row else 0

    def remember(
        self,
        image: Image.Image,
        *,
        thought: str = "",
        action: str = "",
        outcome: str = "",
        episode_id: str = "",
        step_id: int = 0,
    ) -> str:
        mem_id = uuid.uuid4().hex[:12]
        vec = _vec(image)
        blob = ",".join(f"{v:.6f}" for v in vec)
        self._conn.execute(
            "INSERT INTO memories (id, thought, action, outcome, episode_id, step_id, vec) VALUES (?,?,?,?,?,?,?)",
            (mem_id, thought, action, outcome, episode_id, step_id, blob),
        )
        self._conn.commit()
        return mem_id

    def recall(self, image: Image.Image, *, k: int = 3) -> list[tuple[str, str, float]]:
        query = _vec(image)
        rows = self._conn.execute(
            "SELECT id, thought, action, outcome, vec FROM memories"
        ).fetchall()
        ranked: list[tuple[str, str, float]] = []
        for mem_id, thought, action, outcome, blob in rows:
            vec = [float(x) for x in str(blob).split(",") if x]
            score = _cosine(query, vec)
            snippet = f"{action or '?'} → {outcome or thought or mem_id}"
            ranked.append((str(mem_id), snippet, score))
        ranked.sort(key=lambda row: row[2], reverse=True)
        return ranked[:k]

"""Append-only JSONL run trace."""
import json
from collections import deque
from pathlib import Path


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()

    def tail(self, n: int) -> list[dict]:
        if n <= 0 or not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in deque(stream, maxlen=n)]

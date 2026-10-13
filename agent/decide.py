"""Action record and target resolution for the act path."""

from dataclasses import dataclass


@dataclass
class Choice:
    i: int
    effect: str
    detail: str
    used: list[str]
    why: str
    called: bool
    text: str | None = None


def resolve_target(mem, target: str, place: str, tile=None) -> str | None:
    name = str(target or "").strip()
    return mem.resolve(name.split(":", 1)[1].strip() if name.casefold().startswith("exit:") else name, place, tile) if name else None

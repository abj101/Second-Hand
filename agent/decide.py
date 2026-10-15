"""Action record for the act path."""

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


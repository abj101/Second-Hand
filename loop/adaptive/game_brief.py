"""Small, validated game brief catalog used as advisory model context."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

BRIEF_DIR = Path(__file__).resolve().parents[1] / "game_briefs"
MAX_BRIEF_BYTES = 8_000
_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


class GameBriefError(ValueError):
    """Raised when an explicit game brief is unknown or malformed."""


def normalize_game_key(value: object) -> str:
    """Normalize a game id, alias, or window title for exact matching."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _WORD_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class GameBrief:
    format_version: int
    game_id: str
    display_name: str
    aliases: tuple[str, ...]
    objective: str
    mechanics: tuple[str, ...]
    controls: tuple[dict[str, str], ...]
    observation_focus: tuple[str, ...]
    strategy: tuple[str, ...]
    uncertainties: tuple[str, ...]
    sources: tuple[dict[str, str], ...]
    verified_on: str
    digest: str
    path: str

    @property
    def match_keys(self) -> frozenset[str]:
        return frozenset(
            normalize_game_key(x)
            for x in (self.game_id, self.display_name, *self.aliases)
        )

    def as_dict(self) -> dict[str, Any]:
        """Return only the brief fields; provenance stays separate."""

        return {
            "format_version": self.format_version,
            "game_id": self.game_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "objective": self.objective,
            "mechanics": list(self.mechanics),
            "controls": [dict(x) for x in self.controls],
            "observation_focus": list(self.observation_focus),
            "strategy": list(self.strategy),
            "uncertainties": list(self.uncertainties),
            "sources": [dict(x) for x in self.sources],
            "verified_on": self.verified_on,
        }

    def provenance(self) -> dict[str, str]:
        return {"game_id": self.game_id, "path": self.path, "sha256": self.digest}


def _strings(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise GameBriefError(f"brief field {key!r} must be a non-empty string list")
    return tuple(x.strip() for x in value)


def _objects(payload: Mapping[str, Any], key: str, fields: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise GameBriefError(f"brief field {key!r} must be a list")
    result: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, dict) or any(not isinstance(row.get(f), str) or not row[f].strip() for f in fields):
            raise GameBriefError(f"brief field {key!r} has an invalid object")
        result.append({f: row[f].strip() for f in fields})
    return tuple(result)


def _load_path(path: Path) -> GameBrief:
    raw = path.read_bytes()
    if len(raw) > MAX_BRIEF_BYTES:
        raise GameBriefError(f"game brief exceeds {MAX_BRIEF_BYTES} bytes: {path.name}")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GameBriefError(f"invalid game brief JSON: {path.name}") from exc
    if not isinstance(payload, dict) or type(payload.get("format_version")) is not int or payload.get("format_version") != 1:
        raise GameBriefError(f"unsupported game brief format: {path.name}")
    game_id = payload.get("game_id")
    display_name = payload.get("display_name")
    objective = payload.get("objective")
    verified_on = payload.get("verified_on")
    if not all(isinstance(x, str) and x.strip() for x in (game_id, display_name, objective, verified_on)):
        raise GameBriefError(f"brief identity fields are invalid: {path.name}")
    if normalize_game_key(path.stem) != normalize_game_key(game_id):
        raise GameBriefError(f"brief filename must match game_id: {path.name}")
    try:
        date.fromisoformat(verified_on)
    except ValueError as exc:
        raise GameBriefError(f"brief verified_on must be YYYY-MM-DD: {path.name}") from exc
    if verified_on != date.fromisoformat(verified_on).isoformat():
        raise GameBriefError(f"brief verified_on must be YYYY-MM-DD: {path.name}")
    aliases = _strings(payload, "aliases")
    alias_keys = [normalize_game_key(x) for x in aliases]
    if normalize_game_key(game_id) == "" or len(set(alias_keys)) != len(alias_keys):
        raise GameBriefError(f"brief game_id/aliases must be unique and non-empty: {path.name}")
    return GameBrief(
        format_version=1,
        game_id=game_id.strip(),
        display_name=display_name.strip(),
        aliases=aliases,
        objective=objective.strip(),
        mechanics=_strings(payload, "mechanics"),
        controls=_objects(payload, "controls", ("action", "input", "semantics")),
        observation_focus=_strings(payload, "observation_focus"),
        strategy=_strings(payload, "strategy"),
        uncertainties=_strings(payload, "uncertainties"),
        sources=_objects(payload, "sources", ("url", "note")),
        verified_on=verified_on,
        digest=digest,
        path=str(path),
    )


def load_game_briefs(directory: Path | None = None) -> tuple[GameBrief, ...]:
    root = Path(directory) if directory is not None else BRIEF_DIR
    if not root.is_dir():
        return ()
    return tuple(_load_path(path) for path in sorted(root.glob("*.json")))


def select_game_brief(
    game: str | None = None,
    *,
    target_title: str | None = None,
    directory: Path | None = None,
) -> GameBrief | None:
    """Select by exact id/alias; unknown automatic title matching is unbriefed."""

    briefs = load_game_briefs(directory)
    key = normalize_game_key(game if game is not None else target_title)
    if not key and game is None:
        return None
    if not key:
        raise GameBriefError(f"unknown game brief: {game}")
    matches = [brief for brief in briefs if key in brief.match_keys]
    if len(matches) > 1:
        raise GameBriefError(f"ambiguous game brief: {game if game is not None else target_title}")
    if matches:
        return matches[0]
    if game is not None:
        raise GameBriefError(f"unknown game brief: {game}")
    return None

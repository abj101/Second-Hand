from __future__ import annotations

import json
from typing import Any

from loop.memory.journal import FactJournal
from loop.memory.notebook import Notebook
from loop.memory.screen_objects import ScreenObjectStore


def register_memory_tools(
    registry: Any,
    *,
    journal: FactJournal,
    notebook: Notebook,
    screen: str | None = None,
    aim_listing: str | None = None,
    selected: str | None = None,
    energy: float | int | None = None,
) -> None:
    def _recall(query: str = "", **_: Any) -> str:
        hits = journal.recall(query or "", k=5)
        return json.dumps(hits)

    def _remember(note: str = "", **_: Any) -> str:
        if not note.strip():
            return json.dumps({"ok": False, "reason": "empty note"})
        saved = notebook.ingest({"note": note}, screen=screen, aim_listing=aim_listing)
        return json.dumps({"ok": bool(saved), "note": saved or note[:80]})

    def _notebook(**_: Any) -> str:
        block = notebook.prompt_block(
            screen=screen,
            aim_listing=aim_listing,
            selected=selected,
            energy=energy,
        )
        return block or "(empty notebook)"

    registry.register(
        "memory_recall",
        description="Search the fact journal by keyword.",
        parameters={"query": "string"},
        handler=_recall,
    )
    registry.register(
        "memory_remember",
        description="Store a binding note in the notebook.",
        parameters={"note": "string"},
        handler=_remember,
    )
    registry.register(
        "memory_notebook",
        description="Return the full notebook block for this turn.",
        parameters={},
        handler=_notebook,
    )


def register_reasoner_tools(
    registry: Any,
    *,
    journal: FactJournal,
    notebook: Notebook,
    screen: str | None = None,
    aim_listing: str | None = None,
    selected: str | None = None,
    energy: float | int | None = None,
    recheck_handler: Any | None = None,
) -> None:
    """Memory + optional proactive recheck tools exposed to the turn reasoner (LLM2)."""
    register_memory_tools(
        registry,
        journal=journal,
        notebook=notebook,
        screen=screen,
        aim_listing=aim_listing,
        selected=selected,
        energy=energy,
    )
    if recheck_handler is not None:
        registry.register(
            "request_recheck",
            description=(
                "Re-run perception on the current frame before committing. "
                "Use when unresolved marks exist or Allowed lists seem incomplete."
            ),
            parameters={"region_or_entity": "optional string — what to verify"},
            handler=recheck_handler,
        )


def register_screen_tools(registry: Any, store: ScreenObjectStore) -> None:
    def _list(**_: Any) -> str:
        return json.dumps({"listing": store.listing(), "objects": store.to_json()})

    def _get(mark_id: int, **_: Any) -> str:
        obj = store.by_id(int(mark_id))
        return json.dumps(obj.to_dict() if obj else {})

    registry.register(
        "screen_list_objects",
        description="List clickable marks for this frame.",
        parameters={},
        handler=_list,
    )
    registry.register(
        "screen_get_object",
        description="Lookup one mark by id.",
        parameters={"mark_id": "int"},
        handler=_get,
    )

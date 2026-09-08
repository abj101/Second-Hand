from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., str],
    ) -> None:
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schema_block(self) -> str:
        lines = ["Available tools (JSON {\"tool\": \"name\", ...args}):"]
        for spec in self._tools.values():
            lines.append(f"- {spec.name}({spec.parameters}): {spec.description}")
        return "\n".join(lines)

    def call(self, name: str, args: dict[str, Any]) -> str:
        spec = self._tools.get(name)
        if spec is None:
            return f"Unknown tool: {name}"
        try:
            return str(spec.handler(**args))
        except TypeError:
            try:
                return str(spec.handler(args))
            except Exception as exc:  # noqa: BLE001
                return f"Tool error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Tool error: {exc}"

    @staticmethod
    def normalize_tool_name(raw: str) -> str:
        name = (raw or "").strip().lower()
        aliases = {
            "recall": "memory_recall",
            "remember": "memory_remember",
            "notebook": "memory_notebook",
            "recheck": "request_recheck",
            "screen_perceive": "request_recheck",
        }
        return aliases.get(name, name)

"""Deterministic synthetic UI world (no game theme)."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from loop.adaptive.schema import Relation, SceneGraph, SceneNode

# Stable ids — never derived from randomized display labels.
ENTITY_NODE_ID = "node_entity_primary"
CONTROL_NODE_ID = "node_control_primary"
DISPLAY_NODE_ID = "node_display_primary"
ENTITY_TYPE_ID = "type_unit"
SCALAR_FIELD_ID = "field_scalar"
SNAPSHOT_PREFIX = "snap_"

_WORDS = (
    "alpha",
    "bravo",
    "cinder",
    "delta",
    "ember",
    "frost",
    "grove",
    "haven",
    "ivory",
    "jade",
    "kite",
    "lumen",
    "moss",
    "nexus",
    "orbit",
    "prism",
    "quartz",
    "ridge",
    "spark",
    "terra",
)


def _label_words(seed: int) -> tuple[str, str, str]:
    rng = random.Random(seed)
    picks = rng.sample(_WORDS, 3)
    return picks[0], picks[1], picks[2]


def _snapshot_id(seed: int, tick: int) -> str:
    h = hashlib.sha256(f"{seed}:{tick}".encode()).hexdigest()[:12]
    return f"{SNAPSHOT_PREFIX}{h}"


@dataclass
class NeutralWorld:
    layout_scale: float = 1.0
    origin: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        self._seed = 0
        self._tick = 0
        self._scalar = 0
        self._labels: tuple[str, str, str] = _label_words(0)

    def reset(self, seed: int) -> SceneGraph:
        self._seed = seed
        self._tick = 0
        self._scalar = 0
        self._labels = _label_words(seed)
        return self._scene()

    def click(self, node_id: str) -> None:
        if node_id == CONTROL_NODE_ID:
            self._scalar += 1
            self._tick += 1

    def undo_click(self, node_id: str) -> None:
        if node_id == CONTROL_NODE_ID and self._scalar > 0:
            self._scalar -= 1
            self._tick += 1

    def ocr_tokens(self) -> list[tuple[str, tuple[float, float, float, float]]]:
        out: list[tuple[str, tuple[float, float, float, float]]] = []
        for node in self._scene().nodes:
            label = str(node.attributes.get("label", ""))
            if "numeric" in node.attributes:
                text = f"{label}:{node.attributes['numeric']}"
            else:
                text = label
            out.append((text, node.bbox))
        return out

    def click_at(self, x: float, y: float) -> None:
        for node in self._scene().nodes:
            x0, y0, x1, y1 = node.bbox
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.click(node.node_id)
                return

    def noop(self) -> None:
        self._tick += 1

    def observe(self) -> tuple[object | None, SceneGraph, dict]:
        graph = self._scene()
        truth = {
            "scalar_field_id": SCALAR_FIELD_ID,
            "scalar_value": self._scalar,
            "entity_type_id": ENTITY_TYPE_ID,
            "entity_node_id": ENTITY_NODE_ID,
            "labels": {
                "entity": self._labels[0],
                "control": self._labels[1],
                "display": self._labels[2],
            },
            "seed": self._seed,
        }
        pixels = self._render_rgba()
        return pixels, graph, truth

    def _scene(self) -> SceneGraph:
        sid = _snapshot_id(self._seed, self._tick)
        nodes = (
            SceneNode(
                node_id=ENTITY_NODE_ID,
                bbox=(0.1, 0.2, 0.45, 0.7),
                evidence_ids=(),
                attributes={
                    "fill": (90, 120, 200),
                    "label": self._labels[0],
                    "type_id": ENTITY_TYPE_ID,
                },
            ),
            SceneNode(
                node_id=CONTROL_NODE_ID,
                bbox=(0.55, 0.55, 0.85, 0.85),
                evidence_ids=(),
                attributes={
                    "fill": (200, 90, 90),
                    "label": self._labels[1],
                    "clickable": True,
                },
            ),
            SceneNode(
                node_id=DISPLAY_NODE_ID,
                bbox=(0.55, 0.15, 0.9, 0.4),
                evidence_ids=(),
                attributes={
                    "fill": (120, 180, 120),
                    "label": self._labels[2],
                    "numeric": self._scalar,
                },
            ),
        )
        rels = (
            Relation(
                rel_id="rel_display_entity",
                kind="shows_value_of",
                src=DISPLAY_NODE_ID,
                dst=ENTITY_NODE_ID,
            ),
        )
        return SceneGraph(snapshot_id=sid, nodes=nodes, relations=rels)

    def _render_rgba(self) -> object | None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None

        w, h = 320, 240
        ox, oy = self.origin
        scale = self.layout_scale
        img = Image.new("RGBA", (w, h), (30, 30, 40, 255))
        draw = ImageDraw.Draw(img)
        for node in self._scene().nodes:
            x0, y0, x1, y1 = node.bbox
            px0 = int((x0 * scale + ox) * w)
            py0 = int((y0 * scale + oy) * h)
            px1 = int((x1 * scale + ox) * w)
            py1 = int((y1 * scale + oy) * h)
            fill = node.attributes.get("fill", (128, 128, 128))
            draw.rectangle([px0, py0, px1, py1], fill=fill + (255,))
            label = str(node.attributes.get("label", ""))
            if "numeric" in node.attributes:
                label = f"{label}:{node.attributes['numeric']}"
            draw.text((px0 + 4, py0 + 4), label, fill=(255, 255, 255, 255))
        return img

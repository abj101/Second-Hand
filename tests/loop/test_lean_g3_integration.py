"""G3 admission, diagnostic rows, cache/schema freshness (no GPU, input, or network)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from dataclasses import replace

from loop.adaptive.cli import _qualification_frame_row
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.runtime import project
from loop.adaptive.schema import FieldSpec, SceneGraph, SceneNode, empty_schema
from loop.adaptive.session import Session


def _frame(text: str = "Play", *, fill="blue") -> Image.Image:
    image = Image.new("RGB", (200, 200), color=(12, 12, 12))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 100, 100), fill=fill)
    draw.text((44, 48), text, fill=(255, 255, 255))
    return image


def test_qualification_row_carries_cache_and_unavailable_source(tmp_path: Path):
    session = Session(tmp_path)
    assembler = SceneAssembler()
    calls: list[int] = []

    def ocr(_image: Image.Image):
        calls.append(1)
        return [("Play", (0.20, 0.25, 0.45, 0.45))]

    frame = _frame()
    _, first = assembler.update(frame, ocr=ocr, ledger=session.ledger)
    snap = session.observe(first)
    row = _qualification_frame_row(
        first,
        snap,
        semantic_required=False,
        ledger=session.ledger,
        assembler=assembler,
        schema_field_ids=(),
    )
    assert row["frame_reused"] is False
    assert row["source_diagnostics"] is None
    assert row["eligible_offers"] >= 0

    _, reused = assembler.update(frame.copy(), ocr=ocr, ledger=session.ledger)
    snap2 = session.observe(reused)
    row2 = _qualification_frame_row(
        reused,
        snap2,
        semantic_required=False,
        ledger=session.ledger,
        assembler=assembler,
    )
    assert calls == [1]
    assert row2["frame_reused"] is True
    assert row2["cache_hits"] == 1
    assert row2["measurement_digest"] == assembler.measurement_digest
    assert [node.node_id for node in reused.nodes] == [
        node.node_id for node in first.nodes
    ]


def test_missing_required_schema_field_is_unknown_not_remembered(tmp_path: Path):
    schema = replace(empty_schema(), fields=(FieldSpec("score", "number"),))
    session = Session(tmp_path)
    session._schema = schema
    graph = SceneGraph(
        snapshot_id="gone",
        nodes=(
            SceneNode(
                "ocr_1",
                (0.1, 0.1, 0.2, 0.2),
                (),
                {"label": "Play", "registry_id": "reg_0"},
            ),
        ),
    )
    snap = project(graph, schema)
    assert "score" not in snap.fields
    row = _qualification_frame_row(
        graph,
        snap,
        semantic_required=False,
        ledger=session.ledger,
        schema_field_ids=("score",),
    )
    assert row["required_field_status"] == {"score": "unknown"}
    assert row["field_values"].get("score") in (None, "")


def test_duplicate_titles_stay_distinct_and_black_frame_is_not_a_cache_hit():
    assembler = SceneAssembler()
    ocr_calls: list[str] = []

    def ocr(image: Image.Image):
        mean = image.resize((1, 1)).getpixel((0, 0))[0]
        if mean < 8:
            ocr_calls.append("black")
            return []
        ocr_calls.append("cards")
        return [
            ("Strike", (0.10, 0.70, 0.30, 0.95)),
            ("Strike", (0.40, 0.70, 0.60, 0.95)),
        ]

    combat = _frame("Strike")
    _, scene = assembler.update(combat, ocr=ocr)
    titles = [
        str(node.attributes.get("label") or node.attributes.get("text") or "")
        for node in scene.nodes
        if node.node_id.startswith("ocr_")
    ]
    assert titles.count("Strike") == 2
    ids = [
        node.attributes.get("registry_id")
        for node in scene.nodes
        if node.attributes.get("registry_id")
    ]
    assert len(ids) == len(set(ids)) or None in ids

    black = Image.new("RGB", (200, 200), color=(0, 0, 0))
    assembler.update(black, ocr=ocr)
    assert assembler.frame_reused is False
    assert "black" in ocr_calls
    assert assembler.screen_replaced is True

from __future__ import annotations

from pathlib import Path
import re
import sqlite3

from PIL import Image, ImageDraw

from loop.memory.screen_objects import ScreenObjectStore
from loop.os.aim import AimMark, build_aim, build_marks, paint_aim

_OBJECT_N = re.compile(r"^object:\d+$")


def _buttons() -> Image.Image:
    img = Image.new("RGB", (400, 300), (30, 30, 40))
    draw = ImageDraw.Draw(img)
    draw.rectangle((160, 220, 240, 260), fill=(200, 200, 200))
    draw.text((170, 230), "Continue", fill=(0, 0, 0))
    return img


def test_build_marks_matches_build_aim_ids() -> None:
    image = _buttons()
    painted = build_aim(image)
    marks = build_marks(image)
    assert len(marks) == len(painted.marks)
    assert [m.id for m in marks] == [m.id for m in painted.marks]


def test_screen_object_store_listing(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "objects.db")
    marks = (
        AimMark(id=1, x=0.5, y=0.8, x0=0.4, y0=0.7, x1=0.6, y1=0.9, label="Continue"),
        AimMark(id=2, x=0.3, y=0.63, x0=0.2, y0=0.56, x1=0.4, y1=0.70, label="Slime", role="enemy"),
    )
    store.save_frame(run_id="run1", step_id=1, marks=marks, screen="title")
    listing = store.listing()
    assert "1@" in listing
    assert "Continue" in listing
    assert store.by_id(2) is not None
    assert store.enemies()[0].label == "Slime"
    store.close()


def test_paint_aim_still_draws_grid() -> None:
    image = _buttons()
    marks = build_marks(image)
    out = paint_aim(image, marks=marks)
    assert out.size == image.size
    assert out.tobytes() != image.tobytes()


def test_screen_object_store_preserves_instance_metadata(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "objects.db")
    mark = AimMark(
        1, .4, .9, .33, .78, .47, 1.0, "Strike", .94,
        object_id="card_2", role="card", source="ocr-hand", confidence=.94,
    )
    store.save_frame(run_id="r", step_id=1, marks=[mark], screen="combat")
    obj = store.by_object_id("card_2")
    assert obj is not None
    assert obj.role == "card"
    assert obj.provenance == "ocr-hand"
    assert store.hand_cards() == [obj]
    store.close()


def test_screen_object_store_repairs_duplicate_stable_card_ids(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "objects.db")
    marks = [
        AimMark(1, .35, .9, .28, .78, .42, 1.0, "Strike", object_id="card_1", role="card"),
        AimMark(2, .50, .9, .43, .78, .57, 1.0, "Strike", object_id="card_1", role="card"),
        AimMark(3, .65, .9, .58, .78, .72, 1.0, "Defend", object_id="card_2", role="card"),
    ]

    stamped = store.save_frame(run_id="r", step_id=2, marks=marks, screen="combat")

    ids = [mark.object_id for mark in stamped if mark.role == "card"]
    assert len(ids) == len(set(ids)) == 3
    assert all(store.by_object_id(object_id) is not None for object_id in ids)
    store.close()


def test_screen_object_store_additively_migrates_legacy_db(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE screen_objects (run_id TEXT, step_id INTEGER, mark_id INTEGER, "
        "x REAL,y REAL,x0 REAL,y0 REAL,x1 REAL,y1 REAL,label TEXT,kind TEXT,source TEXT,"
        "screen TEXT,listing TEXT,PRIMARY KEY(run_id,step_id,mark_id))"
    )
    con.commit()
    con.close()
    store = ScreenObjectStore(path)
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(screen_objects)")}  # noqa: SLF001
    assert {"object_id", "role", "confidence", "provenance"} <= columns
    store.close()


def test_save_frame_never_persists_object_ids_on_domain_roles(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "domain_ids.db")
    marks = (
        AimMark(
            id=1, x=0.75, y=0.58, x0=0.68, y0=0.50, x1=0.82, y1=0.68,
            label="Nibbit", role="enemy", object_id="object:10",
        ),
        AimMark(
            id=2, x=0.50, y=0.90, x0=0.40, y0=0.80, x1=0.60, y1=1.0,
            label="Strike", role="card", object_id="object:8",
        ),
        AimMark(
            id=3, x=0.08, y=0.80, x0=0.04, y0=0.76, x1=0.12, y1=0.84,
            label="icon", role="unknown", object_id="",
        ),
    )
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    domain = [obj for obj in store.objects if obj.role in {"card", "enemy", "player"}]
    assert domain
    assert all(not _OBJECT_N.match(obj.object_id) for obj in domain)
    enemy = next(obj for obj in store.objects if obj.role == "enemy")
    card = next(obj for obj in store.objects if obj.role == "card")
    assert enemy.object_id == "enemy_1"
    assert card.object_id == "card_1"
    store.close()

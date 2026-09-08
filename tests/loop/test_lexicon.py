from __future__ import annotations

from pathlib import Path

from PIL import Image

from loop.memory.journal import FactJournal
from loop.memory.lexicon import MarkLexicon, screen_from_labels
from loop.os.aim import AimMark, _merge_aim_marks, _relabel_from_lexicon, build_aim


def _icon(x: float = 0.4, y: float = 0.7, label: str = "icon") -> AimMark:
    return AimMark(
        id=0,
        x=x,
        y=y,
        x0=x - 0.04,
        y0=y - 0.04,
        x1=x + 0.04,
        y1=y + 0.04,
        label=label,
        score=0.9,
    )


def test_unlabeled_map_button_relabels_next_merge() -> None:
    lex = MarkLexicon()
    assert lex.discover_click(
        screen="combat",
        next_screen="map",
        x=0.40,
        y=0.70,
        clicked_label="icon",
        pixel_changed=True,
    )
    assert lex.label_for("combat", 0.40, 0.70) == "Map"
    merged = _merge_aim_marks([], [], cap=8, gui=[_icon()])
    labeled = _relabel_from_lexicon(merged, lex, "combat")
    assert any(m.label == "Map" for m in labeled)


def test_live_ocr_wins_over_stale_map() -> None:
    lex = MarkLexicon()
    lex.remember("title", 0.40, 0.70, "Map", source="transit")
    cont = AimMark(
        id=0,
        x=0.40,
        y=0.70,
        x0=0.30,
        y0=0.66,
        x1=0.50,
        y1=0.74,
        label="Continue",
        score=0.9,
    )
    merged = _merge_aim_marks([cont], [], cap=8, gui=[_icon()])
    labeled = _relabel_from_lexicon(merged, lex, "title")
    assert labeled[0].label == "Continue"
    lex.ingest("title", labeled)
    assert lex.label_for("title", 0.40, 0.70) == "Continue"


def test_vlm_choice_does_not_write_a_slot() -> None:
    lex = MarkLexicon()
    assert not lex.discover_click(
        screen="combat",
        next_screen="combat",
        x=0.40,
        y=0.70,
        clicked_label="icon",
        pixel_changed=False,
    )
    assert lex.label_for("combat", 0.40, 0.70) is None
    assert not hasattr(lex.discover_click, "choice")


def test_hover_ocr_names_unlabeled_slot() -> None:
    lex = MarkLexicon()
    assert lex.discover_click(
        screen="combat",
        next_screen="combat",
        x=0.08,
        y=0.06,
        clicked_label="",
        pixel_changed=True,
        hovered=True,
        words=[(0.09, 0.05, "Map")],
    )
    assert lex.label_for("combat", 0.08, 0.06) == "Map"


def test_neighbor_bin_still_matches() -> None:
    lex = MarkLexicon()
    lex.remember("combat", 0.40, 0.70, "Map", source="hover")
    assert lex.label_for("combat", 0.41, 0.72) == "Map"


def test_screen_from_labels_prefers_end_turn() -> None:
    assert screen_from_labels(["Strike", "End Turn"], fallback="map") == "combat"
    assert screen_from_labels(["icon"], fallback="combat") == "combat"


def test_journal_slots_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    journal.discover_overlay(
        screen="combat",
        next_screen="map",
        x=0.08,
        y=0.06,
        clicked_label="icon",
        pixel_changed=True,
    )
    loaded = FactJournal(path=path)
    assert loaded.lexicon.label_for("combat", 0.08, 0.06) == "Map"


def test_ingest_skips_placeholder() -> None:
    lex = MarkLexicon()
    lex.ingest("combat", [_icon()])
    assert not lex.slots


def test_merge_keeps_learned_top_map_icon() -> None:
    lex = MarkLexicon()
    lex.remember("combat", 0.08, 0.05, "Map", source="transit")
    chrome = _icon(x=0.08, y=0.05)
    strike = AimMark(
        id=2,
        x=0.40,
        y=0.85,
        x0=0.32,
        y0=0.80,
        x1=0.48,
        y1=0.90,
        label="Strike",
    )
    dropped = _merge_aim_marks([strike], [], cap=8, gui=[chrome])
    assert all(m.y > 0.12 for m in dropped)
    kept = _merge_aim_marks(
        [strike], [], cap=8, gui=[chrome], lexicon=lex, screen="combat"
    )
    assert any(m.y <= 0.12 for m in kept)
    labeled = _relabel_from_lexicon(kept, lex, "combat")
    assert any(m.label == "Map" for m in labeled)


def test_build_aim_lists_learned_map() -> None:
    lex = MarkLexicon()
    lex.remember("combat", 0.40, 0.70, "Map", source="hover")
    image = Image.new("RGB", (400, 300), (8, 8, 8))
    guide = build_aim(
        image,
        gui_marks=[_icon()],
        lexicon=lex,
        screen="combat",
    )
    assert ":Map" in guide.listing()


def test_lexicon_does_not_relabel_got_it_without_ocr() -> None:
    lex = MarkLexicon()
    lex.remember("tutorial", 0.50, 0.78, "Got it", source="transit")
    merged = _merge_aim_marks([], [], cap=8, gui=[_icon(x=0.96, y=0.04)])
    labeled = _relabel_from_lexicon(merged, lex, "tutorial", ocr=[])
    assert all(m.label != "Got it" for m in labeled)


def test_strip_unverified_got_it_keeps_ocr_backed() -> None:
    from loop.os.aim import _strip_unverified_menu_labels

    ocr = [
        AimMark(
            id=1,
            x=0.50,
            y=0.78,
            x0=0.44,
            y0=0.72,
            x1=0.56,
            y1=0.84,
            label="Got it",
            score=0.95,
        )
    ]
    mark = AimMark(
        id=2,
        x=0.50,
        y=0.78,
        x0=0.44,
        y0=0.72,
        x1=0.56,
        y1=0.84,
        label="Got it",
        score=0.5,
    )
    kept = _strip_unverified_menu_labels([mark], ocr)
    assert kept[0].label == "Got it"
    drift = AimMark(
        id=3,
        x=0.96,
        y=0.04,
        x0=0.90,
        y0=0.0,
        x1=0.99,
        y1=0.08,
        label="Got it",
        score=0.5,
    )
    stripped = _strip_unverified_menu_labels([drift], ocr)
    assert stripped[0].label == ""


def test_lexicon_forget_removes_slot() -> None:
    from loop.memory.lexicon import MarkLexicon

    lex = MarkLexicon()
    lex.remember("tutorial", 0.5, 0.8, "Got it", source="ocr")
    assert lex.label_for("tutorial", 0.5, 0.8) == "Got it"
    assert lex.forget("tutorial", 0.5, 0.8)
    assert lex.label_for("tutorial", 0.5, 0.8) is None


def test_relabel_rejects_strike_in_hud_corner() -> None:
    from loop.os.aim import _eligible_label_for_region, _strip_region_invalid_labels

    hud = AimMark(id=1, x=0.05, y=0.05, x0=0.0, y0=0.0, x1=0.1, y1=0.1, label=":icon")
    assert not _eligible_label_for_region(hud, "Strike", screen="combat")
    hand = AimMark(id=2, x=0.5, y=0.86, x0=0.4, y0=0.8, x1=0.6, y1=0.92, label="Cards")
    stripped = _strip_region_invalid_labels([hand], screen="combat")
    assert stripped[0].label == ":icon"

from __future__ import annotations

from loop.os.sts_stats import extract_sts_stats, stats_from_blob


def test_stats_from_blob_tutorial_combat() -> None:
    blob = (
        "80/80 99 Defeat enemies by playing cards from your hand! "
        "Cards require Energy to play. Once you are out, end your turn. "
        "0 At the start of your turn, new cards are drawn "
        "and your Energy is replenished. Defend Deal 9 damage 46/46 "
        "Combat Basics 3/3 (1 of 3) End Turn 1"
    )
    stats = stats_from_blob(blob, screen="tutorial")
    assert stats["screen"] == "tutorial"
    assert stats["turn"] == "ours"
    player = stats["player"] or {}
    assert player.get("hp") == 80
    assert player.get("hp_max") == 80
    assert player.get("energy") == 3
    assert player.get("energy_max") == 3
    enemies = stats["enemies"]
    assert any(e.get("hp") == 46 and e.get("hp_max") == 46 for e in enemies)
    tutorial = stats["tutorial"] or {}
    assert tutorial.get("step") == 1
    assert tutorial.get("total") == 3
    assert tutorial.get("title") == "Combat Basics"
    assert "end turn" in stats["markers"]


def test_stats_from_blob_select_no_combat_hud() -> None:
    blob = (
        "The Ironclad 80/80 99 Burning Blood. At the end of combat, heal 6 HP. "
        "The last soldier of the Ironclads."
    )
    stats = stats_from_blob(blob, screen="select")
    assert stats["screen"] == "select"
    assert stats.get("player") is None or "hp" not in (stats.get("player") or {})


def test_stats_from_blob_map_no_hp() -> None:
    blob = "62/80 99 Legend Unknown Merchant Treasure Rest Enemy Elite"
    stats = stats_from_blob(blob, screen="map")
    assert stats["screen"] == "map"
    player = stats.get("player") or {}
    assert "hp" not in player


def test_stats_from_blob_combat_energy() -> None:
    blob = "End Turn 80/80 energy 3/3 46/46"
    stats = stats_from_blob(blob, screen="combat")
    player = stats["player"] or {}
    assert player.get("hp") == 80
    assert player.get("energy") == 3


def test_extract_sts_stats_blank_image() -> None:
    from PIL import Image

    stats = extract_sts_stats(Image.new("RGB", (64, 64), (20, 20, 20)))
    assert stats["ocr_confidence"] in {"high", "partial"}
    assert "screen" in stats


def test_extract_sts_stats_combat_fixture_reads_ocr(monkeypatch) -> None:
    """Diagnosis: pipeline OCR works on saved combat frame — live empties are capture/crop."""
    from pathlib import Path

    from PIL import Image

    monkeypatch.setenv("AVATAR_OCR", "auto")
    from loop.os import ocr as ocr_mod

    ocr_mod._ENGINE = None
    ocr_mod._TRIED_RAPID = False
    path = Path("loop/fixtures/sts2_screens/combat_turn.jpg")
    if not path.exists():
        return
    stats = extract_sts_stats(Image.open(path).convert("RGB"))
    blob = str(stats.get("ocr") or "")
    assert blob.strip()
    assert "end turn" in blob.lower() or "defend" in blob.lower()

from pathlib import Path

from loop.eval.two_stage_matrix import MatrixCase, default_cases


def test_default_cases_discovers_images_without_fixture_labels(tmp_path: Path) -> None:
    (tmp_path / "combat.jpg").touch()
    (tmp_path / "menu.png").touch()
    (tmp_path / "labels.jsonl").touch()
    cases = default_cases(fixture_dir=tmp_path)
    assert cases == [MatrixCase(file="combat.jpg"), MatrixCase(file="menu.png")]


def test_default_cases_applies_only_a_collection_limit(tmp_path: Path) -> None:
    for name in ("a.jpg", "b.png", "c.webp"):
        (tmp_path / name).touch()
    assert [case.file for case in default_cases(fixture_dir=tmp_path, limit=2)] == ["a.jpg", "b.png"]

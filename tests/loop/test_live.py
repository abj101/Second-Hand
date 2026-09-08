from __future__ import annotations

from pathlib import Path

from loop.live import (
    clear_stop,
    list_runs,
    live_path,
    publish,
    read,
    request_stop,
    stop_requested,
    tail_text,
)


def test_publish_merges_and_read_roundtrip(tmp_path: Path) -> None:
    first = publish(tmp_path, job="collect", phase="thinking", thought="look at bar")
    assert first["job"] == "collect"
    assert live_path(tmp_path).exists()
    publish(tmp_path, phase="acting", action="a")
    status = read(tmp_path)
    assert status["thought"] == "look at bar"
    assert status["action"] == "a"
    assert status["phase"] == "acting"


def test_stop_flag(tmp_path: Path) -> None:
    assert stop_requested(tmp_path) is False
    request_stop(tmp_path)
    assert stop_requested(tmp_path) is True
    clear_stop(tmp_path)
    assert stop_requested(tmp_path) is False


def test_list_runs_and_tail(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "aaa_fake_collect").mkdir(parents=True)
    (runs / "bbb_fake_collect").mkdir()
    names = [p.name for p in list_runs(tmp_path)]
    assert names[0] == "bbb_fake_collect"
    log = runs / "bbb_fake_collect" / "turns.jsonl"
    log.write_text("one\n\ntwo\nthree\n", encoding="utf-8")
    assert tail_text(log, lines=2) == "two\nthree"

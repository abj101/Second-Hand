"""Live shadow collect (monkeypatched window; no mouse)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.cli import cmd_collect
from loop.adaptive.world import NeutralWorld
from loop.os.window import CaptureProvenance, WindowTarget


@pytest.fixture
def dummy_target() -> WindowTarget:
    return WindowTarget(
        hwnd=1, title="dummy", left=0, top=0, width=320, height=240
    )


def test_arm_without_window_returns_2():
    with tempfile.TemporaryDirectory() as tmp:
        rc = cmd_collect(["--workspace", str(tmp), "--arm"])
        assert rc == 2


def test_shadow_window_missing_returns_2():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc = cmd_collect(["--workspace", str(root), "--window", "no-such-window-xyz"])
        assert rc == 2


def test_shadow_observe_only(monkeypatch, dummy_target):
    world = NeutralWorld()
    world.reset(0)

    monkeypatch.setattr(
        "loop.os.window.list_windows",
        lambda _title: [dummy_target],
    )
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)

    def _grab(_target):
        return world.observe()[0].convert("RGB")

    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda target: (
            _grab(target),
            CaptureProvenance(
                source="hwnd-imagegrab",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="trusted",
                reason="test",
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _im: world.ocr_tokens())

    mouse_execute = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse_execute)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc = cmd_collect(
            ["--workspace", str(root), "--window", "dummy", "--steps", "4"]
        )
        assert rc != 0
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        assert summary["mode"] == "shadow"
        assert summary["frames"] == 4
        assert summary["gates"]["stability"]["pass"] is False
        assert summary["gates"]["qwen_load"]["pass"] is False
        mouse_execute.assert_not_called()


def test_shadow_rejects_untrusted_capture(monkeypatch, dummy_target):
    world = NeutralWorld()
    world.reset(0)
    monkeypatch.setattr("loop.os.window.list_windows", lambda _title: [dummy_target])
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            world.observe()[0].convert("RGB"),
            CaptureProvenance(
                source="uncertain",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="uncertain",
                reason="test",
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(RuntimeError, match="untrusted capture"):
            cmd_collect(["--workspace", tmp, "--window", "dummy", "--steps", "1"])



def test_shadow_summary_carries_acquisition_provenance(monkeypatch, dummy_target):
    world = NeutralWorld()
    world.reset(0)

    monkeypatch.setattr("loop.os.window.list_windows", lambda _title: [dummy_target])
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.target_identity",
        lambda target, selector="": {"hwnd": int(target.hwnd), "pid": 42},
    )
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda target: (
            world.observe()[0].convert("RGB"),
            CaptureProvenance(
                source="hwnd-imagegrab",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="trusted",
                reason="test",
                acquisition_ts_monotonic=1234.5,
                acquisition_origin="pre-grab-sync",
                acquisition_age_known=True,
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _im: world.ocr_tokens())
    mouse_execute = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse_execute)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc = cmd_collect(["--workspace", str(root), "--window", "dummy", "--steps", "2"])
        assert rc != 0
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        assert summary["mode"] == "shadow"
        capture = summary["capture"]
        assert capture["acquisition_origin"] == "pre-grab-sync"
        assert capture["capture_age_known"] is True
        assert capture["capture_ts_monotonic"] == 1234.5
        assert capture["observation_sequence"] >= 1
        mouse_execute.assert_not_called()

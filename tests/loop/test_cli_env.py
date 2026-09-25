from __future__ import annotations

from pathlib import Path

import pytest

from loop.adaptive.cli import _load_repo_env, _optional_vlm


def test_repo_env_loads_model_without_overriding_shell(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_BASE_URL=http://127.0.0.1:11434/v1\n"
        "OLLAMA_MODEL='gemma4:31b-cloud'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "operator-choice")

    _load_repo_env(env_file)

    assert __import__("os").environ["OLLAMA_BASE_URL"].endswith("/v1")
    assert __import__("os").environ["OLLAMA_MODEL"] == "operator-choice"


def test_optional_vlm_probes_native_ollama_url(monkeypatch):
    seen: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:31b-cloud")
    monkeypatch.setattr("httpx.get", lambda url, timeout: seen.append(url) or Response())

    vlm = _optional_vlm()

    assert seen == ["http://127.0.0.1:11434/api/tags"]
    assert vlm is not None and vlm.model == "gemma4:31b-cloud"


@pytest.mark.parametrize("tracker", ["fused", "fused-base", "sam2", "yolo-world"])
def test_benchmark_only_trackers_rejected_before_live_setup(tracker, monkeypatch, capsys):
    from loop.adaptive.cli import cmd_collect

    def unexpected_setup(*args, **kwargs):
        pytest.fail("benchmark-only tracker reached live window setup")

    monkeypatch.setattr("loop.os.window.list_windows", unexpected_setup)
    with pytest.raises(SystemExit) as exc:
        cmd_collect(["--workspace", "unused", "--window", "unused", "--tracker", tracker])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_stamp_capture_carries_acquisition_provenance():
    from loop.adaptive.cli import _stamp_capture

    row = _stamp_capture(
        {},
        {
            "integrity": "trusted",
            "hwnd": 7,
            "pid": 9,
            "capture_ts_monotonic": 12.5,
            "capture_age_known": True,
            "acquisition_origin": "pre-grab-sync",
            "observation_sequence": 3,
        },
        {"hwnd": 7, "pid": 9},
    )

    assert row["capture_ts_monotonic"] == 12.5
    assert row["capture_age_known"] is True
    assert row["acquisition_origin"] == "pre-grab-sync"
    assert row["observation_sequence"] == 3


def test_stamp_capture_reports_unknown_age_when_absent():
    from loop.adaptive.cli import _stamp_capture

    row = _stamp_capture({}, {"integrity": "trusted", "hwnd": 1, "pid": 2}, {})

    assert row["capture_age_known"] is False
    assert row["acquisition_origin"] == "unknown"
    assert "observation_sequence" not in row


def test_semantic_worker_is_absent_when_semantic_is_off():
    from loop.adaptive.cli import _semantic_worker_for

    assert _semantic_worker_for(None, None) is None


def test_semantic_worker_wraps_the_confirmer_for_live_collect():
    from loop.adaptive.cli import _semantic_worker_for
    from loop.adaptive.semantic_confirmation import SemanticConfirmer, SemanticWorker

    worker = _semantic_worker_for(SemanticConfirmer(None, model=""), None)
    assert isinstance(worker, SemanticWorker)
    try:
        assert worker.is_running
    finally:
        worker.shutdown(wait=True)

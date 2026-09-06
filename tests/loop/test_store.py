from __future__ import annotations

from pathlib import Path

from loop.log.schema import Observation, StepLog
from loop.log.store import EpisodeStore, registry_tool_names


def test_step_log_roundtrip(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    record = StepLog(
        session_id="s1",
        episode_id="e1",
        step_id=1,
        ts="2026-08-24T00:00:00+00:00",
        fixture="fake",
        observation=Observation(screenshot_ref="frames/e1/000001_obs.jpg"),
        thought="try b",
        policy_id="skip-models",
    )
    store.write_step(record)
    rows = store.read_steps()
    assert len(rows) == 1
    loaded = StepLog.model_validate(rows[0])
    assert loaded.fixture == "fake"
    assert loaded.observation.screenshot_ref.endswith("obs.jpg")
    assert loaded.paused == "off"
    assert loaded.terminal == "alive"


def test_replace_episode_steps_keeps_other_episodes(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    a = StepLog(
        session_id="s1",
        episode_id="e1",
        step_id=1,
        ts="2026-08-24T00:00:00+00:00",
        fixture="fake",
        observation=Observation(screenshot_ref="frames/e1/000001_obs.jpg"),
        thought="first",
    )
    b = StepLog(
        session_id="s1",
        episode_id="e2",
        step_id=1,
        ts="2026-08-24T00:00:01+00:00",
        fixture="fake",
        observation=Observation(screenshot_ref="frames/e2/000001_obs.jpg"),
        thought="second",
    )
    store.write_step(a)
    store.write_step(b)
    b.thought = "updated"
    b.return_to_go = 1.5
    store.replace_episode_steps("e2", [b])
    rows = store.read_steps()
    assert [r["episode_id"] for r in rows] == ["e1", "e2"]
    assert rows[0]["thought"] == "first"
    assert rows[1]["thought"] == "updated"
    assert rows[1]["return_to_go"] == 1.5


def test_resolve_run_dirs_latest_and_all(tmp_path: Path) -> None:
    from loop.log.store import iter_fixture_runs, resolve_run_dirs

    runs = tmp_path / "runs"
    older = runs / "20260826T010000Z_sts2_collect"
    newer = runs / "20260826T020000Z_sts2_collect"
    other = runs / "20260826T030000Z_elden_collect"
    for path in (older, newer, other):
        path.mkdir(parents=True)
    assert iter_fixture_runs("sts2", tmp_path)[0] == newer
    assert resolve_run_dirs("sts2", tmp_path) == [newer]
    pooled = resolve_run_dirs("sts2", tmp_path, all_runs=True)
    assert pooled == [newer, older]
    explicit = resolve_run_dirs("sts2", tmp_path, [older])
    assert explicit == [older.resolve()]


def test_registry_tool_names_filters_collector_audits() -> None:
    events = [
        {"kind": "mcp_registry", "tool": "memory_notebook"},
        {"kind": "collector_audit", "tool": "targeting_validate"},
        {"tool": "legacy_missing_kind"},
        {"provenance": "mcp_registry", "tool": "memory_recall"},
    ]
    assert registry_tool_names(events) == ["memory_notebook", "memory_recall"]


def test_step_log_telemetry_defaults_and_old_jsonl_load() -> None:
    legacy = {
        "session_id": "s1",
        "episode_id": "e1",
        "step_id": 1,
        "ts": "2026-08-24T00:00:00+00:00",
        "fixture": "sts2",
        "observation": {"screenshot_ref": "frames/e1/000001_obs.jpg"},
        "tool_events": [
            {"tool": "memory_notebook", "args": {}},
            {"kind": "collector_audit", "tool": "targeting_validate"},
        ],
    }
    loaded = StepLog.model_validate(legacy)
    assert loaded.perception_source == "other"
    assert loaded.llm1_called_this_turn is False
    assert loaded.llm1_reused_from_step is None
    assert loaded.reasoner_called_this_turn is False
    assert loaded.explicit_mcp_tools_called == []
    assert registry_tool_names(loaded.tool_events) == []

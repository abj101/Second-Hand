from __future__ import annotations

from pathlib import Path

from loop.agent.prompts import NOTEBOOK_SCHEMA_VERSION, prompt_id
from loop.eval.export import comparable_state, memory_key, notebook_schema_report
from loop.eval.sft import SFT_USER_TURN, sft_chat_row
from loop.log.schema import StepLog, Observation
from loop.memory.episodic import WorkingMemory
from loop.memory.grounded import GroundedStore, play_relevant_outcome
from loop.memory.journal import FactJournal
from loop.memory.notebook import Notebook

def test_prompt_id_includes_notebook_schema() -> None:
    base = prompt_id(arm="no_skill", pace="turn", architecture="overlay")
    assert len(base) == 16
    assert NOTEBOOK_SCHEMA_VERSION == "v1"


def test_grounded_promote_and_hunch() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    nb.ingest({"note": "mk_3 opens shop"}, screen="map", step_id=1)
    assert any(row.source == "vlm_note" for row in nb.grounded.facts.values())
    nb.promote_click(
        screen="map",
        label="Shop",
        device="mk_3",
        outcome="moved",
        prev_screen="map",
        next_screen="shop",
        prev_metrics={"screen": "map"},
        metrics={"screen": "shop"},
        step_id=2,
    )
    known, hunch = nb.grounded.rank_for_prompt(screen="map")
    assert known
    assert known[0].source == "outcome"


def test_play_relevant_requires_state_change() -> None:
    assert play_relevant_outcome(
        outcome="moved",
        prev_screen="combat",
        next_screen="combat",
        prev_metrics={"hp": 50},
        metrics={"hp": 50},
        label="",
    ) is False
    assert play_relevant_outcome(
        outcome="moved",
        prev_screen="combat",
        next_screen="combat",
        prev_metrics={"hp": 50},
        metrics={"hp": 40},
        label="Strike",
    ) is True


def test_notebook_reset_episode_clears_episode_scope_only() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal, memory_scope="run")
    nb.grounded.promote_outcome(screen="combat", value="mk_1 End Turn", step_id=1, scope="run")
    nb.grounded.add_vlm_note("temp", screen="combat", step_id=2, scope="episode")
    nb.reset_episode()
    scopes = {row.scope for row in nb.grounded.facts.values()}
    assert "run" in scopes
    assert "episode" not in scopes


def test_working_memory_general_blocked_hint() -> None:
    wm = WorkingMemory()
    for _ in range(3):
        wm.add(chord="mk_5", pixels=False, mad=0.001)
    hint = wm.blocked_hint()
    assert "mk_5" in hint
    assert "labeled mark" in hint


def test_dpo_memory_key_matching() -> None:
    base = {
        "session_id": "s",
        "episode_id": "e",
        "step_id": 1,
        "ts": "t",
        "fixture": "sts2",
        "observation": Observation(screenshot_ref="a.jpg"),
        "metrics": {"screen": "combat", "hp": 50, "energy": 3, "floor": 1, "block": 0},
    }
    a = StepLog(**{**base, "notebook_snapshot": "known=k1"})
    b = StepLog(**{**base, "notebook_snapshot": "known=k1", "step_id": 2})
    c = StepLog(**{**base, "notebook_snapshot": "known=k2", "step_id": 3})
    assert comparable_state(a, b)
    assert not comparable_state(a, c)
    assert memory_key(a) == "known=k1"


def test_notebook_schema_report_mixed(tmp_path) -> None:
    from loop.log.store import EpisodeStore

    store = EpisodeStore(tmp_path)
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=1,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a.jpg"),
            notebook_schema="v0",
        )
    )
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=2,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="b.jpg"),
            notebook_schema="v1",
        )
    )
    report = notebook_schema_report([tmp_path])
    assert report["mixed"] is True


def test_vlm_notes_only_in_hunch_not_bullet_lines() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    note = "mk_3 opens shop on map"
    nb.ingest({"note": note}, screen="map", step_id=1)
    block = nb.prompt_block(screen="map")
    assert "Hunch:" in block
    assert note in block
    assert not any(line.startswith("- ") and note in line for line in block.splitlines())


def test_play_relevant_rejects_label_only_moved() -> None:
    assert play_relevant_outcome(
        outcome="moved",
        prev_screen="combat",
        next_screen="combat",
        prev_metrics={"hp": 50, "energy": 3},
        metrics={"hp": 50, "energy": 3},
        label="Strike",
    ) is False


def test_memory_snapshot_matches_prompt_screen() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    nb.grounded.add_vlm_note("combat fact", screen="combat", step_id=1)
    nb.grounded.add_vlm_note("map fact", screen="map", step_id=2)
    hud = {"energy": 3}
    screen = "combat"
    block = nb.prompt_block(screen=screen, energy=3)
    snap = nb.memory_snapshot(screen=screen, energy=3)
    ranked_known, ranked_hunch = nb.grounded.rank_for_prompt(screen=screen, hud=hud)
    expected_keys = {row.key for row in ranked_known + ranked_hunch}
    assert expected_keys
    assert all(row.screen == "combat" for row in ranked_known + ranked_hunch)
    assert "combat fact" in block
    assert "map fact" not in block
    assert "known=" in snap or "hunch=" in snap
    assert "map fact" not in snap


def test_rank_for_prompt_is_pure() -> None:
    store = GroundedStore()
    store.add_vlm_note("stay on combat", screen="combat", step_id=1)
    row = next(iter(store.facts.values()))
    store.rank_for_prompt(screen="map")
    store.rank_for_prompt(screen="map")
    assert row.miss_count == 0
    store.decay_stale_facts(screen="map")
    assert row.miss_count == 1
    store.decay_stale_facts(screen="map")
    assert row.miss_count == 2


def test_bind_overlay_promotes_grounded_outcome() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    listing = "5@321,862:No 6@540,858:Yes"
    saved = nb.bind_overlay(mark_id=5, label="No", listing=listing, step_id=1, screen="tutorial")
    assert saved == "mk_5 is No on dialog"
    outcomes = [row for row in nb.grounded.facts.values() if row.source == "outcome"]
    assert outcomes
    assert any("mk_5 is No on dialog" in row.value for row in outcomes)


def test_promote_turn_reward_on_positive_delta() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    line = nb.promote_turn_reward(
        screen="map",
        label="Continue",
        device="mk_2",
        delta=0.5,
        prev_metrics={"screen": "title"},
        metrics={"screen": "map", "choice": "Continue"},
        step_id=3,
    )
    assert line
    outcomes = [row for row in nb.grounded.facts.values() if row.source == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0].screen == "map"
    assert "Continue" in outcomes[0].value


def test_sft_chat_uses_logged_user_prompt() -> None:
    custom = "Notebook:\nGoal: win a run\nKnown: mk_1 End Turn → moved"
    row = {
        "fixture": "sts2",
        "obs": "obs.jpg",
        "chord": {"mark": 1, "click_x": 0.5, "click_y": 0.7},
        "user_prompt_text": custom,
    }
    chat = sft_chat_row(row, run_dir=Path("runs/x"))
    user_parts = chat["messages"][1]["content"]
    text = next(part["text"] for part in user_parts if part.get("type") == "text")
    assert text == custom
    assert text != SFT_USER_TURN


def test_cross_episode_run_scope_carry() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal, memory_scope="run")
    nb.grounded.promote_outcome(screen="combat", value="mk_1 End Turn", step_id=1, scope="run")
    nb.grounded.add_vlm_note("temp episode note", screen="combat", step_id=2, scope="episode")
    nb.reset_episode()
    scopes = {row.scope for row in nb.grounded.facts.values()}
    assert "run" in scopes
    assert "episode" not in scopes
    assert any(row.value == "mk_1 End Turn" for row in nb.grounded.facts.values())


def test_dpo_comparable_state_gates_on_snapshot() -> None:
    base = {
        "session_id": "s",
        "episode_id": "e",
        "step_id": 1,
        "ts": "t",
        "fixture": "sts2",
        "observation": Observation(screenshot_ref="a.jpg"),
        "metrics": {"screen": "combat", "hp": 50, "energy": 3, "floor": 1, "block": 0},
    }
    same_metrics_a = StepLog(**{**base, "notebook_snapshot": "known=k1|screen=combat"})
    same_metrics_b = StepLog(**{**base, "notebook_snapshot": "known=k2|screen=combat", "step_id": 2})
    assert not comparable_state(same_metrics_a, same_metrics_b)
    assert memory_key(same_metrics_a) != memory_key(same_metrics_b)


def test_journal_episode_scope_clears_notes() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal, memory_scope="episode")
    nb.ingest({"note": "episode-only note"}, screen="combat", step_id=1)
    assert any(fact.kind == "note" for fact in journal.facts.values())
    nb.reset_episode()
    assert not any(fact.kind == "note" for fact in journal.facts.values())


def test_pixel_only_moved_does_not_promote_grounded_outcome() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    line = nb.promote_click(
        screen="combat",
        label="Strike",
        device="mk_2",
        outcome="moved",
        prev_screen="combat",
        next_screen="combat",
        prev_metrics={"screen": "combat", "hp": 50, "energy": 3},
        metrics={"screen": "combat", "hp": 50, "energy": 3},
        step_id=4,
    )
    assert line is None
    assert not any(row.source == "outcome" for row in nb.grounded.facts.values())


def test_verified_hp_change_promotes_card_success() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    line = nb.promote_click(
        screen="combat",
        label="Strike",
        device="mk_2",
        outcome="moved",
        prev_screen="combat",
        next_screen="combat",
        prev_metrics={"screen": "combat", "hp": 50, "energy": 3},
        metrics={"screen": "combat", "hp": 40, "energy": 2},
        step_id=5,
    )
    assert line
    assert any(row.source == "outcome" and row.evidence == "transition" for row in nb.grounded.facts.values())


def test_combat_won_promotes_transition_fact_once() -> None:
    journal = FactJournal(path=None)
    nb = Notebook(journal)
    metrics = {"screen": "reward", "combat_won": True, "combat_won_transition_id": "ep1:1"}
    first = nb.promote_turn_reward(
        screen="reward",
        label="Loot",
        device="mk_1",
        delta=0.0,
        prev_metrics={"screen": "combat"},
        metrics=metrics,
        step_id=9,
    )
    second = nb.promote_turn_reward(
        screen="reward",
        label="Loot",
        device="mk_1",
        delta=0.0,
        prev_metrics={"screen": "combat"},
        metrics=metrics,
        step_id=10,
    )
    assert first
    assert second is None
    outcomes = [row for row in nb.grounded.facts.values() if row.evidence == "combat_won"]
    assert len(outcomes) == 1

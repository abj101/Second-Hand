import json

from agent.brief import DEFAULTS, GENERAL, defaults, load_brief
from agent.trace import Trace


def test_pokemon_brief_defaults_and_size():
    assert defaults("pokemon_red") == {} and DEFAULTS == {}  # no preferred names
    assert len(load_brief("pokemon_red").encode()) <= 8192


def test_trace_tail_reads_last_rows(tmp_path):
    trace = Trace(tmp_path / "trace.jsonl")
    for step in range(1000):
        trace.write({"step": step})

    assert trace.tail(2) == [{"step": 998}, {"step": 999}]


def test_general_prompt_has_no_game_specific_words():
    import re
    from agent.brief import defaults
    words = set(re.findall(r"\w+", GENERAL.casefold()))
    # Source-derived: pokemon_red.json defaults plus the Pokémon Red truth reader title.
    values = defaults("pokemon_red")
    game_words = {"pokemon", "pokémon", "red", "rival", "blue", "hand", *(value.casefold() for value in values.values())}
    assert not words & game_words


def test_prompt_version_tracks_exit_knowledge(tmp_path):
    from agent.loop import Loop
    from agent.memory import Memory
    from tests.agent.fakes import GridEnv, StubModel

    assert Loop(GridEnv(), StubModel([]), Memory(tmp_path / "memory"), "grid").prompt_version == "e5-perceiver-10"

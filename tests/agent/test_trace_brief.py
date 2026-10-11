import json

from agent.brief import DEFAULTS, defaults, load_brief
from agent.trace import Trace


def test_pokemon_brief_defaults_and_size():
    assert defaults("pokemon_red") == {"name": "Hand", "rival": "Blue"}
    assert len(load_brief("pokemon_red").encode()) <= 8192
    assert DEFAULTS == {"name": "Hand"}


def test_trace_tail_reads_last_rows(tmp_path):
    trace = Trace(tmp_path / "trace.jsonl")
    for step in range(1000):
        trace.write({"step": step})

    assert trace.tail(2) == [{"step": 998}, {"step": 999}]

import re
from pathlib import Path


def test_agent_imports_no_loop_package():
    forbidden = re.compile(r"^\s*(from|import)\s+loop\b", re.MULTILINE)
    for path in Path("agent").glob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path


def test_state_access_pattern_covers_contract_rules():
    forbidden = re.compile(r"\.ram\(|\.memory\[|load_state|save_state|\b(?:\w*env\w*|environment)\.(?:save|load)\(")
    for snippet in ("e.ram(1)", "ctx.env.memory[1]", "backend.load_state(file)", "env.save_state(file)", "ctx.env.save('state')", "environment.load('state')"):
        assert forbidden.search(snippet)
    assert not forbidden.search("cache.save('response')")


def test_pyboy_state_and_ram_access_stays_in_adapter():
    forbidden = re.compile(r"\.ram\(|\.memory\[|load_state|save_state|\b(?:\w*env\w*|environment)\.(?:save|load)\(")
    for path in Path("agent").rglob("*.py"):
        if path != Path("agent/env_pyboy.py"):
            assert not forbidden.search(path.read_text(encoding="utf-8")), path

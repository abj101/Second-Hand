import pytest


@pytest.fixture
def stub_runner(monkeypatch):
    """scripts.run_agent with the ROM lookup stubbed; pass env (a factory) / model / read to inject fakes. The budget ledger is stubbed unless ledger=True."""
    from scripts import run_agent
    def stub(env=None, model=None, *, ledger=False, read=None):
        monkeypatch.setattr(run_agent, "find_rom", lambda: "rom")
        if env is not None: monkeypatch.setattr(run_agent, "PyBoyEnv", lambda *a, **k: env())
        if model is not None: monkeypatch.setattr(run_agent, "Ollama", lambda *a, **k: model)
        if not ledger: monkeypatch.setattr(run_agent, "_ledger", lambda *a: None)
        if read is not None: monkeypatch.setattr(run_agent, "read", read)
        return run_agent
    return stub

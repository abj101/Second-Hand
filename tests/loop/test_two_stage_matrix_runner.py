import importlib.util
from pathlib import Path


def _runner_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_two_stage_matrix.py"
    spec = importlib.util.spec_from_file_location("two_stage_matrix_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_defaults_use_cloud_gemma_and_qwen9b() -> None:
    runner = _runner_module()
    assert runner.DEFAULT_VISION == [runner.DEFAULT_CLOUD_MODEL]
    assert runner.DEFAULT_REASONERS == ["qwen3.5:9b-q4_K_M"]
    assert all("qwen3-vl" not in model for model in runner.DEFAULT_VISION)

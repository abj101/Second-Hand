from __future__ import annotations

from pathlib import Path

from avatar.config import project_root

SEE_HEADERS = [
    "run_id",
    "model",
    "fixture_state",
    "longest_side",
    "jpeg_quality",
    "bytes",
    "tiles_correct",  # 0-16
    "score_correct",  # 0/1
    "status_correct",  # 0/1
    "still_readable",  # human 0/1
    "notes",
]

UNDERSTAND_HEADERS = [
    "run_id",
    "model",
    "screen_kind_correct",
    "goal_sane",
    "next_move_legal",
    "notes",
]

ACT_HEADERS = [
    "run_id",
    "model",
    "harness_level",
    "task",
    "attempt",
    "success",
    "stuck",
    "notes",
]

QA_HEADERS = [
    "run_id",
    "finding_title",
    "would_file",
    "human_agree",
    "notes",
]


def scoring_sheet(stage: str) -> str:
    sheets = {
        "see": ",".join(SEE_HEADERS),
        "understand": ",".join(UNDERSTAND_HEADERS),
        "act": ",".join(ACT_HEADERS),
        "qa": ",".join(QA_HEADERS),
    }
    return sheets[stage] + "\n"


def write_blank_sheet(run_dir: Path, stage: str) -> Path:
    path = run_dir / f"human_score_{stage}.csv"
    path.write_text(scoring_sheet(stage), encoding="utf-8")
    return path


def rubric_dir() -> Path:
    return project_root() / "eval" / "rubrics"

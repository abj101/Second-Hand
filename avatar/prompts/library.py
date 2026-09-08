from __future__ import annotations

from pathlib import Path

from avatar.config import project_root
from avatar.harness.tables import prompt_listing

SYSTEM_CORE = """You are a vision-language game QA agent. You see only pixels.
You never receive DOM, emulator RAM, or game APIs.
Reply with a single JSON object. No markdown fences. No commentary outside JSON.
Do not invent actions named wait or observe.
"""

SEE_SCHEMA = """JSON schema:
{
  "screen_kind": "ui" | "gameplay",
  "board": [[number or null, ...], ...],
  "score": number or null,
  "status": string,
  "reasoning": string,
  "presence": string,
  "camera": string,
  "scene": string
}
For 2048, board is exactly 4 rows of 4 cells, top-to-bottom, left-to-right.
board[0] is the TOP row. board[3] is the BOTTOM row. board[0][0] is TOP-LEFT.
Empty cells are null. Do not skip empty rows. Read every cell from the pixels.
score is the number in the Score box above the grid (not Best).
status is "gameover" if a Game over overlay is visible, otherwise a short gameplay label.
For 2048, the New Game button is always on screen — that is not UI and not game over.
If any 2048 board cell is empty, status must not be gameover.
presence is a closed piece/enemy line (or "none"). camera is framing. scene is one line.
Extra fields may be empty strings.
"""

UNDERSTAND_SCHEMA = """JSON schema:
{
  "screen_kind": "ui" | "gameplay",
  "goal": string,
  "success_criteria": string,
  "screen_position": string or "N/A",
  "next_move": string,
  "reasoning": string
}
Classify UI Screen vs Gameplay Screen first.
Pixels-only: you were not given a manual. Infer the goal from what you see.
"""

ACT_SCHEMA = """JSON schema:
{
  "goal": string,
  "success_criteria": string,
  "screen_position": string or "N/A",
  "actions": [{"name": string, "details": string, "duration_ms": number or null}],
  "invoke_skill": string or null,
  "invoke_gsm": string or null,
  "reasoning": string
}
Stages 5–6: fill goal, success_criteria, and screen_position, then named actions.
Pick action names only from the named table. At most 5 actions.
Fill duration_ms from the Procedural RAG block when it lists a duration for that name; otherwise null.
Never invent raw key names or scan codes; only names from the table.
If a listed skill matches, set invoke_skill to its name and use an empty actions list.
If a listed GSM matches a reactive gameplay task, set invoke_gsm to its name and use an empty actions list.
If screen_kind is ui or the game has not started (idle/menu/game over), do not invoke_gsm.
Use named table actions instead (start, restart). invoke_gsm is only for an active piece or a live 2048 board.
invoke_gsm must be a GSM name from the list below — never a named-table action (start, shift_left, etc.).
invoke_gsm XOR actions: do not set invoke_gsm together with invoke_skill or a non-empty actions list.
"""

PREVIOUS_TASK_SCHEMA = """JSON schema:
{
  "verdict": "success" | "failure" | "partial",
  "evidence": string,
  "optimizations": string
}
Use the screenshot as evidence of the last action's outcome. verdict is a top-level string.
"""

HISTORICAL_SUMMARY_SCHEMA = """JSON schema:
{
  "narrative": string
}
Write a short narrative of the last 10 tasks (successes, failures, bans, repeats).
"""

ACTION_DESIGN_SCHEMA = """JSON schema:
{
  "timing": string,
  "sequencing": string,
  "gsm_critique": string,
  "proposed_labels": [string]
}
Critique duration_ms / hold timing and action order.
If a GSM ran last turn, critique label-list misses and propose detector labels.
"""

REFLECT_SCHEMA = """JSON schema:
{
  "visual": string,
  "verdict": "success" | "failure" | "partial",
  "task_design": string,
  "action_plan": string,
  "execution": string,
  "qa_notes": [{"title": string, "would_file": boolean, "evidence": string}],
  "next_step": string
}
Analyze failure at three levels: task design, action plan, execution (including timing/duration).
QA notes: would a human tester file this?
verdict must be a top-level string, not an object.
"""


def _manual_block(prompt_mode: str, manual: str) -> str:
    if prompt_mode != "manual" or not manual.strip():
        return "Prompt mode: pixels-only. No manual or how-to text is provided."
    return "Prompt mode: manual boost. Official/how-to text follows:\n" + manual.strip()


def see_system(prompt_mode: str = "pixels", manual: str = "") -> str:
    return "\n".join([SYSTEM_CORE, SEE_SCHEMA, _manual_block(prompt_mode, manual)])


def understand_system(prompt_mode: str = "pixels", manual: str = "") -> str:
    return "\n".join([SYSTEM_CORE, UNDERSTAND_SCHEMA, _manual_block(prompt_mode, manual)])


def act_system(
    table_name: str,
    *,
    memory_block: str = "",
    skills_block: str = "",
    gsm_block: str = "",
    prompt_mode: str = "pixels",
    manual: str = "",
    procedural_block: str = "",
) -> str:
    parts = [
        SYSTEM_CORE,
        ACT_SCHEMA,
        "Named action table:",
        prompt_listing(table_name),
        _manual_block(prompt_mode, manual),
    ]
    if memory_block:
        parts += ["", memory_block]
    if skills_block:
        parts += ["", "Available skills (invoke by name):", skills_block]
    if procedural_block:
        parts += ["", "Procedural RAG (use for duration_ms / construction):", procedural_block]
    if gsm_block:
        parts += ["", gsm_block]
    else:
        parts += ["", "No GSMs are registered for this fixture. invoke_gsm must be null."]
    return "\n".join(parts)


def reflect_system(prompt_mode: str = "pixels", manual: str = "") -> str:
    return "\n".join([SYSTEM_CORE, REFLECT_SCHEMA, _manual_block(prompt_mode, manual)])


def see_user(fixture: str, state_name: str = "") -> str:
    # state_name is for callers/logs only. Putting "start"/"mid" in the prompt
    # leaks priors (4b put a 2 in the corner instead of reading the grid).
    _ = state_name
    return (
        f"Fixture: {fixture}. Read the Score box and every board cell from the pixels. "
        "Classify UI vs gameplay."
    )


def scout_user(fixture: str) -> str:
    return (
        f"Fixture: {fixture}. Classify UI Screen vs Gameplay Screen from this full-window screenshot. "
        "Do not invent a crop. screen_kind is the only field that matters."
    )


def understand_user(fixture: str) -> str:
    return (
        f"Fixture: {fixture}. Classify UI vs gameplay. "
        "What is the goal, and what is a sensible next move? Pixels only."
    )


BRAIN_CORE = """You are a text-only game planner. You cannot see pixels.
You receive a structured See JSON from the vision model. Trust it.
Reply with a single JSON object. No markdown fences. No commentary outside JSON.
Do not invent actions named wait or observe.
Never pick a banned action. If the last action was a noop, pick a different name from the table.
"""


def brain_system(
    table_name: str,
    *,
    memory_block: str = "",
    skills_block: str = "",
    gsm_block: str = "",
    prompt_mode: str = "pixels",
    manual: str = "",
    procedural_block: str = "",
) -> str:
    parts = [
        BRAIN_CORE,
        ACT_SCHEMA,
        "Named action table:",
        prompt_listing(table_name),
        _manual_block(prompt_mode, manual),
    ]
    if memory_block:
        parts += ["", memory_block]
    if skills_block:
        parts += ["", "Available skills (invoke by name):", skills_block]
    if procedural_block:
        parts += ["", "Procedural RAG (use for duration_ms / construction):", procedural_block]
    if gsm_block:
        parts += ["", gsm_block]
    else:
        parts += ["", "No GSMs are registered for this fixture. invoke_gsm must be null."]
    return "\n".join(parts)


def act_user(fixture: str, task: str) -> str:
    return f"Fixture: {fixture}. Task: {task}. Plan up to 5 named actions."


def brain_user(fixture: str, task: str, observation: dict | None = None) -> str:
    import json

    payload = json.dumps(observation or {}, default=str)
    return (
        f"Fixture: {fixture}. Task: {task}.\n"
        f"See JSON (from the vision model):\n{payload}\n"
        "Plan up to 5 named actions. Do not use banned names."
    )


def previous_task_system() -> str:
    return "\n".join([SYSTEM_CORE, PREVIOUS_TASK_SCHEMA])


def previous_task_user(
    fixture: str,
    task: str = "",
    executed: list[str] | None = None,
    outcome: object | None = None,
) -> str:
    acts = ", ".join(executed or []) or "(none)"
    text = (
        f"Fixture: {fixture}. Task: {task}. Executed: {acts}. "
        "Look at the screenshot. Did the last task succeed, fail, or partially succeed? "
        "Reply with PreviousTaskReflection JSON. Use pixels as evidence."
    )
    if outcome is not None:
        verdict = getattr(outcome, "verdict", "")
        evidence = getattr(outcome, "evidence", "")
        text += (
            f" Harness already scored this turn as {verdict}"
            f" (pixel_changed={getattr(outcome, 'pixel_changed', None)}). {evidence}"
            " Match that verdict unless the pixels clearly contradict it."
        )
    return text


def historical_summary_system() -> str:
    return "\n".join([SYSTEM_CORE, HISTORICAL_SUMMARY_SCHEMA])


def historical_summary_user(last_ten_text: str) -> str:
    body = last_ten_text.strip() if last_ten_text else "No episodic history yet."
    return "Write a short HistoricalSummary JSON narrative of these last-10 tasks:\n" + body


def action_design_system() -> str:
    return "\n".join([SYSTEM_CORE, ACTION_DESIGN_SCHEMA])


def action_design_user(
    fixture: str,
    *,
    gsm_last: bool = False,
    labels: list[str] | None = None,
    executed: list[str] | None = None,
) -> str:
    acts = ", ".join(executed or []) or "(none)"
    text = (
        f"Fixture: {fixture}. Executed: {acts}. "
        "Critique timing/duration_ms and action sequencing. Reply with ActionDesignReflection JSON."
    )
    if gsm_last:
        text += (
            f" GSM ran last turn. Critique label-list misses (current labels: {labels or []}) "
            "and set proposed_labels."
        )
    return text


def reflect_user(fixture: str, trace_summary: str) -> str:
    return f"Fixture: {fixture}. Session trace:\n{trace_summary}"


def load_manual(fixture: str) -> str:
    path = project_root() / "avatar" / "prompts" / "manuals" / f"{fixture}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

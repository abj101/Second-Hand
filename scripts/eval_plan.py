"""Offline eval of the goal planner, notebook and act on recorded Pokemon Red situations (P1/P2 in runs/m1_ps_fp, P3 in runs/m1_gp)."""
import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from agent.act import act
from agent.loop import Loop
from agent.brief import _load, defaults
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama
from agent.notebook import Notebook
from agent.plan import plan
from agent.world import World
from scripts.eval_frames import BUTTONS, GAME, _guarded_post
from scripts.run_agent import CallCap, _ledger

RUN_NAME = "m1_gp_eval"
CASES = {"P1": ("runs/m1_ps_fp", 80, 82), "P2": ("runs/m1_ps_fp", 80, 82), "P3": ("runs/m1_gp", 75, 88), "P4": ("runs/m1_gp2", 125, 127)}  # run, checkpoint step, situation step
OUT = ROOT / ".tmp/gp/eval_plan.json"
P_OK, P_BAD = re.compile(r"door|rug|mat|exit|outside|leave|way out|front"), re.compile(r"stair|upstairs|2f|bed")
A_OK, A_BAD = re.compile(r"door|rug|mat|exit|outside|bottom|south"), re.compile(r"stair|mom|mother|woman|lady")
TRIGGER_P1 = "new game text was seen (see the log)"
TRIGGER_P2 = "focus use_stairs blocked: the stairs lead up to the bedroom on 2F where I started, not outside"
TRIGGER_P3 = "no focus goal yet"
GOALS = [("top", {"text": "Finish the game", "status": "active", "parent": None}),
         ("find_oak", {"text": "Find Prof. Oak", "parent": "top", "source": "heard",
                       "evidence": "PROF. OAK, next door, is looking for you.", "status": "active"}),
         ("leave_house", {"text": "Leave the house", "parent": "find_oak", "done_when": "the screen shows outdoors", "status": "active"}),
         ("use_stairs", {"text": "Leave via the stairs", "parent": "leave_house", "status": "active"})]
DRY = {"plan": {"goals": [{"op": "set", "key": "door", "value": {"text": "Leave by the door at the bottom of 1F", "parent": None, "done_when": "outdoors", "status": "active"}}],
                "focus": "door", "lesson": None, "why": "dry"},
       "act": {"place": "x", "notebook": [], "action": {"go_to": "door"}, "text": None, "expect": "place_changes", "why": "dry"}}


class DryModel:
    def ask(self, system, user, images=(), schema=None, *, role=""):
        return SimpleNamespace(data=DRY[role], text="", ms=0, prompt_tokens=0, output_tokens=0)


def _setup(tmp, case):
    run, ck, _ = CASES[case]
    checkpoint = ROOT / run / "checkpoint" / f"{ck:06d}"; trace_path = ROOT / run / "trace.jsonl"
    state = json.loads((checkpoint / "harness.json").read_text(encoding="utf-8"))
    shutil.copytree(checkpoint / "memory", tmp / "memory")
    mem = Memory(tmp / "memory")
    world = World(mem, 16); world.restore(state); world.current_place = state["place"]
    if case == "P4":  # the live notebook at the bounce, copied as-is (goals, focus, log); trigger is Loop._moved's text
        nb_dir = tmp / "nb"; nb_dir.mkdir()
        for name in ("notebook.json", "notebook.history"): shutil.copy(checkpoint / "memory" / name, nb_dir / name)
        return world, Notebook(nb_dir), f"cycle: went back and forth between {world.place_label('p_world_2')} and {world.place_label('p_world')} 4 times in 8 steps without progress"
    if case == "P3":  # the live log at the stall; goals/lessons written under the old planner prompt are cleared
        nb_dir = tmp / "nb"; nb_dir.mkdir()
        for name in ("notebook.json", "notebook.history"): shutil.copy(checkpoint / "memory" / name, nb_dir / name)
        nb = Notebook(nb_dir); nb.data = {s: {} for s in nb.data}; nb.focus = None
        return world, nb, TRIGGER_P3
    nb = Notebook(tmp / "nb")
    # the log the live loop would have written by step 80: game text and moves between places (Loop._learn / Loop._moved)
    events = [(r["step"], f'text: "{r["text"]}"') for r in mem.rows("knowledge") if r.get("src") == "game_text" and r["step"] <= ck]
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    moved = lambda a, b, via: f"went from {world.place_label(a)} to {world.place_label(b)}" + (f" via {via}" if via else "")
    for prev, row in zip(rows, rows[1:]):
        if row["step"] > ck: break
        if prev.get("next_place") and row["place"] != prev["next_place"]: events.append((row["step"], moved(prev["next_place"], row["place"], "a step taken to locate yourself")))
        if row.get("next_place") and row["next_place"] != row["place"]: events.append((row["step"], moved(row["place"], row["next_place"], (row.get("choice") or {}).get("target"))))
    for step, text in sorted(events, key=lambda e: e[0]): nb.note(step, text)
    if case == "P1":
        return world, nb, TRIGGER_P1
    nb.note(79, 'talked to Mom; she said: "All boys leave home some day." and "PROF. OAK, next door, is looking for you."')
    nb.apply([{"op": "set", "section": "goals", "key": k, "value": v} for k, v in GOALS], 80)
    nb.set_focus("use_stairs", 80)
    nb.apply([{"op": "focus", "status": "blocked", "evidence": "the stairs lead up to the bedroom on 2F where I started, not outside"}], 81, structure=False)
    return world, nb, TRIGGER_P2


def _p_pass(focus, goal, *_):
    text = f'{goal.get("text", "")} {goal.get("done_when", "")}'.casefold()
    return bool(focus) and bool(P_OK.search(text)) and not P_BAD.search(text)


P3_OK, P3_BAD = re.compile(r"explor|exit|stair|door|unseen|new|leave|room|area", re.I), re.compile(r"\b(?i:press|button|up|down|left|right|start|select)\b|\bA\b|\bB\b")


def _p3_pass(focus, goal, *_):  # case-sensitive A/B so the article "a" is not read as a button
    text = f'{goal.get("text", "")} {goal.get("done_when", "")}'
    return bool(focus) and bool(P3_OK.search(text)) and not P3_BAD.search(text)


def _target(action, entities):
    if not action:
        return ""
    if "go_to" in action:
        return action["go_to"]
    if "thing" in action:
        return entities[action["thing"]].get("name", "")
    return ""


def _a_pass(action, target, why):
    buttons = (action or {}).get("buttons") or []
    down = buttons.count("DOWN") * 2 > len(buttons) if buttons else False
    text_ok = bool(A_OK.search(f"{target} {why}".casefold())) and not A_BAD.search(target.casefold())
    return down or text_ok


def _a3_pass(action, target, why):  # why is unused: P3 scores the action only
    action = action or {}
    go, buttons = action.get("go_to", ""), action.get("buttons") or []
    return go == "unexplored" or bool(re.search(r"stair|exit|door", f"{go} {target}", re.I)) or any(b in ("UP", "RIGHT") for b in buttons)


P4_BAD, A4_BAD = re.compile(r"\b(enter|go into|back into)\b.*\b(house|home|building|room)\b|door ?mat|room edge|reach_room_edge"), re.compile(r"door|mat|house|entrance|home", re.I)


def _p4_pass(focus, goal, old, nb):  # after the bounce: a new focus, or the old one closed
    text = f'{goal.get("text", "")} {goal.get("done_when", "")}'.casefold()
    old_status = nb.data["goals"].get(old, {}).get("status")
    return bool(focus) and not P4_BAD.search(text) and (focus != old or old_status in ("blocked", "dropped"))


def _a4_pass(action, target, why):  # the house door is directly above the player: no walking back into it
    action = action or {}
    buttons = action.get("buttons") or []
    if ("go_to" in action or action.get("verb") == "approach") and A4_BAD.search(target): return False
    return not (buttons and buttons[0] == "UP")


PASSES = {"P1": (_p_pass, _a_pass), "P2": (_p_pass, _a_pass), "P3": (_p3_pass, _a3_pass), "P4": (_p4_pass, _a4_pass)}


def _frame_per(case):
    run, _, step = CASES[case]
    frame = Image.open(ROOT / run / f"frames/{step:06d}.png").convert("RGB")
    trace = next(r for r in (json.loads(line) for line in (ROOT / run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()) if r["step"] == step)
    per = {"screen": trace["screen"], "text": [], "entities": [{"name": e["name"], "kind": e["kind"], "at": e["at"]} for e in trace["entities"] if e.get("name")]}
    return frame, per


def _run_try(case, n, model, frame, per, results):
    controls = "\n".join(_load(GAME).get("controls", []))
    p_pass_fn, a_pass_fn = PASSES[case]
    with tempfile.TemporaryDirectory(prefix=f"{case}_", dir=ROOT / ".tmp/gp") as tmp:
        world, nb, trigger = _setup(Path(tmp), case)
        # mechanical frontier pick, no model call: Loop._unexplored only reads self.mem, self.pos and self.tile_px, all of which World carries
        route = Loop._unexplored(world, world.current_place) if case == "P3" else None
        goal_text = " ".join(nb.data["goals"][k].get("text", "") for k in nb.path())
        view = world.view(goal_text, world.current_place)
        old_focus = nb.focus
        p = plan(model, trigger, nb, view, [], [])
        nb.apply(p.ops, 81); nb.set_focus(p.focus, 81)
        goal = nb.data["goals"].get(nb.focus, {}) if nb.focus else {}
        row = {"case": case, "try": n, "cached": n == 1, "focus": nb.focus, "focus_text": goal.get("text", ""),
               "done_when": goal.get("done_when", ""), "p_pass": p_pass_fn(nb.focus, goal, old_focus, nb),
               "goal_tree": nb.render(), "plan_why": p.why, "plan_ops": p.ops}
        if case == "P3": row["unexplored_route"] = route
        results.append(row)
        a = act(model, frame, per, nb.render_focus(), view, [], controls, defaults(GAME), BUTTONS, places=[])
        target = _target(a.action, per["entities"])
        row.update(act_action=a.action, act_target=target, act_why=a.why, act_ops=a.ops, a_pass=a_pass_fn(a.action, target, a.why))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--cap", type=int, default=16, help="max real model requests, charged to the m1 ledger")
    ap.add_argument("--dry", action="store_true", help="stub model: no network, no ledger")
    ap.add_argument("--cases", default="P1,P2,P3,P4", help="comma-separated subset of P1,P2,P3,P4")
    a = ap.parse_args(argv)
    cases = [c.strip() for c in a.cases.split(",") if c.strip()]
    if not set(cases) <= set(CASES): ap.error(f"unknown case in --cases: {a.cases}")
    OUT.parent.mkdir(parents=True, exist_ok=True); (ROOT / ".tmp/gp").mkdir(parents=True, exist_ok=True)
    attempts, results, status, error = [0], [], "ok", None
    try:
        for case in cases:
            frame, per = _frame_per(case)
            for n in range(1, a.tries + 1):
                if a.dry:
                    model = DryModel()
                else:
                    model = Ollama("gemma4:31b-cloud", cache_dir=ROOT / ".cache/model" if n == 1 else None)
                    model._post = _guarded_post(model._post, attempts, charge=lambda _run, stage, cap: _ledger(RUN_NAME, stage, cap), cap=a.cap)
                _run_try(case, n, model, frame, per, results)
    except CallCap as exc:
        status, error = "cap", str(exc)
    except ModelUnavailable as exc:
        status, error = "unavailable", str(exc)
    for r in results:
        print(f"\n## {r['case']} try {r['try']} (cached={r['cached']})\n{r.get('goal_tree', '')}\nplan why: {r.get('plan_why', '')}\nact why: {r.get('act_why', '')}")
    print("\n| case | try | focus | focus text | done_when | P pass | act action | act target | A pass |\n|---|---|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['case']} | {r['try']} | {r['focus']} | {r['focus_text']} | {r['done_when']} | {r['p_pass']} | "
              f"{json.dumps(r.get('act_action'))} | {r.get('act_target', '')} | {r.get('a_pass', '')} |")
    OUT.write_text(json.dumps({"status": status, "error": error, "requests": attempts[0], "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nstatus={status} requests={attempts[0]} wrote {OUT}")
    return {"ok": 0, "cap": 3, "unavailable": 4}[status]


if __name__ == "__main__":
    raise SystemExit(main())

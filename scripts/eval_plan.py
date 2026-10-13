"""Offline eval of the goal planner, notebook and act on one recorded Pokemon Red situation (runs/m1_ps_fp, step 81)."""
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
from agent.brief import _load, defaults
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama
from agent.notebook import Notebook
from agent.plan import plan
from agent.world import World
from scripts.eval_frames import BUTTONS, GAME, _guarded_post
from scripts.run_agent import CallCap, _ledger

RUN_NAME = "m1_gp_eval"
RUN = ROOT / "runs/m1_ps_fp"
CHECKPOINT = RUN / "checkpoint/000080"
OUT = ROOT / ".tmp/gp/eval_plan.json"
P_OK, P_BAD = re.compile(r"door|rug|mat|exit|outside|leave|way out|front"), re.compile(r"stair|upstairs|2f|bed")
A_OK, A_BAD = re.compile(r"door|rug|mat|exit|outside|bottom|south"), re.compile(r"stair|mom|mother|woman|lady")
TRIGGER_P1 = "new game text was seen (see the log)"
TRIGGER_P2 = "focus use_stairs blocked: the stairs lead up to the bedroom on 2F where I started, not outside"
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
    state = json.loads((CHECKPOINT / "harness.json").read_text(encoding="utf-8"))
    shutil.copytree(CHECKPOINT / "memory", tmp / "memory")
    mem = Memory(tmp / "memory")
    world = World(mem, 16); world.restore(state); world.current_place = state["place"]
    nb = Notebook(tmp / "nb")
    # the log the live loop would have written by step 80: game text and moves between places (Loop._learn / Loop._moved)
    events = [(r["step"], f'text: "{r["text"]}"') for r in mem.rows("knowledge") if r.get("src") == "game_text" and r["step"] <= 80]
    rows = [json.loads(line) for line in (RUN / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    moved = lambda a, b, via: f"went from {world.place_label(a)} to {world.place_label(b)}" + (f" via {via}" if via else "")
    for prev, row in zip(rows, rows[1:]):
        if row["step"] > 80: break
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


def _p_pass(focus, goal):
    text = f'{goal.get("text", "")} {goal.get("done_when", "")}'.casefold()
    return bool(focus) and bool(P_OK.search(text)) and not P_BAD.search(text)


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


def _run_try(case, n, model, frame, per, results):
    controls = "\n".join(_load(GAME).get("controls", []))
    with tempfile.TemporaryDirectory(prefix=f"{case}_", dir=ROOT / ".tmp/gp") as tmp:
        world, nb, trigger = _setup(Path(tmp), case)
        goal_text = " ".join(nb.data["goals"][k].get("text", "") for k in nb.path())
        view = world.view(goal_text, world.current_place)
        p = plan(model, trigger, nb, view, [], [])
        nb.apply(p.ops, 81); nb.set_focus(p.focus, 81)
        goal = nb.data["goals"].get(nb.focus, {}) if nb.focus else {}
        row = {"case": case, "try": n, "cached": n == 1, "focus": nb.focus, "focus_text": goal.get("text", ""),
               "done_when": goal.get("done_when", ""), "p_pass": _p_pass(nb.focus, goal),
               "goal_tree": nb.render(), "plan_why": p.why, "plan_ops": p.ops}
        results.append(row)
        a = act(model, frame, per, nb.render_focus(), view, [], controls, defaults(GAME), BUTTONS, places=[])
        target = _target(a.action, per["entities"])
        row.update(act_action=a.action, act_target=target, act_why=a.why, act_ops=a.ops, a_pass=_a_pass(a.action, target, a.why))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--cap", type=int, default=16, help="max real model requests, charged to the m1 ledger")
    ap.add_argument("--dry", action="store_true", help="stub model: no network, no ledger")
    a = ap.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True); (ROOT / ".tmp/gp").mkdir(parents=True, exist_ok=True)
    frame = Image.open(RUN / "frames/000082.png").convert("RGB")
    trace = next(r for r in (json.loads(line) for line in (RUN / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()) if r["step"] == 82)
    per = {"screen": trace["screen"], "text": [], "entities": [{"name": e["name"], "kind": e["kind"], "at": e["at"]} for e in trace["entities"] if e.get("name")]}
    attempts, results, status, error = [0], [], "ok", None
    try:
        for case in ("P1", "P2"):
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

"""Run the normal perception and act prompts against labelled saved frames."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from agent.act import act
from agent.brief import _load, defaults
from agent.loop import Loop
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama
from agent.observer import for_model
from scripts.run_agent import CallCap, _ledger

GAME = "pokemon_red"
BUTTONS = ["UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT"]
RUN_NAME = "m1_ps_s0"
ATTEMPT_CAP = 26
PROBE_STEPS = {("m1_long", 71), ("m1_long", 72)}


class DryModel:
    calls = 0

    def ask(self, system, user, images=(), schema=None, *, role=""):
        if role == "perceiver":
            data = {"screen": self.screen, "text": [], "entities": self.entities,
                    "affordances": [{"verb": self.verb, "target": "dry target",
                                     "at": self.point, "why": "offline scoring check"}]}
        else:
            data = {"place": "dry", "notebook": [], "action": {"i": 0},
                    "expect": "screen_changes", "why": "offline scoring check"}
        return SimpleNamespace(data=data, text="", ms=0, prompt_tokens=0, output_tokens=0)


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ledger_snapshot():
    path = ROOT / "runs/e3_budget.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"total": 0, "m1": 0}
    return {key: data.get(key, 0) for key in ("total", "m1")}


def _labels(per):
    return [f'{e.get("name", "")}@{e["at"][0]:.3f},{e["at"][1]:.3f}'
            for e in per.get("entities", []) if str(e.get("kind", "")).casefold() == "player"]


def _point_for(action, per):
    """Resolve legacy options and model-owned S2 action shapes for one scorer."""
    if not isinstance(action, dict):
        return "invalid", None, None
    affs, entities = per.get("affordances", []), per.get("entities", [])
    if type(action.get("i")) is int and 0 <= action["i"] < len(affs):
        row = affs[action["i"]]
        return f'{row.get("verb", "")} {row.get("target", "")}'.strip(), row.get("at"), row.get("verb")
    if isinstance(action.get("buttons"), list):
        names = [str(value).upper() for value in action["buttons"]]
        return "buttons " + " ".join(names), None, "buttons"
    raw_verb = action.get("verb")
    if isinstance(action.get("go_to"), str):
        name, verb, physical_verb = action["go_to"], raw_verb or "approach", "approach"
    elif "thing" in action:
        thing = action["thing"]
        verb = raw_verb or "approach"
        if type(thing) is int and 0 <= thing < len(entities):
            entity = entities[thing]
            return f"{verb} thing {thing}", entity.get("at"), physical_verb_for(verb)
        name, physical_verb = thing, physical_verb_for(verb)
        if isinstance(name, dict):
            name = name.get("name", name.get("target"))
    elif "target" in action:
        name, verb, physical_verb = action["target"], raw_verb or "approach", physical_verb_for(raw_verb or "approach")
        if raw_verb == "press" and str(name).upper() in {"UP", "DOWN", "LEFT", "RIGHT"}:
            return f"press {str(name).upper()}", None, "approach"
    elif raw_verb in ("advance", "cancel", "type", "select", "press", "interact", "click"):
        target = str(action.get("target", ""))
        if raw_verb == "press" and target.upper() in BUTTONS:
            return (f"press {target.upper()}", None,
                    "approach" if target.upper() in {"UP", "DOWN", "LEFT", "RIGHT"} else "press")
        return (f"{raw_verb} {target}".strip(), None, raw_verb)
    else:
        return "invalid", None, None
    if not isinstance(name, str):
        return "invalid", None, None
    folded = name.casefold()
    row = next((a for a in affs if str(a.get("target", "")).casefold() == folded and
                (not raw_verb or a.get("verb") == raw_verb)), None)
    entity = next((e for e in entities if str(e.get("name", "")).casefold() == folded), None)
    target = row or entity
    return f"{physical_verb} {name}".strip(), target.get("at") if target else None, physical_verb


def physical_verb_for(verb):
    return "approach" if verb == "go_to" else verb


def _near(point, acceptable):
    return point is not None and any(max(abs(point[i] - p[i]) for i in (0, 1)) <= 0.1 for p in acceptable)


STEP = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def _buttons_toward(label, self_point, acceptable):
    """True when the first direction moves toward an acceptable point and no direction points away from it."""
    dirs = [STEP[w.upper()] for w in label.split()[1:] if w.upper() in STEP]
    if self_point is None or not acceptable or not dirs:
        return False
    for x, y in acceptable:
        d = (x - self_point[0], y - self_point[1])
        along = [sx * d[0] + sy * d[1] for sx, sy in dirs]
        if along[0] > 0.02 and all(a > -0.02 for a in along):
            return True
    return False


def _ok(verb, label, point, entry, screen):
    if screen == "world":
        if point is not None and entry["self"] is not None and max(
                abs(point[i] - entry["self"][i]) for i in (0, 1)) <= 0.08:
            return False
        if verb == "buttons":
            if not any(v in entry["ok_verbs"] for v in ("approach", "press", "buttons")):
                return False
            return _buttons_toward(label, entry["self"], entry["ok_near"])
        if verb not in entry["ok_verbs"]:
            return False
        if point is not None and _near(point, entry["ok_near"]):
            return True
        if label.startswith("press ") and verb == "approach":
            return _buttons_toward(label, entry["self"], entry["ok_near"])
        return False
    if verb == "buttons":
        names = set(label.upper().split()[1:])
        return ("press" in entry["ok_verbs"] and bool(names & {"A", "B", "START", "SELECT"})) or (
            "advance" in entry["ok_verbs"] and bool(names & {"A", "START"})) or (
            "select" in entry["ok_verbs"] and bool(names & {"A", "START"}))
    return verb in entry["ok_verbs"]


def _guarded_post(post, attempts, charge=_ledger, cap=ATTEMPT_CAP):
    def guarded(payload):
        if attempts[0] >= cap:
            raise CallCap(f"S0 attempt cap ({cap})")
        charge(RUN_NAME, None, None)
        attempts[0] += 1
        return post(payload)
    return guarded


def _screen_from_trace(trace, entry):
    screen = str(trace.get("screen", "other")).casefold()
    if "keyboard" in str(entry["why"]).casefold() or "naming keyboard" in screen:
        return "keyboard"
    if screen in ("dialogue", "dialogue_screen"):
        return "dialogue"
    if screen in ("menu", "title", "world", "battle", "keyboard", "other"):
        return screen
    return "other"


def evaluate(entry, trace_by_step, truth_by_step, model, attempts):
    run_root, step = ROOT / "runs" / entry["run"], entry["step"]
    frame_path = run_root / "frames" / f"{step:06d}.png"
    frame = Image.open(frame_path).convert("RGB")
    trace, truth = trace_by_step[step], truth_by_step[step]
    previous = trace_by_step.get(step - 1)
    with tempfile.TemporaryDirectory(prefix="s0_frame_", dir=ROOT / ".tmp/ps") as isolated:
        mem = Memory(Path(isolated) / "memory")
        loop = Loop(SimpleNamespace(tile_px=16), model, mem, GAME)
        loop.tile_px = loop.world.tile_px = 16
        loop.prev = {"screen": previous.get("screen")} if previous else None
        # Every world frame is mark-eligible: the eval measures prompts with the marker; S1 unit tests own eligibility.
        mark_eligible = trace.get("screen") == "world"
        loop.prev_frame_mark_eligible = mark_eligible
        if previous and previous.get("self_locked") and previous.get("self_spot"):
            loop.world.self_spot, loop.world._self_from_motion = previous["self_spot"], True
            truth_self = entry["self"]
            truth_injected = False
        else:
            # Harness-only: frames recorded before a motion lock (runs/m1, early m1_long) use the labelled spot.
            loop.world.self_spot = list(entry["self"]) if entry["self"] else None
            loop.world._self_from_motion = entry["self"] is not None
            truth_self, truth_injected = entry["self"], entry["self"] is not None
        seeded_self, seeded_from_motion = loop.world.self_spot, loop.world._self_from_motion
        if isinstance(model, DryModel):
            model.screen = _screen_from_trace(trace, entry)
            model.verb = entry["ok_verbs"][0]
            point = entry["ok_near"][0] if entry["ok_near"] else [0.5, 0.5]
            model.point = [int(value * 1000) for value in point]
            model.entities = ([{"kind": "player", "name": "player", "at": [int(value * 1000) for value in entry["self"]]}]
                              if entry["self"] else [])
        per, _, _, _ = loop._see(frame, previous.get("place") if previous else None, force=True,
                                  screen_was_world=mark_eligible)
        before_fix = _labels(per)
        player_fix = loop.world.fix_player(per, frame.size)
        controls = "\n".join(_load(GAME).get("controls", []))
        flags = ["the sprite inside the red box is you"] if loop.seen_marked else []
        choice = act(model, for_model(loop.seen_image), per, loop.notebook.render(), "none", flags,
                     controls, defaults(GAME), BUTTONS)
        label, point, verb = _point_for(choice.action, per)
        self_target = bool(point is not None and truth_self is not None and
                           max(abs(point[i] - truth_self[i]) for i in (0, 1)) <= 0.08)
        if self_target:
            ok = False
        else:
            ok = _ok(verb, label, point, entry, per.get("screen"))
        result = {"run": entry["run"], "step": step, "screen": per.get("screen"),
                  "truth": {key: truth.get(key) for key in ("map", "x", "y")},
                  "player_labels": before_fix, "player_fix": player_fix, "marked": loop.seen_marked,
                  "act_flags": flags, "raw_action": choice.action, "why": choice.why,
                  "action": label, "target": point, "self_target": self_target, "ok": ok,
                  "truth_self": truth_self, "self_seed": seeded_self,
                  "self_from_motion": seeded_from_motion, "truth_injected": truth_injected,
                  "attempts": attempts[0]}
        print(f'{entry["run"]} {step:03d} | {result["screen"]} | player {before_fix or "none"} | marked={result["marked"]} '
              f'| {label} {point} | self_target={self_target} | ok={ok}', flush=True)
        return result


def _validate(entries, traces, truths, parser):
    if not isinstance(entries, list) or len(entries) > 13:
        parser.error("frames.json must be a list of at most 13 entries")
    for entry in entries:
        key = (entry.get("run"), entry.get("step"))
        if key in PROBE_STEPS:
            parser.error(f"probe frame is not eligible: {key[0]} step {key[1]}")
        if entry["run"] == "m1" and entry["step"] > 94:
            parser.error("m1 frames after step 94 are stale and ineligible")
        if entry["step"] not in traces[entry["run"]] or entry["step"] not in truths[entry["run"]]:
            parser.error(f"{entry['run']} step {entry['step']} needs both trace and truth rows")
        row = traces[entry["run"]][entry["step"]]
        if row.get("probe"):
            parser.error(f"trace labels {entry['run']} step {entry['step']} as a probe")
        if entry.get("self") is not None and any(
                max(abs(entry["self"][i] - point[i]) for i in (0, 1)) <= 0.08
                for point in entry.get("ok_near", [])):
            parser.error(f"{entry['run']} step {entry['step']} has an acceptable target at the player")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, default=ROOT / "scripts/frames.json")
    parser.add_argument("--out", type=Path, default=ROOT / ".tmp/ps/baseline.json")
    parser.add_argument("--dry", action="store_true", help="run with a zero-request stub model")
    args = parser.parse_args(argv)
    entries = json.loads(args.frames.read_text(encoding="utf-8"))
    traces, truths = {}, {}
    for run in {row["run"] for row in entries}:
        run_root = ROOT / "runs" / run
        traces[run] = {row["step"]: row for row in _rows(run_root / "trace.jsonl")}
        truths[run] = {row["step"]: row for row in _rows(run_root / "truth.jsonl")}
    _validate(entries, traces, truths, parser)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmp/ps").mkdir(parents=True, exist_ok=True)
    model = DryModel() if args.dry else Ollama("gemma4:31b-cloud", cache_dir=None)
    attempts = [0]
    if not args.dry:
        model._post = _guarded_post(model._post, attempts)
    before, results = _ledger_snapshot(), []
    status, error = "ok", None
    for entry in entries:
        try:
            results.append(evaluate(entry, traces[entry["run"]], truths[entry["run"]], model, attempts))
        except CallCap as exc:
            status, error = "cap", str(exc)
            break
        except ModelUnavailable as exc:
            status, error = "unavailable", str(exc)
            break
    after = _ledger_snapshot()
    output = {"status": status, "error": error,
              "truth_injection": "m1 label projection is harness-only; agent/ never reads RAM truth",
              "attempts": attempts[0], "ledger_before": before, "ledger_after": after, "results": results}
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"status={status} requests={attempts[0]} ledger_before={before} ledger_after={after}")
    return {"ok": 0, "cap": 3, "unavailable": 4}[status]


if __name__ == "__main__":
    raise SystemExit(main())

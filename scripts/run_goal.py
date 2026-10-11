"""Run the E2 PokÃ©mon Red goals; RAM is used only by the evaluator."""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path; from statistics import mean
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.brief import GENERAL, load_brief
from agent.env_pyboy import PyBoyEnv
from agent.model import ModelUnavailable, Ollama
from agent.observer import cut, for_model, scroll, wait_settled
from agent.perceive import perceive
from agent.predict import effects, verdict
from agent.skills import Ctx, run_affordance, press, select, wait_until
from agent.trace import Trace
from scripts.truth_pokered import read
FIXTURE = Path(".tmp/e2")
LEDGER = FIXTURE / "cloud_budget.json"
GOALS = {"stairs": ("red_2f.state", "place_changes"), "menu": ("red_2f.state", "text_appears"),
         "mom": ("red_1f.state", "text_appears"), "door": ("red_1f.state", "place_changes")}
class CallCap(RuntimeError): pass
def reserve(cap: int) -> int:
    """Lock the phase ledger and reserve one real HTTP attempt before posting."""
    import msvcrt
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with (LEDGER.with_suffix(".lock")).open("a+b") as lock:
        lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            row = json.loads(LEDGER.read_text() or "{}") if LEDGER.exists() else {}
            n = int(row.get("requests", 0))
            if n >= min(60, cap): raise CallCap(f"phase/invocation request cap ({n})")
            tmp = LEDGER.with_suffix(".tmp"); tmp.write_text(json.dumps({"requests": n + 1})); tmp.replace(LEDGER)
            return n + 1
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
def fuzzy(per: dict, names: tuple[str, ...], verb="approach") -> dict | None:
    rows = [*per.get("affordances", []), *per.get("entities", [])]
    row = next((r for n in names for r in rows if n in (r.get("target", "") + " " + r.get("name", "")).casefold()), None)
    return (row if "verb" in row and row.get("verb") == verb else
            {"verb": verb, "target": row.get("name") or row.get("target", ""), "at": row.get("at")}) if row else None
def success(goal, t):
    return ((t["map"] == 0x25) if goal == "stairs" else ("TEXT SPEED" in t["screen_text"].upper()) if goal == "menu"
            else t["text_box"] if goal == "mom" else t["map"] == 0x00)
def text_tokens(s): return [x.casefold() for x in re.findall(r"[A-Za-z]{3,}", s) if x.casefold() not in {"the", "and", "you"}]
def score_perception(per, truth, score):
    score["player"][1] += 1
    sprites = truth["sprites"]; candidates = []
    for e in per.get("entities", []):
        x, y = e["at"][0] * 160, e["at"][1] * 144
        candidates.append((e, x, y))
    left = candidates[:]; hits = 0
    for s in sprites:
        sx, sy = s["x"] + 8, s["y"] + 8
        match = min(left, key=lambda q: (q[1]-sx)**2 + (q[2]-sy)**2, default=None)
        if match and (match[1]-sx)**2 + (match[2]-sy)**2 <= 12**2:
            hits += 1; left.remove(match)
            if s["slot"] == 0 and match[0].get("kind", "").casefold() == "player": score["player"][0] += 1
    score["sprites"][0] += hits; score["sprites"][1] += len(sprites)
    truth_words = text_tokens(truth["screen_text"]); seen = " ".join(per.get("text", [])).casefold()
    if truth_words: score["text"][0] += sum(w in seen for w in truth_words); score["text"][1] += len(truth_words)
def persist(path, summary):
    for row in summary.get("goals", {}).values():
        for key, num in (("verdict_truth_agreement", "correct"), ("registration_acc", "correct"), ("sprite_recall", "found"), ("player_found", "found"), ("text_match", "matched")):
            metric = row.get(key, {}); metric["accuracy"] = metric.get(num, 0) / metric["n"] if metric.get("n") else None
        moves = row.get("tiles_moved_per_press")
        if isinstance(moves, list): row["tiles_moved_per_press"] = mean(moves) if moves else None
    summary["phase_requests"] = int(json.loads(LEDGER.read_text()).get("requests", 0)) if LEDGER.exists() else 0; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(summary, indent=2))
def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--goal", choices=[*GOALS, "all"], required=True); ap.add_argument("--runs", type=int, default=1); ap.add_argument("--max-calls", type=int, required=True)
    a = ap.parse_args(argv)
    if a.runs < 1 or a.max_calls < 1: ap.error("runs and max-calls must be positive")
    summary_path = Path("runs/e2_summary.json"); summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    reports = summary.setdefault("goals", {}); chosen = list(GOALS) if a.goal == "all" else [a.goal]; invocation = [0]
    for goal in chosen:
        report = {"truth_success": 0, "verdict_truth_agreement": {"n": 0, "correct": 0}, "registration_acc": {"n": 0, "correct": 0}, "tile_px": 16, "move_frames": 16, "tiles_moved_per_press": [], "sprite_recall": {"n": 0, "found": 0}, "player_found": {"n": 0, "found": 0}, "text_match": {"n": 0, "matched": 0}, "calls": 0, "requests": 0, "successful_uncached_asks": 0, "retries": 0, "seconds": 0.0, "runs": [], "failures": []}
        for attempt in range(a.runs):
            started = time.perf_counter(); directory = Path("runs") / f"e2_{goal}"; (directory / "frames").mkdir(parents=True, exist_ok=True)
            trace = Trace(directory / "trace.jsonl"); tail = trace.tail(1); step = (tail[0]["step"] if tail else 0)
            events, requests, denied, moves = [], [0], [False], []
            calls = directory / "calls.jsonl"
            def log(row):
                if denied[0]: denied[0] = False; return
                events.append(row)
                with calls.open("a", encoding="utf-8") as f: f.write(json.dumps(row) + "\n")
            model = Ollama("gemma4:31b-cloud", cache_dir=Path(".cache/model"), log=log)
            post = model._post
            def guarded(payload):
                if invocation[0] >= a.max_calls: denied[0] = True; raise CallCap("invocation request cap")
                try: reserve(60)
                except CallCap: denied[0] = True; raise
                invocation[0] += 1; requests[0] += 1; return post(payload)
            model._post = guarded
            env = PyBoyEnv(state=str(FIXTURE / GOALS[goal][0])); ctx = Ctx(env, model, tile_px=16, move_frames=16)
            original_press = env.press
            def measured(key, frames=8, on_frame=None):
                if key not in ("UP", "DOWN", "LEFT", "RIGHT"): return original_press(key, frames, on_frame)
                b = read(env); first = env.screenshot(); captured = []
                def frame(img): captured.append(img); on_frame(img) if on_frame else None
                original_press(key, frames, frame); tick = env.tick
                env.tick = lambda n=1: (tick(n), frame(env.screenshot()))[0]
                try: settled = wait_settled(env)
                finally: env.tick = tick; after = read(env)
                shifts = [scroll(x, y) for x, y in zip([first, *captured], [*captured, settled.frame])]
                px = tuple(sum(s[i] for s in shifts) for i in (0, 1)) if shifts and all(s is not None for s in shifts) else None
                moves.append({"direction": key, "from_map": b["map"], "to_map": after["map"], "ram_delta": [after["x"]-b["x"], after["y"]-b["y"]], "scroll": list(px) if px else None})
            env.press = measured
            local = {"sprites": [0, 0], "player": [0, 0], "text": [0, 0]}; run_ok = False; place_id = 0
            try:
                context = GENERAL + "\n" + load_brief("pokemon_red") + "\nCurrent target query: " + {"stairs": "stair", "menu": "OPTION", "mom": "mom or mother", "door": "door, exit, or mat"}[goal]
                before_set = wait_settled(env); before_truth = read(env); before, _ = perceive(model, for_model(before_set.frame), context)
                score_perception(before, before_truth, local); targets = {"stairs": ("stair",), "mom": ("mom", "mother"), "door": ("door", "mat", "exit")}
                plan = [{"verb": "wait_until", "target": ""}] if not before.get("affordances") else ([{"verb": "press", "target": "START"}, {"verb": "select", "target": "OPTION"}] if goal == "menu" else
                        [fuzzy(before, targets[goal])] if goal != "mom" else [fuzzy(before, targets[goal]), {"verb": "interact", "target": "mom"}])
                for i, aff in enumerate(plan):
                    step_started, event_start, move_start = time.perf_counter(), len(events), len(moves)
                    if goal == "mom" and i == 1:
                        target = fuzzy(before, targets[goal]); aff = {**target, "verb": "interact"} if target else None
                    if aff is None: raise ValueError("target not listed")
                    unobservable = aff["verb"] == "wait_until"
                    if aff["verb"] == "select": res = select(ctx, aff["target"])
                    elif aff["verb"] == "press": res = press(ctx, aff["target"])
                    elif unobservable: res = wait_until(ctx)
                    else: res = run_affordance(ctx, aff, before)
                    if res.status != "done": report["failures"].append({"run": attempt+1, "reason": res.reason, "status": res.status})
                    after_set = wait_settled(env); after_truth = read(env); after, _ = perceive(model, for_model(after_set.frame), context)
                    score_perception(after, after_truth, local)
                    expected = "screen_changes" if goal == "menu" and i == 0 else GOALS[goal][1] if i == len(plan)-1 else "view_scrolled"
                    place = f"place_{place_id}"; outcome = effects(before, after, res, place, f"place_{place_id+1}")
                    scene_cut = cut(res.before, res.after, res.blank_seen)
                    if scene_cut: place_id += 1
                    pred = None if unobservable else {"basis": "pattern", "id": None, "effect": expected, "detail": None, "conf": 1.0}
                    v = "unobservable" if unobservable else verdict(pred, outcome)
                    pixel_expected = expected in ("place_changes", "view_scrolled")
                    truth_hit = (before_truth["map"] != after_truth["map"] if expected == "place_changes" else (before_truth["x"], before_truth["y"]) != (after_truth["x"], after_truth["y"])) if pixel_expected else None
                    if pixel_expected and not unobservable:
                        report["verdict_truth_agreement"]["n"] += 1; report["verdict_truth_agreement"]["correct"] += (v == "hit") == truth_hit
                    frame_name = f"frames/{step+1:06d}.png"; res.before.save(directory / frame_name); step += 1
                    idx = next((j for j, x in enumerate(before.get("affordances", [])) if x.get("verb") == aff["verb"] and x.get("target", "").casefold() == aff.get("target", "").casefold()), None) if aff["verb"] in ("approach", "interact") else None
                    trace.write({"step": step, "t": time.time(), "place": place, "screen": before.get("screen"), "task": goal, "frame": frame_name, "options": len(before.get("affordances", [])), "choice": {"i": idx, "verb": aff["verb"], "target": aff.get("target"), "id": None}, "pred": pred, "used": [], "skill": {"status": res.status, "reason": res.reason, "inputs": res.inputs, "calls": res.calls}, "outcome": outcome, "verdict": v, "calls": len(events)-event_start, "ms": int((time.perf_counter()-step_started)*1000), "shortcut": None})
                    press_rows = moves[move_start:]
                    for m in press_rows:
                        if m["from_map"] == m["to_map"]:
                            dx, dy = m["ram_delta"]; px = m["scroll"]
                            if goal != "menu": report["tiles_moved_per_press"].append(abs(dx)+abs(dy)); report["registration_acc"]["n"] += 1
                            if goal != "menu" and px is not None and round(-px[0]/16) == dx and round(-px[1]/16) == dy:
                                report["registration_acc"]["correct"] += 1
                    with (directory / "truth.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"step": step, **after_truth, "before_truth": before_truth, "presses": press_rows}) + "\n")
                    before, before_truth = after, after_truth
                run_ok = success(goal, read(env)); report["truth_success"] += run_ok; report["failures"].append({"run": attempt+1, "reason": "truth goal not met"}) if not run_ok else None
            except Exception as e:
                env.save(str(FIXTURE / "checkpoint.state")); (FIXTURE / "harness.json").write_text(json.dumps({"goal": goal, "step": step, "tile_px": ctx.tile_px, "move_frames": ctx.move_frames, "presses": moves, "error": str(e)}))
                report["calls"] += requests[0]; report["requests"] += requests[0]; report["successful_uncached_asks"] += model.calls; report["retries"] += sum(r.get("retries", 0) for r in events); report["seconds"] += time.perf_counter()-started
                report["player_found"]["n"] += local["player"][1]; report["player_found"]["found"] += local["player"][0]; report["sprite_recall"]["found"] += local["sprites"][0]; report["sprite_recall"]["n"] += local["sprites"][1]; report["text_match"]["matched"] += local["text"][0]; report["text_match"]["n"] += local["text"][1]
                report["failures"].append({"run": attempt+1, "type": type(e).__name__, "reason": str(e)}); report["runs"].append({"truth_success": False, "perceptions": local["player"][1]})
                reports[goal] = report; persist(summary_path, summary); return 3 if isinstance(e, CallCap) else 4 if isinstance(e, ModelUnavailable) else 1
            finally: env.close()
            report["calls"] += requests[0]; report["requests"] += requests[0]; report["successful_uncached_asks"] += model.calls
            report["retries"] += sum(r.get("retries", 0) for r in events); report["seconds"] += time.perf_counter() - started
            report["player_found"]["n"] += local["player"][1]; report["player_found"]["found"] += local["player"][0]
            report["sprite_recall"]["found"] += local["sprites"][0]; report["sprite_recall"]["n"] += local["sprites"][1]
            report["text_match"]["matched"] += local["text"][0]; report["text_match"]["n"] += local["text"][1]
            report["runs"].append({"truth_success": run_ok, "perceptions": local["player"][1]})
        if report["requests"] > a.max_calls: return 3
        reports[goal] = report
    gate = all(reports.get(g, {}).get("truth_success", 0) == a.runs for g in chosen)
    if a.goal == "all": gate &= all((n := sum(reports[g][k]["n"] for g in chosen)) > 0 and sum(reports[g][k]["correct"] for g in chosen)/n >= limit for k, limit in (("verdict_truth_agreement", .90), ("registration_acc", .95)))
    persist(summary_path, summary); return 0 if gate else 1
if __name__ == "__main__":
    try: raise SystemExit(main())
    except CallCap: raise SystemExit(3)
    except ModelUnavailable: raise SystemExit(4)

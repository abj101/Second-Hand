"""Offline replays for planner size, op transcription, and target closure."""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.memory import Memory
from agent.notebook import Notebook, _refs
from agent.plan import REPLY, plan
from agent.world import World


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _world(run):
    return World(Memory(Path(run) / "memory"), 16)


def _text_from_log(value, step, rows):
    match = re.match(r"(.+?) at ([^:]+):\s*(.*)$", str(value), re.S)
    if not match:
        return
    speaker, where, text = match.groups()
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    rows.append({"key": f"t{len(rows) + 1}", "step": step,
                 "speaker": None if speaker.casefold() == "text" else speaker,
                 "where": where, "text": text})


def _legacy_quest(row, world):
    q = dict(row)
    q["key"] = str(q.get("key") or q.get("id") or "")
    q["text"] = str(q.get("text") or "")
    q["why"] = q.get("why", [])
    status = q.get("status")
    q["status"] = status if status in ("done", "dropped") else "open"
    check = q.get("check")
    raw_target = q.get("target") or (check.get("arg") if isinstance(check, dict) else None)
    q["target"] = world.ref(raw_target) if raw_target is not None else None
    q.setdefault("opened", 0)
    q.setdefault("closed", None)
    q.setdefault("reason", "")
    return q


def _unclosed(q):
    """A snapshot taken after an observe closure reopens here: closures are re-derived, never copied."""
    return {**q, "status": "open", "closed": None, "reason": None, "by": None} if q.get("by") == "observe" else q


def notebook_at(run: Path, step: int) -> Notebook:
    """Re-derive a notebook at step: planner events, then heard text, then observe, per trace step."""
    run = Path(run)
    scratch = tempfile.TemporaryDirectory(prefix="eval-plan-")
    world = _world(run)
    live = lambda kind: [f"{kind[:-1] if kind == 'places' else 'thing'}:{r['id']}" for r in world.mem.rows(kind) if r.get("state") != "merged"]
    nb = Notebook(Path(scratch.name), ref=world.ref, canon=world.canon, label=world.label, ties=world.ties, route=world.exit_route, is_exit=world.is_exit, leads_to=world.leads_to, refs=lambda: live("places") + live("entities"))
    nb._scratch = scratch
    nb.data = {"text": [], "quests": [], "last": {}, "here": None, "moves": []}
    at = lambda event: event.get("step", 0) if type(event.get("step", 0)) is int else 0
    history = [event for event in _rows(run / "memory" / "notebook.history") if at(event) <= step]
    trace = {row.get("step"): row for row in _rows(run / "trace.jsonl") if at(row) <= step}
    for s in sorted({at(event) for event in history} | set(trace)):
        events = [event for event in history if at(event) == s]
        for event in events:
            op = event.get("op")
            if op == "quests" and isinstance(event.get("value"), list):
                nb.data["quests"] = [_legacy_quest(_unclosed(q), world) for q in event["value"] if isinstance(q, dict)]
            elif op in ("add", "done", "drop", "reopen", "top", "target") and isinstance(event.get("quest"), dict):
                q = _unclosed(event["quest"])
                if op == "target": q = {"retargeted": s, **q}
                prior = next((i for i, old in enumerate(nb.data["quests"]) if old.get("key") == q.get("key")), None)
                if prior is not None:
                    old = nb.data["quests"].pop(prior)
                    if "retargeted" in old: q = {"retargeted": old["retargeted"], **q}
                if op == "add" or prior is not None:
                    index = 0 if op in ("top", "reopen") else min(prior if prior is not None else len(nb.data["quests"]), len(nb.data["quests"]))
                    nb.data["quests"].insert(index, q)
            elif op == "last" and isinstance(event.get("value"), dict):
                nb.data["last"] = event["value"]
        nb._close_crossed(s, [])  # apply_ops closes a quest whose exit was already used when its target was set
        texts = []
        for event in events:
            if event.get("op") == "text" and isinstance(event.get("value"), dict):
                texts.append(event["value"])
            elif event.get("op") == "log":
                _text_from_log(event.get("text", ""), s, texts)
        for row in texts:
            if "who" in row:
                who = row["who"]
            else:  # recorded before heard() kept the speaker's ref: resolve the printed speaker
                who = world.ref(row.get("speaker")) if row.get("speaker") else None
                who = who if str(who).startswith("thing:") else None
            nb.heard(s, row.get("speaker"), row.get("where", "?"), row.get("text", ""), who=who)
        if s in trace:
            place = trace[s].get("next_place") or trace[s].get("place")
            nb.observe(s, f"place:{place}" if place else None)
    return nb


class _Capture:
    def ask(self, system, user, images, schema, role):
        self.prompt = user
        return SimpleNamespace(data={"ops": [], "why": ""})


def _size(run):
    failed = False
    for row in _rows(Path(run) / "trace.jsonl"):
        if "plan" not in row or row["plan"] is None:
            continue
        state = row["plan"] if isinstance(row["plan"], dict) else {}
        nb = notebook_at(run, row.get("step", 0))
        trigger = str(state.get("trigger", ""))
        evidence, view = state.get("evidence"), state.get("view", "")
        screen_text, recent = state.get("screen_text", []), state.get("recent", [])
        capture = _Capture()
        plan(capture, trigger, nb, view, screen_text, recent, evidence)
        rendered = nb.render()
        section_starts = (("last_ops", "Your last ops ("), ("last_why", "Your last why:"),
                           ("open_quests", "Open quests (first is worked on now):"),
                           ("closed", "Recently closed:"), ("game_text", "Game text:"))
        lines = rendered.splitlines(keepends=True)
        starts = [(next((i for i, line in enumerate(lines) if line.startswith(marker)), len(lines)), name)
                  for name, marker in section_starts]
        starts = sorted((i, name) for i, name in starts if i < len(lines))
        internal = {name: len("".join(lines[i:starts[n + 1][0] if n + 1 < len(starts) else len(lines)]))
                    for n, (i, name) in enumerate(starts)}
        sections = {
            "notebook_total": len(rendered), **{f"notebook_{key}": size for key, size in internal.items()},
            "trigger": len(f"Trigger: {trigger}"),
            "evidence": len(f"Evidence since last plan: {evidence or 'none'}"),
            "map": len(f"Map and memory: {view or 'none'}"),
            "screen_text": len(f"Text on screen now: {'; '.join(map(str, screen_text)) or 'none'}"),
            "recent": len(f"Recent actions: {'; '.join(recent) or 'none'}"), "reply": len(REPLY),
        }
        sections["prompt_total"] = len(capture.prompt)
        print(f"step {row.get('step')}: " + " ".join(f"{key}={size}" for key, size in sections.items()))
        if sections["notebook_total"] > 3500:
            failed = True
            print(f"FAIL step {row.get('step')}: notebook render is over 3500 chars")
    return 1 if failed else 0


def _reply(raw):
    if isinstance(raw, dict):
        return raw
    text = str(raw or "")
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        return json.loads(match.group()) if match else {}


def _cite(nb, value, candidates=()):
    """Resolve refs to text keys, following old belief keys through their because lists."""
    pending = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    beliefs = {belief.get("key"): belief for belief in candidates if isinstance(belief, dict)}
    known = {row.get("key") for row in nb.data["text"]}
    seen, found = set(), []
    while pending:
        ref = pending.pop(0)
        if ref in seen: continue
        seen.add(ref)
        if ref in known: found.append(ref)
        elif ref in beliefs: pending.extend(_refs(beliefs[ref].get("because")))
    return found


def _ops(run):
    world = _world(run)
    failed = False
    readds = 0
    for call in _rows(Path(run) / "calls.jsonl"):
        if call.get("role") != "plan":
            continue
        step = int(call.get("step", 0))
        raw = call.get("response", "")
        try:
            reply = _reply(raw)
        except (ValueError, TypeError):
            reply = {}
        prior = notebook_at(run, max(0, step - 1))
        ops = reply.get("ops") if isinstance(reply.get("ops"), list) else None
        if ops is None:
            quests = [q for q in reply.get("quests", []) if isinstance(q, dict)]
            beliefs = [b for b in reply.get("beliefs", []) if isinstance(b, dict)]
            old = {q.get("key"): q for q in prior.data["quests"]}
            returned = {v.get("key") for v in quests}
            omissions = [q.get("key") for q in prior.data["quests"]
                         if q.get("status") == "open" and q.get("key") not in returned]
            ops = []
            for q in quests:
                key = q.get("key") or q.get("id")
                if key not in old:
                    check = q.get("check")
                    target = q.get("target") or (check.get("arg") if isinstance(check, dict) else None)
                    target = world.ref(target) if target else None
                    why = _cite(prior, q.get("why"), beliefs)
                    if not why:
                        print(f"ERROR step {step}: cannot map quest {key} source {q.get('why')!r} to a t-key or belief")
                    ops.append({"op": "add", "key": key, "text": q.get("text", ""),
                                "why": why, "target": target})
                    if target and any(done.get("status") in ("done", "dropped") and done.get("target") == target
                                      for done in prior.data["quests"]):
                        readds += 1
                elif old[key].get("status") == "open" and q.get("status") in ("done", "dropped"):
                    status = "drop" if q["status"] == "dropped" else "done"
                    ops.append({"op": status, "key": key,
                                "reason": q.get("reason") or f"recorded status changed to {q['status']}"})
            old_top = prior.focus
            new_top = next((q.get("key") or q.get("id") for q in quests if q.get("status", "open") == "open"), None)
            if new_top and new_top != old_top:
                ops.append({"op": "top", "key": new_top})
            if omissions:
                print(f"step {step}: omitted quests are no-ops: {', '.join(omissions)}")
        else:
            kept = []
            for op in ops:
                if isinstance(op, dict) and op.get("op") in ("believe", "refute"):
                    print(f"step {step}: skipped belief op {op.get('key')}")
                else:
                    kept.append(op)
            ops = kept
            closed = {q.get("target") for q in prior.data["quests"]
                      if q.get("status") in ("done", "dropped") and q.get("target")}
            readds += sum(1 for op in ops if isinstance(op, dict) and op.get("op") == "add" and
                          world.ref(op.get("target")) in closed)
        why = reply.get("why", "")
        payload = {"ops": ops, "why": why}
        nb = notebook_at(run, max(0, step - 1))
        rows = nb.apply_ops(ops, step, why if isinstance(why, str) else "")
        raw_chars = len(raw) if isinstance(raw, str) else len(json.dumps(raw, ensure_ascii=False))
        chars = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        print(f"step {step}: source_reply_chars={raw_chars} ops_json_chars={chars}")
        print(f"  ops={json.dumps(ops, ensure_ascii=False)}")
        print(f"  results={json.dumps(rows, ensure_ascii=False)}")
        if chars > 600:
            failed = True
            print(f"FAIL step {step}: transcribed reply is over 600 chars")
        if len(rows) != len(ops):
            failed = True
            print(f"FAIL step {step}: an op has no result row")
    print(f"re-adds of finished target: {readds}")
    return 1 if failed else 0


def _observe(run):
    run = Path(run)
    trace = _rows(run / "trace.jsonl")
    relations = _rows(run / "memory" / "relations.jsonl")
    places, seen = [], set()
    for index, row in enumerate(trace):
        place = row.get("place")
        if index and place and place not in seen:
            places.append({"target": f"place:{place}", "first": row.get("step"), "kind": "place"})
        if place:
            seen.add(place)
    talks = []
    for relation in relations:
        if relation.get("p") == "says" and relation.get("s") and type(relation.get("step")) is int:
            talks.append({"target": f"thing:{relation['s']}", "first": relation.get("step"), "kind": "talk"})
    schedule = sorted(places + talks, key=lambda item: item["first"])
    world = _world(run)
    nb = notebook_at(run, 0)
    by_open = {}
    for n, item in enumerate(schedule):
        by_open.setdefault(item["first"] - 1, []).append((n, item))
    actual = {}
    activated = set()

    def activate(opened):
        for n, item in by_open.get(opened, []):
            target = world.ref(item["target"]) or item["target"]
            nb.data["quests"].append({"key": f"observe_{item['kind']}_{n}",
                "text": f"{item['kind']} {item['target']}", "why": [], "target": target,
                "status": "open", "opened": opened,
                "closed": None, "reason": None})
            activated.add(n)

    for index, row in enumerate(trace):
        step = row.get("step", 0)
        if index == 0:
            for opened in sorted(k for k in by_open if k < step):
                activate(opened)
        for relation in relations:
            if relation.get("p") == "says" and relation.get("step") == step and relation.get("s"):
                nb.heard(step, None, "?", str(relation.get("o", "")), who=f"thing:{relation['s']}")
        closed = nb.observe(step, f"place:{row['place']}" if row.get("place") else None)
        for key in closed:
            q = next(q for q in nb.data["quests"] if q.get("key") == key)
            actual[q["text"].split(" ", 1)[1] + f"@{q['closed']}"] = q["closed"]
        activate(step)
    failed = False
    for n, item in enumerate(schedule):
        key = item["target"] + f"@{item['first']}"
        expected = "open" if item["kind"] == "talk" else item["first"]
        found = next((q.get("status") for q in nb.data["quests"] if q.get("key") == f"observe_talk_{n}"), None) if item["kind"] == "talk" else actual.get(key)
        print(f"{item['kind']} {item['target']}: expected={expected} result={found}")
        if found != expected:
            failed = True
    if failed:
        print("FAIL: place targets must close on first entry; talk targets must stay open")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--check", choices=("size", "ops", "observe", "all"), required=True)
    args = parser.parse_args(argv)
    checks = {"size": _size, "ops": _ops, "observe": _observe}
    selected = checks if args.check == "all" else {args.check: checks[args.check]}
    return int(any([check(args.run) for check in selected.values()]))


if __name__ == "__main__":
    raise SystemExit(main())

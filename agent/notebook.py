import json
import time
from pathlib import Path

MAX_OPEN, MAX_HELD, MAX_OPS = 5, 5, 8


def _refs(value):
    return [value] if isinstance(value, str) else value if isinstance(value, list) else []


class Notebook:
    def __init__(self, root: Path, *, ref=None, canon=None) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.file, self.history = root / "notebook.json", root / "notebook.history"
        self.ref, self.canon = ref or (lambda text: None), canon or (lambda value: value)
        try:
            loaded = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        if isinstance(loaded, dict) and isinstance(loaded.get("text"), list) and isinstance(loaded.get("quests"), list):
            self.data = {"text": loaded["text"], "quests": loaded["quests"],
                         "beliefs": loaded.get("beliefs", {}), "last": loaded.get("last", {})}
            if not isinstance(self.data["beliefs"], dict): self.data["beliefs"] = {}
            if not isinstance(self.data["last"], dict): self.data["last"] = {}
            self.log = loaded.get("log", [])
        else:
            self.data = {"text": [], "quests": [], "beliefs": {}, "last": {}}
            self.log = []
        if not isinstance(self.log, list): self.log = []

    @property
    def focus(self):
        return next((q["key"] for q in self.data["quests"] if q.get("status") == "open"), None)

    def _known_key(self, key):
        return any(row.get("key") == key for row in self.data["text"] if isinstance(row, dict)) or key in self.data["beliefs"]

    def _resolve(self, target):
        resolved = self.ref(target) if target is not None else None
        if not isinstance(resolved, str) or not resolved.startswith(("place:", "thing:")):
            return None
        resolved = self.canon(resolved)
        return resolved if isinstance(resolved, str) and resolved.startswith(("place:", "thing:")) else None

    @staticmethod
    def _label(target):
        value = str(target).strip()
        kind, sep, ident = value.partition(":")
        return ident if sep and kind in ("place", "thing") else value

    def _apply(self, op, key, step, op_data, event):
        quests, beliefs = self.data["quests"], self.data["beliefs"]
        quest = next((q for q in quests if q.get("key") == key), None)
        if op == "add":
            if any(q.get("key") == key for q in quests): return "key is already used"
            text, why = op_data.get("text"), op_data.get("why")
            if not isinstance(text, str) or not text.strip(): return "text is empty"
            refs = _refs(why)
            if not refs or any(not isinstance(r, str) or not self._known_key(r) for r in refs): return "why must cite text or a belief"
            if sum(q.get("status") == "open" for q in quests) >= MAX_OPEN: return "maximum open quests reached"
            target = op_data.get("target")
            resolved = self._resolve(target) if target is not None else None
            if target is not None and resolved is None: return "target must resolve to a place or thing"
            value = {"key": key, "text": text, "why": refs, "target": resolved,
                     "label": self._label(target) if target is not None else None,
                     "status": "open", "opened": step, "closed": None, "reason": None}
            quests.append(value)
            event["quest"] = value
        elif op in ("done", "drop"):
            reason = op_data.get("reason")
            if not quest or quest.get("status") != "open": return "key is not open"
            if not isinstance(reason, str) or not reason.strip(): return "reason is empty"
            quest.update(status="done" if op == "done" else "dropped", closed=step, reason=reason)
            event["quest"] = quest
        elif op == "top":
            if not quest or quest.get("status") != "open": return "key is not open"
            quests.remove(quest)
            quests.insert(0, quest)
            event["quest"] = quest
        elif op == "target":
            target = op_data.get("target")
            if not quest or quest.get("status") != "open": return "key is not open"
            if target is None: return "target must resolve to a place or thing"
            resolved = self._resolve(target)
            if resolved is None: return "target must resolve to a place or thing"
            quest.update(target=resolved, label=self._label(target))
            event["quest"] = quest
        elif op == "believe":
            text, because = op_data.get("text"), op_data.get("because", [])
            if key in beliefs: return "key is already used"
            if not isinstance(text, str) or not text.strip(): return "text is empty"
            refs = _refs(because)
            if any(not isinstance(r, str) or not self._known_key(r) for r in refs): return "because cites an unknown key"
            if sum(b.get("status") == "held" for b in beliefs.values() if isinstance(b, dict)) >= MAX_HELD: return "maximum held beliefs reached"
            value = {"key": key, "text": text, "because": refs, "status": "held", "reason": None, "step": step}
            beliefs[key] = value
            event["belief"] = value
        elif op == "refute":
            reason = op_data.get("reason")
            belief = beliefs.get(key)
            if not isinstance(belief, dict) or belief.get("status") != "held": return "key is not held"
            if not isinstance(reason, str) or not reason.strip(): return "reason is empty"
            belief.update(status="refuted", reason=reason, step=step)
            event["belief"] = belief
        if op in ("add", "done", "drop", "top", "target", "believe", "refute"):
            return None
        return "unknown op"

    def apply_ops(self, ops, step: int, why: str = "") -> list[dict]:
        rows, events = [], []
        if not isinstance(ops, list):
            rows.append({"op": None, "key": None, "result": "error: ops must be a list"})
        else:
            for index, raw in enumerate(ops, 1):
                op = raw.get("op") if isinstance(raw, dict) else None
                key = raw.get("key") if isinstance(raw, dict) else None
                event = {"step": step, "op": op, "key": key}
                error = None
                if index > MAX_OPS: error = "not applied: more than 8 ops"
                elif not isinstance(raw, dict): error = "op must be an object"
                elif op is None: error = "missing op"
                elif not isinstance(key, str) or not key.strip(): error = "missing key"
                elif len(key) > 40: error = "key exceeds 40 characters"
                else:
                    key = key.strip()
                    event["key"] = key
                    op_data = raw
                    error = self._apply(op, key, step, op_data, event)
                result = f"error: {error}" if error else "ok"
                row = {"op": op, "key": key if isinstance(key, str) else None, "result": result}
                rows.append(row)
                event["result"] = result
                events.append(event)
        self.data["last"] = {"step": step, "why": why, "results": rows}
        self._save(events + [{"step": step, "op": "last", "value": self.data["last"]}])
        return rows

    def heard(self, step: int, speaker: str | None, where: str, text: str) -> str:
        key = f"t{len(self.data['text']) + 1}"
        value = {"key": key, "step": step, "speaker": speaker, "where": where, "text": text}
        self.data["text"].append(value)
        self._save([{"step": step, "op": "text", "value": value}])
        return key

    def observe(self, step: int, place: str | None, talked=()) -> list[str]:
        talked = {self.canon(ref) for ref in talked}
        here = self.canon(place) if place is not None else None
        closed, events = [], []
        for quest in self.data["quests"]:
            target = quest.get("target")
            opened = quest.get("opened", step)
            if quest.get("status") != "open" or not target or (type(opened) is int and step < opened): continue
            kind = target.partition(":")[0]
            if kind == "place" and here == self.canon(target): reason = f"reached {quest.get('label') or target}"
            elif kind == "thing" and self.canon(target) in talked: reason = f"talked to {quest.get('label') or target}"
            else: continue
            quest.update(status="done", closed=step, reason=reason)
            closed.append(quest["key"])
            events.append({"step": step, "op": "observe", "key": quest["key"], "quest": quest})
        if events: self._save(events)
        return closed

    def note(self, step: int, text: str) -> None:
        text = str(text)
        if text:
            self.log.append({"step": step, "text": text})
            self._save([{"step": step, "op": "log", "text": text}])

    def _save(self, lines):
        if lines:
            with self.history.open("a", encoding="utf-8") as f:
                f.write("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
        self.log = self.log[-200:]
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.data, "log": self.log}, ensure_ascii=False, indent=1), encoding="utf-8")
        for wait in (0, .05, .1, .2, .4, .8):
            time.sleep(wait)
            try:
                tmp.replace(self.file)
                break
            except PermissionError:
                if wait == .8: raise

    def _game_text_for(self, quests):
        pending = [ref for q in quests for ref in _refs(q.get("why"))]
        seen, lines = set(), []
        beliefs = self.data["beliefs"]
        while pending:
            key = pending.pop(0)
            if key in seen: continue
            seen.add(key)
            text = next((row for row in self.data["text"] if row.get("key") == key), None)
            if text:
                lines.append(text)
            elif isinstance(beliefs.get(key), dict):
                pending.extend(_refs(beliefs[key].get("because")))
        return lines

    @staticmethod
    def _text_line(row, limit=None):
        value = f"{row['key']} {row.get('speaker') or 'text'} at {row.get('where', '?')}: \"{row.get('text', '')}\""
        return value if limit is None else value[:limit]

    def render(self) -> str:
        d = self.data
        open_quests = [q for q in d["quests"] if q.get("status") == "open"]
        closed = sorted((q for q in d["quests"] if q.get("status") in ("done", "dropped")),
                        key=lambda q: q.get("closed", 0), reverse=True)[:5]
        held = [b for b in d["beliefs"].values() if b.get("status") == "held"][:MAX_HELD]
        ruled = sorted((b for b in d["beliefs"].values() if b.get("status") == "refuted"),
                       key=lambda b: b.get("step", 0), reverse=True)[:5]
        cited = self._game_text_for(open_quests)
        cited_keys = {row["key"] for row in cited}
        recent = d["text"][-8:]
        last = d["last"]
        lines = [f"Your last ops (step {last.get('step', 0)}):"]
        lines.extend(f"- {row.get('op')} {row.get('key')}: {row.get('result')}" for row in last.get("results", []))
        lines.extend(["Your last why:", str(last.get("why", "")), "Open quests (first is worked on now):"])
        for q in open_quests:
            target = f" [target: {q['label']}]" if q.get("label") else ""
            lines.append(f"- {q['key']}: {q['text']}{target}")
            lines.extend(f"  {self._text_line(row)}" for row in self._game_text_for([q]))
        lines.append("Recently closed:")
        lines.extend(f"- {q['key']}: {q.get('status')} — {q.get('reason', '')}" for q in closed)
        lines.append("Held beliefs:")
        lines.extend(f"- {b['key']}: {b.get('text', '')} (because: {', '.join(_refs(b.get('because')))})" for b in held)
        lines.append("Ruled out:")
        lines.extend(f"- {b['key']}: {b.get('text', '')} — {b.get('reason', '')}" for b in ruled)
        lines.append("Game text:")
        lines.extend(self._text_line(row) for row in cited)
        lines.extend(self._text_line(row, 160) for row in recent if row.get("key") not in cited_keys)
        return "\n".join(lines)

    def render_focus(self, limit: int = 4000) -> str:
        quest = next((q for q in self.data["quests"] if q.get("status") == "open"), None)
        lines = ["Focus:"]
        if quest:
            target = f" [target: {quest['label']}]" if quest.get("label") else ""
            lines.extend([f"Top quest: {quest['text']}{target}", "Because:"])
            lines.extend(self._text_line(row) for row in self._game_text_for([quest]))
        else:
            lines.extend(["Top quest:", "Because:"])
        if self.log:
            lines.append("Already happened (log):")
            kept = []
            for entry in reversed(self.log):
                line = f"- step {entry.get('step')}: {entry.get('text')}"
                candidate = [*kept, line]
                if len("\n".join(lines + candidate)) > limit: break
                kept = candidate
            lines.extend(reversed(kept))
        return "\n".join(lines)

import difflib
import json
import time
from pathlib import Path

MAX_OPEN, MAX_OPS = 5, 8


def _refs(value):
    return [value] if isinstance(value, str) else value if isinstance(value, list) else []


class Notebook:
    def __init__(self, root: Path, *, ref=None, canon=None, label=None, refs=None) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.file, self.history = root / "notebook.json", root / "notebook.history"
        self.ref, self.canon = ref or (lambda text: None), canon or (lambda value: value)
        self.label, self.refs = label or self._label, refs or (lambda: [])
        try:
            loaded = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        if isinstance(loaded, dict) and isinstance(loaded.get("text"), list) and isinstance(loaded.get("quests"), list):
            self.data = {"text": loaded["text"], "quests": loaded["quests"], "last": loaded.get("last", {})}
            if not isinstance(self.data["last"], dict): self.data["last"] = {}
            self.log = loaded.get("log", [])
        else:
            self.data = {"text": [], "quests": [], "last": {}}
            self.log = []
        if not isinstance(self.log, list): self.log = []

    @property
    def focus(self):
        return next((q["key"] for q in self.data["quests"] if q.get("status") == "open"), None)

    def _known_key(self, key):
        return any(row.get("key") == key for row in self.data["text"] if isinstance(row, dict))

    def _no_target(self, target):
        labels = list(dict.fromkeys(l for r in self.refs() if (l := self.label(r)) and self.ref(l)))
        closest = difflib.get_close_matches(str(target), labels, n=3, cutoff=0)
        tail = f"closest: {', '.join(closest)}" if closest else "no known places or things yet"
        return f'target "{target}" matches no known place or thing; {tail}'

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
        quests = self.data["quests"]
        quest = next((q for q in quests if q.get("key") == key), None)
        if op == "add":
            if any(q.get("key") == key for q in quests): return "key is already used"
            text, why = op_data.get("text"), op_data.get("why")
            if not isinstance(text, str) or not text.strip(): return "text is empty"
            refs = _refs(why)
            if not refs or any(not isinstance(r, str) or not self._known_key(r) for r in refs):
                newest = [row["key"] for row in self.data["text"][-3:][::-1]]
                return f"why must cite game-text keys, newest: {', '.join(newest)}" if newest else "why must cite game-text keys, none heard yet"
            if sum(q.get("status") == "open" for q in quests) >= MAX_OPEN: return "maximum open quests reached"
            target = op_data.get("target")
            resolved = self._resolve(target) if target is not None else None
            if target is not None and resolved is None: return self._no_target(target)
            value = {"key": key, "text": text, "why": refs, "target": resolved,
                     "status": "open", "opened": step, "closed": None, "reason": None}
            quests.append(value)
            event["quest"] = value
        elif op in ("done", "drop"):
            reason = op_data.get("reason")
            if not quest or quest.get("status") != "open": return "key is not open"
            if not isinstance(reason, str) or not reason.strip(): return "reason is empty"
            quest.update(status="done" if op == "done" else "dropped", closed=step, reason=reason)
            event["quest"] = quest
        elif op == "reopen":
            reason = op_data.get("reason")
            if not quest or quest.get("status") not in ("done", "dropped"): return "key is not closed"
            if not isinstance(reason, str) or not reason.strip(): return "reason is empty"
            if sum(q.get("status") == "open" for q in quests) >= MAX_OPEN: return "maximum open quests reached"
            quests.remove(quest)
            quests.insert(0, quest)
            quest.update(status="open", closed=None, by=None, reason=reason, reopened=step)
            event["quest"] = quest
        elif op == "top":
            if not quest or quest.get("status") != "open": return "key is not open"
            quests.remove(quest)
            quests.insert(0, quest)
            event["quest"] = quest
        elif op == "target":
            target = op_data.get("target")
            if not quest or quest.get("status") != "open": return "key is not open"
            if target is None: return "target is missing"
            resolved = self._resolve(target)
            if resolved is None: return self._no_target(target)
            quest.update(target=resolved, retargeted=step)
            event["quest"] = quest
        if op in ("add", "done", "drop", "reopen", "top", "target"):
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

    def heard(self, step: int, speaker: str | None, where: str, text: str, who=None) -> str:
        key = f"t{len(self.data['text']) + 1}"
        value = {"key": key, "step": step, "speaker": speaker, "where": where, "text": text,
                 "who": self.canon(who) if who else None}
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
            if kind == "place" and here == self.canon(target): reason = "reached"
            elif kind == "thing" and self.canon(target) in talked: reason = "talked"
            else: continue
            quest.update(status="done", closed=step, reason=reason, by="observe")
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
        for key in pending:
            if key in seen: continue
            seen.add(key)
            text = next((row for row in self.data["text"] if row.get("key") == key), None)
            if text:
                lines.append(text)
        return lines

    def _evidence(self, q):
        target = q.get("target")
        if q.get("by") != "observe" or not target:
            return q.get("reason", "")
        if target.startswith("place:"): return f"reached {self.label(target)}"
        said = [self._text_line(row, 160) for row in self.data["text"]
                if row.get("who") == self.canon(target) and row.get("step", 0) >= q["closed"]][:2]
        return f"talked to {self.label(target)}, " + ("who said " + "; ".join(said) if said else "who said nothing yet")

    @staticmethod
    def _text_line(row, limit=None):
        value = f"{row['key']} {row.get('speaker') or 'text'} at {row.get('where', '?')}: \"{row.get('text', '')}\""
        return value if limit is None else value[:limit]

    def render(self) -> str:
        d = self.data
        open_quests = [q for q in d["quests"] if q.get("status") == "open"]
        closed = sorted((q for q in d["quests"] if q.get("status") in ("done", "dropped")),
                        key=lambda q: q.get("closed", 0), reverse=True)[:5]
        cited = self._game_text_for(open_quests)
        cited_keys = {row["key"] for row in cited}
        recent = d["text"][-8:]
        last = d["last"]
        lines = [f"Your last ops (step {last.get('step', 0)}):"]
        lines.extend(f"- {row.get('op')} {row.get('key')}: {row.get('result')}" for row in last.get("results", []))
        lines.extend(["Your last why:", str(last.get("why", "")), "Open quests (first is worked on now):"])
        for q in open_quests:
            target = f" [target: {self.label(q['target'])}]" if q.get("target") else ""
            lines.append(f"- {q['key']}: {q['text']}{target}")
            lines.extend(f"  {self._text_line(row)}" for row in self._game_text_for([q]))
        lines.append("Recently closed:")
        for q in closed:
            retarget = f" (target set by you at step {q['retargeted']})" if q.get("retargeted") is not None else ""
            lines.append(f"- {q['key']} \"{q['text']}\": {q.get('status')} at step {q.get('closed')} — {self._evidence(q)}{retarget}")
        lines.append("Game text:")
        lines.extend(self._text_line(row) for row in cited)
        lines.extend(self._text_line(row, 160) for row in recent if row.get("key") not in cited_keys)
        return "\n".join(lines)

    def render_focus(self, label) -> str:
        quest = next((q for q in self.data["quests"] if q.get("status") == "open"), None)
        lines = []
        if quest:
            target = quest.get("target")
            done_when = ""
            if target:
                kind = target.partition(":")[0]
                condition = "talked to" if kind == "thing" else "in"
                done_when = f" (done when: {condition} {label(target)})"
            lines.extend([f"Goal: {quest['text']}{done_when}", "Because:"])
            lines.extend(
                f"{row.get('speaker') or 'text'}, step {row.get('step', '?')}: \"{row.get('text', '')}\""[:160]
                for row in self._game_text_for([quest])[:3]
            )
        else:
            lines.extend(["Goal: (none)", "Because:"])
        lines.append("Recent:")
        lines.extend(f"- step {entry.get('step')}: {entry.get('text')}"[:120] for entry in self.log[-4:])
        return "\n".join(lines)

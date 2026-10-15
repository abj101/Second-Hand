import json
import re
import time
from pathlib import Path

SECTIONS = ("facts", "beliefs", "quests", "notes", "chapters")
MAX_OPEN = 5
FINISHED = ("done", "dropped")
_TACTICAL = re.compile(r"#\d|\(\s*-?\d+\s*,\s*-?\d+\s*\)|^(?:press|move|walk|go)\s+(?:up|down|left|right)\b", re.I)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class Notebook:
    """Append-or-revise run state. Planner output is reconciled against code-owned history."""

    def __init__(self, root: Path, *, buttons=(), labels=None) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.file, self.history = root / "notebook.json", root / "notebook.history"
        self.buttons, self.labels = tuple(buttons or ()), labels
        self._pending_migration = None
        try:
            loaded = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        loaded = _dict(loaded)
        if all(isinstance(loaded.get(section), (dict, list)) for section in SECTIONS):
            self.data = {section: loaded[section] for section in SECTIONS}
            self.log = self._load_log(loaded.get("log"))
        else:
            self.data = {section: {} if section in ("facts", "beliefs") else [] for section in SECTIONS}
            self.log = self._load_log(loaded.get("log"))
            if "goals" in loaded or "hints" in loaded or "lessons" in loaded:
                self._migrate(loaded)

    @property
    def focus(self) -> str | None:
        return next((q.get("key") for q in self.data["quests"] if q.get("status") == "open"), None)

    @staticmethod
    def _load_log(value):
        if isinstance(value, list):
            return [e for e in value if isinstance(e, dict)][-200:]
        if isinstance(value, str):  # old checkpoints stored a human-readable event string
            out = []
            for line in value.splitlines():
                match = re.match(r"(\d+),\s*(.*)", line)
                if match:
                    out.append({"step": int(match.group(1)), "text": match.group(2)})
            return out[-200:]
        return []

    def _migrate(self, old):
        events = []
        hints = _dict(old.get("hints"))
        for key, value in hints.items():
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                fact = {"key": key, **value}
                self.data["facts"][key] = fact
                events.append({"step": value.get("step", 0), "op": "migrate_fact", "fact": fact})
        goals = _dict(old.get("goals"))
        heard = [ (key, g) for key, g in goals.items()
                  if isinstance(g, dict) and g.get("source") == "heard" and g.get("status") not in FINISHED
                  and not key.casefold().startswith("try_") and not self._bad_quest(g.get("text", ""), self.buttons) ]
        for key, goal in sorted(heard, key=lambda pair: pair[1].get("step", 0)):
            reference = next((fk for fk, fact in self.data["facts"].items()
                              if fact.get("source") == "heard" and
                              (fact.get("text", "") in str(goal.get("evidence", "")) or
                               str(goal.get("evidence", "")) in fact.get("text", ""))), None)
            quest = {"key": key, "text": goal.get("text", key), "why": [reference] if reference else [],
                     "done_when": goal.get("done_when", ""), "status": "open", "evidence": "",
                     "opened": goal.get("step", 0)}
            if not reference:
                quest["source"] = "heard"
            self.data["quests"].append(quest)
            events.append({"step": quest["opened"], "op": "migrate_quest", "quest": quest})
        migrated = {key for key, _ in heard}
        summaries = [f"{g.get('text', key)} ({g.get('status', 'unfinished')})"
                     for key, g in goals.items() if isinstance(g, dict) and g.get("status") not in FINISHED
                     and key not in migrated]
        if summaries:
            chapter = {"from": 0, "to": max((g.get("step", 0) for g in goals.values() if isinstance(g, dict)), default=0),
                       "closed": [], "summary": "; ".join(summaries)[:600], "ruled_out": [], "open_leads": []}
            self.data["chapters"].append(chapter)
            events.append({"step": chapter["to"], "op": "migrate_chapter", "chapter": chapter})
        for key, lesson in _dict(old.get("lessons")).items():
            lesson = lesson if isinstance(lesson, dict) else {"text": str(lesson)}
            self.data["notes"].append({"step": lesson.get("step", 0), "trigger": "migration", "text": str(lesson.get("text", ""))[:400]})
            events.append({"step": lesson.get("step", 0), "op": "migrate_note", "key": key})
        self._pending_migration = {"state": old, "events": events}

    def apply_plan(self, plan, step: int, *, buttons=()):
        flags, quest_changed, belief_changed = [], 0, 0
        self.buttons = tuple(buttons or self.buttons)
        belief_changed += self._apply_beliefs(_value(plan, "beliefs", []), step, flags)
        self._apply_facts(_value(plan, "facts", []), step)
        quests = _value(plan, "quests")
        if not isinstance(quests, list):
            flags.append("planner returned no quests; kept current state")
            quests = None
        if quests is not None:
            incoming, keys = [], set()
            old_by_key = {q.get("key"): q for q in self.data["quests"] if isinstance(q, dict)}
            for raw in quests:
                if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"].strip():
                    flags.append("rejected quest: missing key")
                    continue
                key = raw["key"].strip()
                if key in keys:
                    flags.append(f"rejected {key}: duplicate key")
                    continue
                keys.add(key)
                if self._bad_quest(raw.get("text", ""), self.buttons):
                    flags.append(f"rejected {key}: name an outcome")
                    continue
                old = old_by_key.get(key, {})
                quest = {**old, **raw, "key": key}
                quest["opened"] = old.get("opened", raw.get("opened", step))
                quest["evidence"] = old.get("evidence", "")
                if "check_history" in old:
                    quest["check_history"] = old["check_history"]
                status = raw.get("status", old.get("status", "open"))
                if status in FINISHED and not str(raw.get("evidence", "")).strip():
                    flags.append(f"refused {key}: {status} requires evidence")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                elif status == "dropped" and self._heard_quest(old or quest) and not self._has_reference(raw):
                    flags.append(f"refused {key}: dropping a heard quest needs a fact or belief reference")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                elif status == "done" and quest.get("check") and not self._check_passes(quest):
                    flags.append(f"refused {key}: mechanical check has not passed")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                else:
                    quest["status"] = status if status in (*FINISHED, "open", "parked") else "open"
                    if status in FINISHED:
                        quest["evidence"] = self._append_evidence(old.get("evidence", ""), raw.get("evidence", ""))
                        quest["closed"] = old.get("closed", step)
                incoming.append(quest)
            present = {q.get("key") for q in incoming}
            for old in self.data["quests"]:
                key = old.get("key")
                if key not in present:
                    if old.get("status") == "open" and self._heard_quest(old):
                        flags.append(f"omitted {key}: keep, park or drop it with a reason")
                    incoming.append(old)
            open_count = 0
            for quest in incoming:
                if quest.get("status") == "open":
                    open_count += 1
                    if open_count > MAX_OPEN:
                        quest["status"] = "parked"
                        flags.append(f"parked {quest.get('key')}: maximum {MAX_OPEN} open quests")
            old_quests = self.data["quests"]
            if incoming != old_quests:
                self.data["quests"] = incoming
                quest_changed = sum(q != old_by_key.get(q.get("key")) for q in incoming) or 1
                self._save([{"step": step, "op": "quests", "value": incoming}])

        note = _value(plan, "note")
        if isinstance(note, str) and note.strip():
            value = {"step": step, "trigger": str(_value(plan, "why", "planner"))[:80], "text": " ".join(note.split())[:400]}
            self.data["notes"].append(value)
            self._save([{"step": step, "op": "planner_note", "value": value}])
        self.flags = flags
        return {"quests_changed": quest_changed, "beliefs_changed": belief_changed, "flags": flags}

    def _apply_beliefs(self, beliefs, step, flags):
        changed = 0
        if not isinstance(beliefs, list):
            return changed
        by_key = {b.get("key"): b for b in self.data["beliefs"].values() if isinstance(b, dict)}
        for raw in beliefs:
            if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"].strip():
                continue
            key, old = raw["key"].strip(), by_key.get(raw["key"].strip(), {})
            value = {**old, **raw, "key": key}
            value["history"] = list(old.get("history", []))
            if old and (value.get("text") != old.get("text") or value.get("status") != old.get("status")):
                reason = str(raw.get("reason", "")).strip()
                if not reason:
                    flags.append(f"refused belief update {key}: reason required")
                    continue
                value["history"].append({"step": step, "from_text": old.get("text", ""), "reason": reason})
            if value != old:
                self.data["beliefs"][key] = value
                by_key[key] = value
                changed += 1
                self._save([{"step": step, "op": "belief", "value": value}])
        return changed

    def _apply_facts(self, facts, step):
        if not isinstance(facts, list):
            return
        for raw in facts:
            if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not isinstance(raw.get("text"), str):
                continue
            key, text = raw["key"].strip(), raw["text"].strip()
            if not key or not text:
                continue
            target = key
            if target in self.data["facts"] and self.data["facts"][target].get("text") != text:
                n = 2
                while f"{key}_{n}" in self.data["facts"]:
                    n += 1
                target = f"{key}_{n}"
            if target not in self.data["facts"]:
                value = {**raw, "key": target, "step": step}
                self.data["facts"][target] = value
                self._save([{"step": step, "op": "fact", "value": value}])

    @staticmethod
    def _append_evidence(old, new):
        old, new = str(old or "").strip(), str(new or "").strip()
        return old if not new or new in old else f"{old}; {new}".strip("; ")

    def _bad_quest(self, text, buttons):
        text = str(text or "")
        if _TACTICAL.search(text):
            return True
        return any(button and re.search(rf"(?<!\w){re.escape(str(button))}(?!\w)", text, re.I) for button in buttons)

    def _heard_quest(self, quest):
        refs = quest.get("why", [])
        refs = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
        return quest.get("source") == "heard" or any(
            isinstance(ref, str) and self.data["facts"].get(ref, {}).get("source") == "heard" for ref in refs)

    def _has_reference(self, quest):
        refs = quest.get("why", [])
        refs = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
        known = set(self.data["facts"]) | set(self.data["beliefs"])
        if any(ref in known for ref in refs if isinstance(ref, str)):
            return True
        evidence_refs = set(re.findall(r"[A-Za-z0-9_]+", str(quest.get("evidence", ""))))
        return bool(known & evidence_refs)

    def _check_passes(self, quest):
        check = _dict(quest.get("check"))
        arg = str(check.get("arg", ""))
        if check.get("kind") == "reach":
            labels = self.labels() if callable(self.labels) else self.labels
            labels = [labels] if isinstance(labels, str) else labels or []
            return any(arg.casefold() == str(label).casefold() for label in labels)
        for entry in self.log:
            if entry.get("step", 0) <= quest.get("opened", 0):
                continue
            match = re.match(r'(.+?) at .+: "(.*)"$', str(entry.get("text", "")))
            if match and ((check.get("kind") == "speak" and match.group(1).casefold() == arg.casefold()) or
                          (check.get("kind") == "text" and arg.casefold() in match.group(2).casefold())):
                return True
        return False

    def run_checks(self, step: int):
        closed = []
        place_labels = self.labels() if callable(self.labels) else self.labels
        if isinstance(place_labels, str):
            place_labels = [place_labels]
        for quest in self.data["quests"]:
            check = _dict(quest.get("check"))
            if quest.get("status") != "open" or check.get("kind") not in ("reach", "speak", "text"):
                continue
            arg = str(check.get("arg", ""))
            evidence = None
            if check["kind"] == "reach" and any(arg.casefold() == str(label).casefold() for label in (place_labels or [])):
                evidence = f"check reach {arg} at step {step}"
            if check["kind"] in ("speak", "text"):
                for entry in self.log:
                    if entry.get("step", 0) <= quest.get("opened", 0):
                        continue
                    line = str(entry.get("text", ""))
                    match = re.match(r'(.+?) at .+: "(.*)"$', line)
                    if not match:
                        continue
                    if check["kind"] == "speak" and match.group(1).casefold() == arg.casefold():
                        evidence = f"check speak {arg} at step {step}"
                    elif check["kind"] == "text" and arg.casefold() in match.group(2).casefold():
                        evidence = f"check text {arg} at step {step}"
                    if evidence:
                        break
            if evidence:
                quest.update(status="done", evidence=self._append_evidence(quest.get("evidence"), evidence), closed=step)
                quest.setdefault("check_history", []).append({"step": step, "result": "pass", "evidence": evidence})
                closed.append(quest["key"])
                self._save([{"step": step, "op": "check", "key": quest["key"], "evidence": evidence}])
        return closed

    def apply_chapter(self, chapter, step: int):
        if not isinstance(chapter, dict) or not isinstance(chapter.get("summary"), str):
            return False
        leads = set(chapter.get("open_leads", [])) if isinstance(chapter.get("open_leads", []), list) else set()
        state = {q.get("key") for q in self.data["quests"] if q.get("status") == "open" and q.get("key")}
        state |= {k for k, b in self.data["beliefs"].items() if k and b.get("status", "held") == "held"}
        required = [q.get("key") for q in self.data["quests"] if q.get("status") == "open"]
        required += [k for k, b in self.data["beliefs"].items() if b.get("status", "held") == "held"]
        if any(not key or (key not in state and key not in leads) for key in required):
            return False
        start = self.data["notes"][0].get("step", step) if self.data["notes"] else step
        closed = [q["key"] for q in self.data["quests"] if q.get("status") in FINISHED
                  and type(q.get("closed")) is int and start <= q["closed"] <= step]
        value = {"from": start, "to": step, "closed": closed, "summary": chapter["summary"][:600],
                 "ruled_out": chapter.get("ruled_out", []), "open_leads": chapter.get("open_leads", [])}
        self.data["chapters"].append(value)
        self.data["notes"] = []
        self._save([{"step": step, "op": "chapter", "value": value}, {"step": step, "op": "clear_notes"}])
        return True

    def note(self, step: int, text: str):
        text = str(text)
        if text:
            self.log.append({"step": step, "text": text})
            self._save([{"step": step, "op": "log", "text": text}])

    def _save(self, lines):
        if lines:
            with self.history.open("a", encoding="utf-8") as f:
                if self._pending_migration:
                    f.write(json.dumps({"step": 0, "op": "migrate_v1", "state": self._pending_migration["state"]}, ensure_ascii=False) + "\n")
                    f.write("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in self._pending_migration["events"]))
                    self._pending_migration = None
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
                if wait == .8:
                    raise

    def render_focus(self, limit: int = 4000):
        open_quests = [q for q in self.data["quests"] if q.get("status") == "open"]
        top = open_quests[0] if open_quests else None
        lines = [f"Focus: {self.focus or '(none)'}"]
        if top:
            fields = ("text", "done_when", "approach")
            lines.append("Top quest: " + json.dumps({k: top[k] for k in fields if k in top}, ensure_ascii=False, separators=(",", ":")))
        lines += ["Other open quests:"] + [f"- {q.get('key')}: {' '.join(str(q.get('text', '')).split())}" for q in open_quests[1:]]
        lines += ["facts:"] + [f"- {k}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in self.data["facts"].items()]
        lines += ["Already happened (log):"] + [f"- step {e.get('step')}: {e.get('text')}" for e in self.log[-10:]]
        protected = {1} | {i for i, line in enumerate(lines) if line.startswith("- ") and
                     (any(line.startswith(f"- {q.get('key')}:") for q in open_quests[1:]) or
                      any(line.startswith(f"- {k}:") for k in self.data["facts"]))}
        return self._fit(lines, limit, protected=protected)

    def render_log(self, n: int = 15):
        return "\n".join(f"- step {e.get('step')}: {e.get('text')}" for e in self.log[-n:]) or "(nothing yet)"

    def render(self, limit: int = 6000):
        d = self.data
        rows = []
        for section in ("facts", "beliefs", "quests", "notes", "chapters"):
            rows.append([section, None, f"{section}:", True])
            values = d[section]
            entries = list(values.values()) if isinstance(values, dict) else values
            for i, value in enumerate(entries):
                key = value.get("key", value.get("step", i))
                hidden = section == "beliefs" and value.get("status") == "refuted"
                heading = "ruled out:" if hidden else None
                if heading:
                    rows.append([section, "ruled out", heading, True])
                line = f"- {key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
                protected = section in ("facts", "beliefs") and value.get("status", "held") != "refuted" or section == "facts" or (section == "quests" and value.get("status") == "open")
                rows.append([section, key, line, protected])
        drop_order = ([r for r in rows if r[0] == "chapters" and r[1] is not None] +
                      [r for r in rows if r[0] == "quests" and r[1] is not None and not r[3]] +
                      [r for r in rows if r[0] == "beliefs" and r[1] is not None and r[2].startswith("- ") and not r[3]])
        while len("\n".join(row[2] for row in rows)) > limit and drop_order:
            drop = drop_order.pop(0)
            if drop in rows:
                rows.remove(drop)
        lines = [row[2] for row in rows]
        protected = {i for i, row in enumerate(rows) if row[3]}
        return self._fit(lines, limit, protected=protected)

    @staticmethod
    def _fit(lines, limit, protected=()):
        out, length = [], 0
        for i, line in enumerate(lines):
            extra = len(line) + bool(out)
            if length + extra > limit and i not in protected:
                continue
            out.append(line)
            length += extra
        return "\n".join(out) or "(empty)"

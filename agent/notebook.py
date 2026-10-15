import json
import re
import time
from pathlib import Path

SECTIONS = ("facts", "beliefs", "quests", "notes", "chapters")
MAX_OPEN = 5
FINISHED = ("done", "dropped")
_TACTICAL = re.compile(
    r"\(\s*-?\d+\s*,\s*-?\d+\s*\)|"
    r"\b(?:door|exit|stairs|mat|rug|gate)\w*\s*#\d|"
    r"\b(?:press|move|walk|go|step|head)\s+(?:up|down|left|right|north|south|east|west)\b",
    re.I,
)
_EXPLORE = re.compile(r"^\s*(?:explore|investigate|look around|search)\b.*\b(?:place|room|area|surroundings)", re.I)
_QUEST_FIELDS = {"key", "text", "why", "done_when", "status", "evidence", "check", "approach"}
_BELIEF_FIELDS = {"key", "text", "because", "confidence", "status", "reason"}
_FACT_FIELDS = {"key", "text", "from", "source", "place"}


def normalize_quest_text(text):
    return " ".join(re.sub(r"[\W_]+", " ", str(text or "").casefold()).split())


def _valid_check(check):
    if not isinstance(check, dict) or check.get("kind") not in ("reach", "speak", "text"):
        return False
    arg = check.get("arg")
    if not isinstance(arg, str) or not arg.strip():
        return False
    return check["kind"] != "text" or (
        len(arg.strip()) >= 4 and re.search(r"(?u)\b[^\W\d_]{3,}\b", arg) is not None
    )


def _strip_place_id(name):
    return re.sub(r"\s+\[[^\]]+\]\s*$", "", str(name)).strip().casefold()


def _edit_distance_one(a, b):
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
    return edits + (j < len(b)) <= 1


def _dict(value):
    return value if isinstance(value, dict) else {}


def _refs(value):
    return [r for r in ([value] if isinstance(value, str) else value if isinstance(value, list) else []) if isinstance(r, str)]


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class Notebook:
    """Append-or-revise run state. Planner output is reconciled against code-owned history."""

    def __init__(self, root: Path, *, buttons=(), places=None, labels=None) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.file, self.history = root / "notebook.json", root / "notebook.history"
        self.buttons, self.places, self.labels = tuple(buttons or ()), places, labels
        self.flags = []
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
                     and key not in migrated and not key.casefold().startswith("try_") and not self._bad_quest(g.get("text", ""), self.buttons)]
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
            old_by_text = {normalize_quest_text(q.get("text")): q for q in self.data["quests"]
                           if isinstance(q, dict) and q.get("status") == "open" and q.get("text")}
            for raw in quests:
                if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"].strip():
                    flags.append("rejected quest: missing key")
                    continue
                key = raw["key"].strip()
                incoming_raw = {field: raw[field] for field in _QUEST_FIELDS if field in raw}
                status = incoming_raw.get("status", "open")
                normalized = normalize_quest_text(incoming_raw.get("text", ""))
                match = old_by_text.get(normalized) if status == "open" and normalized else None
                if match and match.get("key") != key:
                    flags.append(f"merged {key} into {match['key']}")
                    key = match["key"]
                    incoming_raw["key"] = key
                if key in keys:
                    flags.append(f"rejected {key}: duplicate key")
                    continue
                keys.add(key)
                if self._bad_quest(incoming_raw.get("text", ""), self.buttons):
                    flags.append(f"rejected {key}: name an outcome")
                    continue
                if _EXPLORE.search(str(incoming_raw.get("text", ""))) and not self._heard_quest(incoming_raw):
                    flags.append(f"rejected {key}: exploring is not a quest")
                    continue
                old = old_by_key.get(key, {})
                quest = {**old, **incoming_raw, "key": key}
                quest["opened"] = old.get("opened", step)
                quest["evidence"] = old.get("evidence", "")
                if "check_history" in old:
                    quest["check_history"] = old["check_history"]
                status = incoming_raw.get("status", old.get("status", "open"))
                if "check" in incoming_raw and incoming_raw.get("check") != old.get("check"):
                    if status != "open" or (old and old.get("status") != "open"):
                        flags.append(f"refused {key}: check can change only on an open quest before it is done")
                        quest["check"] = old.get("check")
                    elif isinstance(incoming_raw.get("check"), dict) and incoming_raw["check"].get("kind") == "text" and not _valid_check(incoming_raw["check"]):
                        flags.append(f"refused {key}: text check needs at least four characters and a three-letter word")
                        quest.pop("check", None)
                        if "check" in old:
                            quest["check"] = old["check"]
                    elif (why := self._impossible_check(incoming_raw.get("check"), step)):
                        flags.append(f"impossible check {key}: {why}")
                        quest.pop("check", None)
                        if "check" in old:
                            quest["check"] = old["check"]
                if status in FINISHED and not str(incoming_raw.get("evidence", "")).strip():
                    flags.append(f"refused {key}: {status} requires evidence")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                elif status == "dropped" and self._heard_quest(old or quest) and not self._has_reference(incoming_raw):
                    flags.append(f"refused {key}: dropping a heard quest needs a fact or belief reference")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                elif status == "done" and quest.get("check") and not self._check_passes(quest, step):
                    flags.append(f"refused {key}: mechanical check has not passed")
                    quest["status"] = old.get("status", "open")
                    quest["evidence"] = old.get("evidence", "")
                else:
                    quest["status"] = status if status in (*FINISHED, "open", "parked") else "open"
                    if status in FINISHED:
                        quest["evidence"] = self._append_evidence(old.get("evidence", ""), incoming_raw.get("evidence", ""))
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
            open_count = sum(q.get("status") == "open" for q in incoming)
            parked = sorted(((q.get("opened") if type(q.get("opened")) is int else 0, i, q) for i, q in enumerate(incoming)
                             if q.get("status") == "parked"), key=lambda row: (row[0], row[1]))
            for _, _, quest in parked:
                if open_count >= MAX_OPEN:
                    break
                quest["status"] = "open"
                open_count += 1
            old_quests = self.data["quests"]
            if incoming != old_quests:
                self.data["quests"] = incoming
                quest_changed = sum(q != old_by_key.get(q.get("key")) for q in incoming) or 1
                self._save([{"step": step, "op": "quests", "value": incoming}])

        for quest in self.data["quests"]:
            if quest.get("status") == "open" and self._heard_quest(quest) and not _valid_check(_dict(quest.get("check"))):
                flags.append(f"needs check {quest.get('key')}")

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
            incoming = {field: raw[field] for field in _BELIEF_FIELDS if field in raw}
            status = incoming.get("status", old.get("status", "held"))
            if status not in ("held", "revised", "refuted"):
                flags.append(f"refused belief {key}: invalid status")
                continue
            if old.get("status") == "refuted" and status == "held":
                flags.append(f"relapse {key}: refuted beliefs stay ruled out")
                continue
            value = {**old, **incoming, "key": key}
            value["history"] = list(old.get("history", []))
            if old and (value.get("text") != old.get("text") or value.get("status") != old.get("status")):
                reason = str(incoming.get("reason", "")).strip()
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
                value = {field: raw[field] for field in _FACT_FIELDS if field in raw}
                value.update(key=target, step=step)
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
        return any(len(button) >= 2 and re.search(rf"(?<!\w){re.escape(button)}(?!\w)", text, re.I)
                   for button in (str(button).strip() for button in buttons) if button)

    def _impossible_check(self, check, step):
        """Why a check can never pass: reach to the place we stand in, or speak to a lowercase role word (no NPC speaks as "villager"); a capitalised heard name stays allowed."""
        if not isinstance(check, dict): return None
        arg = str(check.get("arg", "")).strip()
        if check.get("kind") == "reach":
            here = self._place_entries(step)[-1:]
            if here and any(_edit_distance_one(_strip_place_id(arg), _strip_place_id(n)) for n in here[-1][1]):
                return "already here; name a place you have not reached"
        if check.get("kind") == "speak" and arg and arg == arg.casefold():
            spoken = {m.group(1).casefold() for e in self.log if (m := re.match(r'(.+?) at .+: "', str(e.get("text", ""))))}
            if arg not in spoken: return "that person has no name; use a text check on their words"
        return None

    def _heard_quest(self, quest):
        if quest.get("source") == "heard":
            return True
        refs = quest.get("why", [])
        pending = [(ref, 1) for ref in ([refs] if isinstance(refs, str) else refs if isinstance(refs, list) else [])]
        seen = set()
        while pending:
            ref, depth = pending.pop()
            if not isinstance(ref, str) or ref in seen or depth > 3:
                continue
            seen.add(ref)
            fact = self.data["facts"].get(ref)
            if isinstance(fact, dict) and fact.get("source") == "heard":
                return True
            belief = self.data["beliefs"].get(ref)
            if isinstance(belief, dict):
                because = belief.get("because", [])
                because = [because] if isinstance(because, str) else because if isinstance(because, list) else []
                pending.extend((item, depth + 1) for item in because)
        return False

    def _has_reference(self, quest):
        refs = quest.get("why", [])
        refs = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
        known = set(self.data["facts"]) | set(self.data["beliefs"])
        if any(ref in known for ref in refs if isinstance(ref, str)):
            return True
        evidence_refs = set(re.findall(r"[A-Za-z0-9_]+", str(quest.get("evidence", ""))))
        return bool(known & evidence_refs)

    def _place_entries(self, step=None):
        if self.places is not None:
            entries = self.places() if callable(self.places) else self.places
            result = []
            for row in entries or ():
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                entered, names = row
                if type(entered) is int:
                    names = [names] if isinstance(names, str) else names
                    result.append((entered, [str(name) for name in (names or [])]))
            return result
        labels = self.labels() if callable(self.labels) else self.labels
        labels = [labels] if isinstance(labels, str) else labels or []
        return [(step, [str(name) for name in labels])] if type(step) is int else []

    def _check_passes(self, quest, step=None):
        check = _dict(quest.get("check"))
        if not _valid_check(check):
            return False
        arg = check["arg"].strip()
        if check.get("kind") == "reach":
            target = _strip_place_id(arg)
            opened = quest.get("opened", 0)
            opened = opened if type(opened) is int else 0
            return any(entered > opened and any(_edit_distance_one(target, _strip_place_id(name)) for name in names)
                       for entered, names in self._place_entries(step))
        for entry in self.log:
            if entry.get("step", 0) <= quest.get("opened", 0):
                continue
            match = re.match(r'(.+?) at .+: "(.*)"$', str(entry.get("text", "")))
            if match and ((check.get("kind") == "speak" and match.group(1).casefold() == arg.casefold()) or
                          (check.get("kind") == "text" and re.search(rf"(?<!\w){re.escape(arg)}(?!\w)", match.group(2), re.I))):
                return True
        return False

    def run_checks(self, step: int):
        closed = []
        for quest in self.data["quests"]:
            check = _dict(quest.get("check"))
            if quest.get("status") != "open" or not _valid_check(check):
                continue
            arg = str(check.get("arg", ""))
            if self._check_passes(quest, step):
                evidence = f"check {check['kind']} {arg} at step {step}"
                quest.update(status="done", evidence=self._append_evidence(quest.get("evidence"), evidence), closed=step)
                quest.setdefault("check_history", []).append({"step": step, "result": "pass", "evidence": evidence})
                closed.append(quest["key"])
                self._save([{"step": step, "op": "check", "key": quest["key"], "evidence": evidence}])
        for quest in self.data["quests"]:
            if quest.get("status") == "open" and self._heard_quest(quest) and not _valid_check(_dict(quest.get("check"))):
                flag = f"needs check {quest.get('key')}"
                flags = getattr(self, "flags", [])
                if flag not in flags:
                    flags.append(flag)
                self.flags = flags
        return closed

    def apply_chapter(self, chapter, step: int):
        if not isinstance(chapter, dict) or not isinstance(chapter.get("summary"), str):
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

    def _cited(self, quests, extra=()):  # keys the quests (and extra refs) cite, directly or through beliefs' because
        pending, seen = [r for q in quests for r in _refs(q.get("why"))] + list(extra), set()
        while pending:
            ref = pending.pop()
            if ref in seen: continue
            seen.add(ref)
            if isinstance(self.data["beliefs"].get(ref), dict): pending += _refs(self.data["beliefs"][ref].get("because"))
        return seen

    def render_focus(self, limit: int = 4000):
        d = self.data
        open_quests = [q for q in d["quests"] if q.get("status") == "open"]
        top = open_quests[0] if open_quests else None
        lines = [f"Focus: {self.focus or '(none)'}"]
        if top:
            fields = ("text", "done_when", "approach")
            lines.append("Top quest: " + json.dumps({k: top[k] for k in fields if k in top}, ensure_ascii=False, separators=(",", ":")))
        lines += ["Other open quests:"] + [f"- {q.get('key')}: {' '.join(str(q.get('text', '')).split())}" for q in open_quests[1:]]
        held = [b for b in d["beliefs"].values() if isinstance(b, dict) and b.get("status", "held") == "held"]
        cited = self._cited([top] if top else [])
        beliefs = ([b for b in held if b.get("key") in cited] or held[-3:])[:3]
        if beliefs: lines += ["beliefs:"] + [f"- {b.get('key')}: {b.get('text')} ({b.get('confidence', 'low')})" for b in beliefs]
        if d["notes"]: lines.append("Planner note: " + str(d["notes"][-1].get("text", ""))[:400])
        facts = list(d["facts"].items())
        lines += ["facts:"] + [f"- {k}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in facts if k in cited or (k, v) in facts[-4:]]
        used, budget = len("\n".join(lines)), []
        for e in reversed(self.log[-10:]):  # the newest log lines that fit
            line = f"- step {e.get('step')}: {e.get('text')}"
            if used + len(line) + 1 > limit and budget: break
            budget.append(line); used += len(line) + 1
        lines += ["Already happened (log):"] + budget[::-1]
        return "\n".join(lines)

    def render_log(self, n: int = 15):
        return "\n".join(f"- step {e.get('step')}: {e.get('text')}" for e in self.log[-n:]) or "(nothing yet)"

    def render(self, limit: int = 6000):
        d = self.data
        live = [q for q in d["quests"] if q.get("status") in ("open", "parked")]
        held = [b for b in d["beliefs"].values() if b.get("status", "held") != "refuted"]
        cited = self._cited(live, [b.get("key") for b in held])
        row = lambda key, value, drop=None: (f"- {key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}", drop)  # drop: None = always shown, else its drop rank
        facts = list(d["facts"].items()); shown = [(k, v) for k, v in facts if k in cited or (k, v) in facts[-8:]]
        ruled, notes = [b for b in d["beliefs"].values() if b.get("status") == "refuted"], d["notes"]
        rows = [("facts:", None)] + [row(k, v) for k, v in shown] + ([(f"(+{len(facts) - len(shown)} older facts)", None)] if len(facts) > len(shown) else [])
        rows += [("beliefs:", None)] + [row(b.get("key"), b) for b in held]
        rows += ([("ruled out:", None)] + [row(b.get("key"), b, 3) for b in ruled]) if ruled else []
        rows += [("quests:", None)] + [row(q.get("key"), q, None if q in live else 2) for q in d["quests"]]
        rows += [("notes:", None)] + [row(n.get("step"), n, 0 if i < len(notes) - 5 else None) for i, n in enumerate(notes)]
        rows += [("chapters:", None)] + [row(c.get("key", c.get("from")), c, 1) for c in d["chapters"]]
        text = lambda: "\n".join(r[0] for r in rows)
        for victim in sorted((r for r in rows if r[1] is not None), key=lambda r: r[1]):  # stable: oldest first within a rank
            if len(text()) <= limit: break
            rows.remove(victim)
        if ruled and not any(r[1] == 3 for r in rows): rows.remove(("ruled out:", None))
        return text() or "(empty)"

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

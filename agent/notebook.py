import json
from pathlib import Path
SECTIONS = ("goals", "hints", "lessons")
CAPS = {"lessons": 10, "hints": 20}  # most recent kept by step
FINISHED = ("done", "blocked", "dropped")
LOG_SHOWN = 15
def _trim(d: dict, keys: list, keep: int) -> None:  # drop oldest-by-step until `keep` of `keys` remain
    for k in sorted(keys, key=lambda k: d[k].get("step", 0))[:max(0, len(keys) - keep)]: del d[k]
class Notebook:
    """Model-owned goals (a tree via `parent`), one `focus` leaf, and an append-only `log` of what happened."""
    def __init__(self, root: Path) -> None:
        root = Path(root); root.mkdir(parents=True, exist_ok=True); self.file, self.history = root / "notebook.json", root / "notebook.history"
        try: loaded = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError): loaded = {}
        loaded = loaded if isinstance(loaded, dict) else {}
        self.data = {s: loaded.get(s) if isinstance(loaded.get(s), dict) else {} for s in SECTIONS}
        self.focus = loaded.get("focus") if isinstance(loaded.get("focus"), str) else None
        self.log = [e for e in loaded.get("log", []) if isinstance(e, dict)] if isinstance(loaded.get("log"), list) else []
    def apply(self, ops, step: int, *, structure: bool = True) -> int:
        """structure=False (the act call): goals can't be created or removed; only the focus can be closed or annotated."""
        lines, goals = [], self.data["goals"]
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict): continue
            if op.get("op") == "focus" and self.focus in goals and op.get("status") in FINISHED:
                goal = goals[self.focus]; goal.update(status=op["status"], evidence=str(op.get("evidence") or "")[:160], step=step)
                self.note(step, f"goal {self.focus} {op['status']}: {goal['evidence']}")
                lines.append(json.dumps({"step": step, **op, "key": self.focus}, ensure_ascii=False)); continue
            if op.get("op") == "tried" and self.focus in goals and str(op.get("text") or "").strip():
                goal = goals[self.focus]; goal["tried"] = [*goal.get("tried", []), str(op["text"]).strip()[:80]][-3:]
                lines.append(json.dumps({"step": step, **op, "key": self.focus}, ensure_ascii=False)); continue
            if op.get("op") not in ("set", "remove") or op.get("section") not in SECTIONS: continue
            if op["section"] == "goals" and not structure: continue
            key = (op.get("key") if isinstance(op.get("key"), str) else "").strip()[:40]
            if not key: continue
            op, section = dict(op, key=key), self.data[op["section"]]
            value = {"text": op.get("value")} if isinstance(op.get("value"), str) else op.get("value")
            if op["op"] == "remove" and key in section: del section[key]
            elif op["op"] == "set" and isinstance(value, dict):
                was = section.get(key, {}).get("status")
                section[key] = dict(value, step=step)
                if op["section"] == "goals" and value.get("status") in FINISHED and was != value.get("status"):
                    self.note(step, f"goal {key} {value['status']}: {str(value.get('evidence') or value.get('text') or '')[:120]}")
            else: continue
            lines.append(json.dumps({"step": step, **op}, ensure_ascii=False))
        for s, cap in CAPS.items(): _trim(self.data[s], list(self.data[s]), cap)
        parents = {g.get("parent") for g in goals.values() if g.get("status") not in FINISHED}
        _trim(goals, [k for k in goals if goals[k].get("status") in FINISHED and k not in parents and k != self.focus], 10)
        self._save(lines)
        return len(lines)
    def note(self, step: int, text: str) -> None:
        """Append to the log; nothing ever removes an entry (history keeps all, render shows the last few)."""
        text = " ".join(str(text).split())[:200]
        if text: self.log.append({"step": step, "text": text}); self._save([json.dumps({"step": step, "op": "log", "text": text}, ensure_ascii=False)])
    def set_focus(self, key, step: int) -> None:
        key = key if isinstance(key, str) and key in self.data["goals"] else None
        if key != self.focus:
            self.focus = key
            if key and self.data["goals"][key].get("status") in (None, "todo"): self.data["goals"][key]["status"] = "active"
            self._save([json.dumps({"step": step, "op": "focus", "key": key}, ensure_ascii=False)])
    def path(self) -> list[str]:
        """Goal keys from the top goal down to the focus."""
        goals, out, key = self.data["goals"], [], self.focus
        while key in goals and key not in out: out.append(key); key = goals[key].get("parent")
        return out[::-1]
    def _save(self, lines) -> None:
        self.log = self.log[-200:]
        with self.history.open("a", encoding="utf-8") as f: f.write("".join(line + "\n" for line in lines))
        tmp = self.file.with_suffix(".tmp"); tmp.write_text(json.dumps({**self.data, "focus": self.focus, "log": self.log}, ensure_ascii=False, indent=1), encoding="utf-8"); tmp.replace(self.file)
    def render_log(self, n: int = LOG_SHOWN) -> str:
        return "\n".join(f"- step {e.get('step')}: {e.get('text')}" for e in self.log[-n:]) or "(nothing yet)"
    def render_focus(self, limit: int = 4000) -> str:
        """What the act call sees: the root-to-focus path, the log, hints and lessons."""
        goals = self.data["goals"]; path = self.path()
        lines = ["Goal path (top -> focus):"] + [f"{'  ' * i}- {k}: " + json.dumps({f: v for f, v in goals[k].items() if f in ("text", "done_when", "status", "tried", "evidence")}, ensure_ascii=False, separators=(",", ":")) for i, k in enumerate(path)] if path else ["Goal path: (no focus yet)"]
        lines += ["Already happened (log):", self.render_log()]
        for s in ("hints", "lessons"): lines += [f"{s}:"] + [f"- {k}: " + json.dumps({f: v for f, v in d.items() if f != "step"}, ensure_ascii=False, separators=(",", ":")) for k, d in self.data[s].items()]
        return "\n".join(lines)[:limit]
    def render(self, limit: int = 6000) -> str:
        d = self.data
        if not any(d.values()): return "(empty)"
        shown = {(s, k) for s in SECTIONS for k in d[s]}
        finished = [k for k in d["goals"] if d["goals"][k].get("status") in ("done", "dropped")]
        drop = [(s, k) for s, keys in (("lessons", d["lessons"]), ("hints", d["hints"]), ("goals", finished)) for k in sorted(keys, key=lambda k: d[s][k].get("step", 0))]
        while True:
            lines = []
            for s in SECTIONS:
                keys = sorted(d[s], key=lambda k: d[s][k].get("status") not in (None, "active")) if s == "goals" else list(d[s])
                lines.append(f"{s}:"); lines += [f"- {k}: " + json.dumps({f: v for f, v in d[s][k].items() if f != "step"}, ensure_ascii=False, separators=(",", ":")) for k in keys if (s, k) in shown]
            text = "\n".join(lines)
            if len(text) <= limit or not drop: return text[:limit]
            shown.discard(drop.pop(0))

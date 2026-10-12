import json
from pathlib import Path
SECTIONS = ("goals", "places", "hints", "lessons")
CAPS = {"lessons": 10, "hints": 20}  # most recent kept by step
def _trim(d: dict, keys: list, keep: int) -> None:  # drop oldest-by-step until `keep` of `keys` remain
    for k in sorted(keys, key=lambda k: d[k].get("step", 0))[:max(0, len(keys) - keep)]: del d[k]
class Notebook:
    def __init__(self, root: Path) -> None:
        root = Path(root); root.mkdir(parents=True, exist_ok=True); self.file, self.history = root / "notebook.json", root / "notebook.history"
        try: loaded = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError): loaded = {}
        loaded = loaded if isinstance(loaded, dict) else {}
        self.data = {s: loaded.get(s) if isinstance(loaded.get(s), dict) else {} for s in SECTIONS}
    def apply(self, ops, step: int) -> int:
        lines = []
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict) or op.get("op") not in ("set", "remove") or op.get("section") not in SECTIONS: continue
            key = (op.get("key") if isinstance(op.get("key"), str) else "").strip()[:40]
            if not key: continue
            op, section = dict(op, key=key), self.data[op["section"]]
            value = {"text": op.get("value")} if isinstance(op.get("value"), str) else op.get("value")
            if op["op"] == "remove" and key in section: del section[key]
            elif op["op"] == "set" and isinstance(value, dict): section[key] = dict(value, step=step)
            else: continue
            lines.append(json.dumps({"step": step, **op}, ensure_ascii=False))
        for s, cap in CAPS.items(): _trim(self.data[s], list(self.data[s]), cap)
        goals = self.data["goals"]; _trim(goals, [k for k in goals if goals[k].get("status") in ("done", "dropped")], 10)
        with self.history.open("a", encoding="utf-8") as f: f.write("".join(line + "\n" for line in lines))
        tmp = self.file.with_suffix(".tmp"); tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"); tmp.replace(self.file)
        return len(lines)
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
    def places(self) -> list[str]: return list(self.data["places"])

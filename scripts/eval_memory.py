"""Zero-request memory and trace counts for one run."""
import argparse, json, re
from collections import defaultdict
from pathlib import Path

MODAL = {"button", "menuitem", "hpbar", "keyboard", "text", "icon", "cursor", "dialoguebox", "menu", "textbox", "arrow"}


def jl(path):
    if not path.exists(): return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if line.strip(): rows.append(json.loads(line))
        except ValueError: pass
    return rows


def analyze(run):
    run = Path(run)
    trace_rows = jl(run / "trace.jsonl")
    trace = {r["step"]: r for r in trace_rows if "step" in r}
    def nonworld(step):
        row = trace.get(step)
        return row is not None and row.get("screen") not in (None, "world")
    places = {}
    for row in jl(run / "memory" / "places.jsonl"): places[row["id"]] = {**places.get(row["id"], {}), **row}
    world_places = {pid for pid, row in places.items() if row.get("screen") == "world"}
    versions = defaultdict(list)
    for row in jl(run / "memory" / "entities.jsonl"): versions[row["id"]].append(row)
    final = {ident: rows[-1] for ident, rows in versions.items()}
    world_ids = {ident for ident, row in final.items() if row.get("state") != "merged" and row.get("place") in world_places}
    modal = sum(re.sub(r"[ _]", "", str(final[i].get("kind", "")).casefold()) in MODAL for i in world_ids)
    minted = sum(isinstance(row.get("first"), int) and nonworld(row["first"]) and nonworld(row["first"] - 1) for row in final.values())
    strict = loose = 0
    for ident in world_ids:
        rows = versions[ident]
        for prev, row in zip(rows, rows[1:]):
            if prev.get("name") == row.get("name"): continue
            step = row.get("last")
            if not isinstance(step, int): continue
            a, b = nonworld(step), nonworld(step - 1)
            strict += a and b
            loose += a or b
    route = crossing = 0
    groups = defaultdict(list)
    def intent(row):
        key = (row.get("choice") or {}).get("intent")
        if key: return key
        label = (row.get("option") or {}).get("label")
        return f"label:{label}" if label else None
    for row in trace_rows:
        stage = ((row.get("skill") or {}).get("failure") or {}).get("stage")
        route += stage == "route"; crossing += stage == "crossing"
        if stage == "route" and intent(row): groups[intent(row)].append(row["step"])
    multi = {key: max(steps) for key, steps in groups.items() if len(steps) >= 2}
    reached = sum(any(intent(row) == key and row.get("step", -1) > last and (row.get("skill") or {}).get("status") == "done" for row in trace_rows) for key, last in multi.items())
    return {"world": len(world_ids), "modal": modal, "minted": minted, "strict": int(strict), "loose": int(loose),
            "route": int(route), "crossing": int(crossing), "steps": len(trace_rows), "unreached": len(multi), "reached": reached}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("run", type=Path)
    run = parser.parse_args(argv).run; x = analyze(run)
    print("run | world rows | modal-kind | minted non-world | renamed strict | renamed loose | route fail | crossing fail | steps | unreached >=2 | later reached")
    print(f"{run} | {x['world']} | {x['modal']} | {x['minted']} | {x['strict']} | {x['loose']} | {x['route']} | {x['crossing']} | {x['steps']} | {x['unreached']} | {x['reached']}")


if __name__ == "__main__": main()

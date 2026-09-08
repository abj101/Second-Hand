"""One-off: analyze turns.jsonl screen/perceive sequence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from loop.agent.prompts import allowed_controls_for_turn

run = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\Abj1\Documents\GitHub\Avatar\runs\20260829T061000Z_sts2_collect\turns.jsonl"
)
rows = [json.loads(l) for l in run.read_text(encoding="utf-8").splitlines() if l.strip()]
print("Run:", run.parent.name, "steps:", len(rows))
print()

for r in rows:
    m = r.get("metrics") or {}
    pr = json.loads(r.get("perceive_raw") or "{}") if r.get("perceive_raw") else {}
    res = r.get("result") or {}
    llm1_screen = pr.get("screen", "")
    hud_screen = m.get("screen", "")
    tut = pr.get("tutorial") or {}
    choice = m.get("choice", "")
    scene_labels = [c.get("label") for c in (pr.get("controls") or []) if c.get("label")]
    combat = pr.get("combat") or {}
    for key in ("enemies", "hand"):
        for v in combat.get(key) or []:
            scene_labels.append(str(v))
    det = []
    if m.get("aim_marks"):
        det = [p.split(":")[1].replace("_", " ") for p in m.get("aim_marks", "").split() if ":" in p]
    allowed, src = allowed_controls_for_turn(
        scene_labels=scene_labels,
        detector_labels=det,
        tutorial_info=tut if tut else None,
        host_stats={"tutorial": tut} if tut else None,
    )
    print(
        f"step {r['step_id']:2d} hud={hud_screen:9s} llm1={llm1_screen:9s} "
        f"tut={tut.get('step')} src={src:3s} choice={choice:18s} "
        f"exec={res.get('executed')} px={res.get('pixel_changed')}"
    )
    flags = []
    if hud_screen != llm1_screen and llm1_screen:
        flags.append(f"hud vs llm1 ({hud_screen} vs {llm1_screen})")
    if choice and choice not in allowed and not str(choice).startswith("screen"):
        flags.append("choice not in allowed")
    if tut.get("active") and hud_screen == "map":
        flags.append("tutorial metadata on map hud")
    if tut.get("active") and llm1_screen == "tutorial" and hud_screen != "tutorial":
        flags.append("llm1 still tutorial, hud moved on")
    if flags:
        print("         FLAG:", "; ".join(flags))
    print(f"         allowed={allowed[:10]}")
    ocr = (m.get("ocr") or "")[:100]
    if ocr:
        print(f"         ocr={ocr}")
    if r.get("last_outcome") or (m.get("note")):
        print(f"         note={(m.get('note') or '')[:100]}")
    print()

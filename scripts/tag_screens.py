"""Tag each curated sample frame with a screen type using the teacher VLM (metadata only, not an answer key).

usage: python scripts/tag_screens.py SAMPLES_DIR [--workers 3] [--model gemma4:31b-cloud]
Reads SAMPLES_DIR/manifest.jsonl, writes SAMPLES_DIR/screens.jsonl ({"id","screen","detail","ms","error"}),
resuming past rows. Screen vocabularies are per game data below, never used by the product path.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop.model.gateway import VlmGateway  # noqa: E402
from loop.os.capture import encode_image  # noqa: E402

SCREENS = {
    "sts2": ["title_menu", "character_select", "run_setup", "combat", "card_reward", "loot_reward", "map", "shop",
             "rest_site", "event", "treasure_chest", "card_grid", "item_popup", "tutorial_overlay", "settings_or_pause",
             "game_over_or_victory", "loading_or_transition", "dialog_confirm", "other"],
    "elden": ["title_menu", "character_creation", "gameplay_explore", "gameplay_combat", "dialog_npc", "menu_inventory",
              "map", "site_of_grace_menu", "item_or_message_popup", "death_screen", "cutscene", "loading_or_transition",
              "other"],
}


def main() -> None:
    a = sys.argv[1:]
    root = Path(a[0])
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 3
    model = a[a.index("--model") + 1] if "--model" in a else "gemma4:31b-cloud"
    rows = [json.loads(line) for line in open(root / "manifest.jsonl", encoding="utf-8")]
    out = root / "screens.jsonl"
    done = {json.loads(line)["id"] for line in open(out, encoding="utf-8")} if out.exists() else set()
    todo = [r for r in rows if r["id"] not in done]
    lock = threading.Lock()
    gw = VlmGateway(model=model, temperature=0.0, max_tokens=256, num_ctx=8192)

    def tag(r: dict) -> None:
        names = SCREENS[r["game"]]
        img = Image.open(root / r["game"] / f"{r['id']}.jpg").convert("RGB")
        t0 = time.perf_counter()
        try:
            call = gw.complete_json(
                system="You classify video game screenshots.",
                user=f"Which screen is this? Pick exactly one of: {', '.join(names)}. "
                     "If a modal or overlay covers the screen, pick the overlay. "
                     "Answer on one line as `<screen>: <at most 8 words naming what is shown>`.",
                frames=[encode_image(img)], timeout_s=180)
            head, _, detail = (call.text or "").strip().strip("`").partition(":")
            head = head.strip().lower()
            p = {"screen": head if head in names else None, "detail": detail.strip() or call.text}
            err = None if p["screen"] else "unparsed"
        except Exception as e:  # noqa: BLE001 - record and move on; resumable
            p, err = {}, f"{type(e).__name__}: {e}"
        rec = {"id": r["id"], "screen": p.get("screen"), "detail": p.get("detail", ""),
               "ms": round((time.perf_counter() - t0) * 1000), "error": err}
        with lock, open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(tag, todo))
    print(f"tagged {len(todo)} frames -> {out}")


if __name__ == "__main__":
    main()

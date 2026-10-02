"""Curate a deduplicated, screen-clustered sample corpus from local collect runs.

usage: python scripts/build_samples.py OUT_DIR [--k-sts2 40] [--k-elden 20]

Sources (all local, nothing downloaded):
  runs/*_<game>_collect/frames/**/*_obs.jpg   observation frames from past collect runs
  git history loop/fixtures/{sts2_screens,elden_hud}  the frozen screenshot-replay fixtures
Writes OUT_DIR/<game>/<digest>.jpg, OUT_DIR/embeddings.npz, OUT_DIR/manifest.jsonl
(one row per kept frame: id, game, run, source, cluster) and OUT_DIR/sheets/<game>_<cluster>.jpg
contact sheets used to name clusters. Screen names and splits are added later to the manifest.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GAMES = ("sts2", "elden")
DUP_COS = 0.985  # ponytail: global near-duplicate cut on DINO CLS; per-screen cuts if a screen collapses
FIXTURE_COMMIT = "d64342f^"  # last tree holding the screenshot-replay fixtures


def _sources(game: str):
    for p in sorted(ROOT.glob(f"runs/*_{game}_collect/frames/**/*_obs.jpg")):
        yield p.relative_to(ROOT).parts[1], str(p.relative_to(ROOT)), lambda p=p: Image.open(p)
    folder = {"sts2": "loop/fixtures/sts2_screens", "elden": "loop/fixtures/elden_hud"}[game]
    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", FIXTURE_COMMIT, "--", folder],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    for name in names:
        if name.endswith((".jpg", ".png")):
            def load(name=name):
                blob = subprocess.run(["git", "show", f"{FIXTURE_COMMIT}:{name}"], cwd=ROOT, capture_output=True, check=True).stdout
                return Image.open(io.BytesIO(blob))
            yield "fixture", f"git:{name}", load


def _embedder():
    import torch
    from transformers import AutoModel

    model = AutoModel.from_pretrained("facebook/dinov2-small").cuda().eval().half()
    mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(3, 1, 1)

    @torch.no_grad()
    def embed(img: Image.Image) -> np.ndarray:
        x = torch.from_numpy(np.asarray(img.convert("RGB").resize((448, 252)))).cuda()
        x = ((x.permute(2, 0, 1).float() / 255 - mean) / std).half()[None]
        out = model(pixel_values=x).last_hidden_state[0]
        v = torch.cat([out[0], out[1:].mean(0)]).float()  # CLS + mean patch: layout and content
        return (v / v.norm()).cpu().numpy()

    return embed


def _kmeans(x: np.ndarray, k: int, iters: int = 50) -> np.ndarray:
    rng = np.random.default_rng(0)
    c = x[rng.choice(len(x), min(k, len(x)), replace=False)]
    for _ in range(iters):
        lab = (x @ c.T).argmax(1)
        c = np.stack([x[lab == i].mean(0) if (lab == i).any() else c[i] for i in range(len(c))])
        c /= np.linalg.norm(c, axis=1, keepdims=True)
    return (x @ c.T).argmax(1)


def _sheet(paths: list[Path], dest: Path, n: int = 12) -> None:
    pick = [paths[i] for i in np.linspace(0, len(paths) - 1, min(n, len(paths))).astype(int)]
    tw, th = 320, 180
    sheet = Image.new("RGB", (tw * 4, th * ((len(pick) + 3) // 4)))
    for i, p in enumerate(pick):
        sheet.paste(Image.open(p).convert("RGB").resize((tw, th)), ((i % 4) * tw, (i // 4) * th))
    sheet.save(dest, quality=85)


def main() -> None:
    a = sys.argv[1:]
    out = Path(a[0])
    ks = {g: int(a[a.index(f"--k-{g}") + 1]) if f"--k-{g}" in a else {"sts2": 40, "elden": 20}[g] for g in GAMES}
    embed = _embedder()
    rows, vecs = [], []
    for game in GAMES:
        (out / game).mkdir(parents=True, exist_ok=True)
        kept = np.zeros((20000, 768), np.float32)
        n_kept = 0
        seen: set[str] = set()
        n_in = 0
        for run, source, load in _sources(game):
            n_in += 1
            img = load().convert("RGB")
            digest = hashlib.sha256(img.tobytes()).hexdigest()[:16]
            if digest in seen:
                continue
            seen.add(digest)
            v = embed(img)
            if n_kept and float(np.max(kept[:n_kept] @ v)) >= DUP_COS:
                continue
            kept[n_kept] = v
            n_kept += 1
            img.save(out / game / f"{digest}.jpg", quality=92)
            rows.append({"id": digest, "game": game, "run": run, "source": source, "size": list(img.size)})
            vecs.append(v)
        print(f"{game}: {n_in} frames -> {n_kept} after dedupe", flush=True)
    x = np.stack(vecs)
    for game in GAMES:
        idx = [i for i, r in enumerate(rows) if r["game"] == game]
        lab = _kmeans(x[idx], ks[game])
        for i, l in zip(idx, lab):
            rows[i]["cluster"] = int(l)
    (out / "sheets").mkdir(exist_ok=True)
    for game in GAMES:
        for l in sorted({r["cluster"] for r in rows if r["game"] == game}):
            members = [out / game / f"{r['id']}.jpg" for r in rows if r["game"] == game and r["cluster"] == l]
            _sheet(members, out / "sheets" / f"{game}_{l:02d}.jpg")
    np.savez(out / "embeddings.npz", ids=np.array([r["id"] for r in rows]), x=x)
    with open(out / "manifest.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()

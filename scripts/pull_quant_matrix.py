"""Pull official Qwen3-VL instruct quants and import Bartowski Q3/Q2 + mmproj."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.config import load_config, project_root
from avatar.experiments.quant_matrix import CELLS, QuantCell, QUANT_SYSTEM, QUANT_USER, gguf_cache_dir
from avatar.harness.capture import encode_image
from avatar.models import make_gateway

HF = "https://huggingface.co/{repo}/resolve/main/{name}"


def _ollama_bin() -> str:
    path = shutil.which("ollama")
    if not path:
        raise SystemExit("ollama not on PATH. Install from https://ollama.com/download")
    return path


def installed_tags(bin_path: str) -> set[str]:
    proc = subprocess.run([bin_path, "list"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or "ollama list failed — is the daemon running?")
    tags: set[str] = set()
    for line in proc.stdout.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            tags.add(name)
            if ":" not in name:
                tags.add(f"{name}:latest")
    return tags


def _head_ok(url: str) -> bool:
    try:
        response = httpx.head(url, follow_redirects=True, timeout=30.0)
        if response.status_code < 400:
            return True
        # Some HF endpoints reject HEAD; try a tiny GET.
        response = httpx.get(url, follow_redirects=True, timeout=30.0, headers={"Range": "bytes=0-0"})
        return response.status_code < 400
    except httpx.HTTPError:
        return False


def _resolve_hf_file(repo: str, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        url = HF.format(repo=repo, name=name)
        print(f"    checking {name}…", flush=True)
        if _head_ok(url):
            return name
    return None


def _download(repo: str, name: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"    cached {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)", flush=True)
        return
    url = HF.format(repo=repo, name=name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"    downloading {name}…", flush=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        with tmp.open("wb") as fh:
            for chunk in response.iter_bytes(1024 * 1024):
                fh.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100.0 * written / total
                    print(f"\r    {pct:5.1f}%  {written / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
        if total:
            print(flush=True)
    tmp.replace(dest)


def _create_imported(cell: QuantCell, ollama: str) -> int:
    assert cell.hf_repo
    work = gguf_cache_dir() / cell.label
    work.mkdir(parents=True, exist_ok=True)
    gguf_name = _resolve_hf_file(cell.hf_repo, cell.gguf_candidates)
    mmproj_name = _resolve_hf_file(cell.hf_repo, cell.mmproj_candidates)
    if not gguf_name or not mmproj_name:
        print(f"  missing GGUF/mmproj for {cell.label} in {cell.hf_repo}", flush=True)
        return 2
    gguf = work / "model.gguf"
    mmproj = work / "mmproj.gguf"
    _download(cell.hf_repo, gguf_name, gguf)
    _download(cell.hf_repo, mmproj_name, mmproj)
    modelfile = work / "Modelfile"
    modelfile.write_text(
        "FROM ./model.gguf\n"
        "FROM ./mmproj.gguf\n"
        "PARAMETER num_ctx 4096\n"
        "PARAMETER temperature 0.7\n",
        encoding="utf-8",
    )
    print(f"  ollama create {cell.ollama_id}", flush=True)
    proc = subprocess.run(
        [ollama, "create", cell.ollama_id, "-f", str(modelfile)],
        cwd=str(work),
        check=False,
    )
    return proc.returncode


def _smoke_frame():
    sample = project_root() / "writeups" / "figures" / "see_start_768_q85.jpg"
    if sample.exists():
        image = Image.open(sample)
    else:
        image = Image.new("RGB", (768, 768), color=(187, 173, 160))
    return encode_image(image, longest_side=768, jpeg_quality=85)


def smoke_vision(cell: QuantCell, cfg: dict) -> str:
    gateway = make_gateway(cfg, cell.ollama_id, "ollama")
    frame = _smoke_frame()
    try:
        call = gateway.complete_json(system=QUANT_SYSTEM, user=QUANT_USER, frames=[frame])
    except Exception as exc:  # noqa: BLE001
        return f"fail:{exc}"
    finally:
        gateway.unload()
    if call.parsed is None:
        return "fail:not-json"
    return "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull/import Qwen3-VL quant-matrix models")
    parser.add_argument("--check", action="store_true", help="list installed vs needed; do not pull")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument(
        "--cell",
        action="append",
        default=None,
        metavar="LABEL",
        help="Only this cell label (repeatable).",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    ollama = _ollama_bin()
    installed = installed_tags(ollama)
    cells = list(CELLS)
    if args.cell:
        wanted = {c.lower() for c in args.cell}
        cells = [c for c in CELLS if c.label.lower() in wanted or c.ollama_id in wanted]
        if not cells:
            parser.error(f"No cells matched {args.cell}")

    failures = 0
    for cell in cells:
        present = cell.ollama_id in installed or f"{cell.ollama_id}:latest" in installed
        mark = "ok" if present else "missing"
        print(f"[{mark}] {cell.label}  {cell.ollama_id}  ({cell.source})", flush=True)
        if args.check:
            if not present:
                failures += 1
            continue
        if present:
            continue
        if cell.ollama_id == "qwen3.5:4b-instruct-q4_K_M":
            print(f"  creating {cell.ollama_id}…", flush=True)
            helper = project_root() / "scripts" / "pull_local_vlms.py"
            proc = subprocess.run(
                [sys.executable, str(helper), "--model", cell.ollama_id],
                check=False,
            )
            if proc.returncode != 0:
                failures += 1
                print(f"  create failed ({proc.returncode})", flush=True)
            continue
        if cell.source in ("official", "ollama"):
            print(f"  pulling {cell.ollama_id}…", flush=True)
            proc = subprocess.run([ollama, "pull", cell.ollama_id], check=False)
            if proc.returncode != 0:
                failures += 1
                print(f"  pull failed ({proc.returncode})", flush=True)
            continue
        code = _create_imported(cell, ollama)
        if code != 0:
            failures += 1
            print(f"  create failed ({code})", flush=True)

    if args.check or args.skip_smoke:
        return 1 if failures else 0

    print("\nVision smoke:", flush=True)
    installed = installed_tags(ollama)
    for cell in cells:
        present = cell.ollama_id in installed or f"{cell.ollama_id}:latest" in installed
        if not present:
            print(f"  skip {cell.label}: not installed", flush=True)
            failures += 1
            continue
        print(f"  smoking {cell.label}…", flush=True)
        result = smoke_vision(cell, cfg)
        print(f"    {result}", flush=True)
        if not result.startswith("ok"):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

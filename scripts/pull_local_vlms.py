"""Pull the Phase 1 Ollama vision models from configs/experiment.yaml."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.config import load_config, project_root
from avatar.models import configured_models, is_ollama_cloud

QWEN35_INSTRUCT = "qwen3.5:4b-instruct-q4_K_M"
QWEN35_BASE = "qwen3.5:4b-q4_K_M"
INSTRUCT_MODELFILE = project_root() / "configs" / "modelfiles" / "qwen3.5-4b-instruct.Modelfile"


def _ollama_bin() -> str:
    path = shutil.which("ollama")
    if not path:
        raise SystemExit("ollama not on PATH. Install from https://ollama.com/download")
    return path


def _parse_version(raw: str) -> tuple[int, int, int] | None:
    for token in raw.replace(",", " ").split():
        parts = token.strip().lstrip("v").split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            return int(parts[0]), int(parts[1]), patch
    return None


def ollama_version(bin_path: str) -> tuple[int, int, int] | None:
    proc = subprocess.run([bin_path, "--version"], capture_output=True, text=True, check=False)
    return _parse_version((proc.stdout or "") + " " + (proc.stderr or ""))


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


def _present(model_id: str, installed: set[str]) -> bool:
    return model_id in installed or f"{model_id}:latest" in installed


def _pull_rows(cfg: dict, only: list[str] | None) -> list[tuple[str, str]]:
    rows = list(configured_models(cfg, only=only))
    if only:
        return rows
    seen = {model for model, _ in rows}
    for item in cfg.get("models", {}).get("local") or []:
        if isinstance(item, dict) and item.get("id"):
            mid = str(item["id"])
            if mid not in seen:
                rows.append((mid, str(item.get("provider", "ollama"))))
                seen.add(mid)
        elif isinstance(item, str) and item not in seen:
            rows.append((item, "ollama"))
            seen.add(item)
    for item in cfg.get("models", {}).get("cloud") or []:
        if isinstance(item, dict) and item.get("id"):
            mid = str(item["id"])
            provider = str(item.get("provider") or ("ollama" if is_ollama_cloud(mid) else "openrouter"))
            if provider != "ollama" or mid in seen:
                continue
            rows.append((mid, provider))
            seen.add(mid)
        elif isinstance(item, str) and item.strip() and is_ollama_cloud(item) and item not in seen:
            rows.append((item.strip(), "ollama"))
            seen.add(item.strip())
    return rows


def ensure_qwen35_instruct(ollama: str, installed: set[str], *, check_only: bool) -> int:
    if _present(QWEN35_INSTRUCT, installed):
        print(f"  [ok] {QWEN35_INSTRUCT}")
        return 0
    print(f"  [missing] {QWEN35_INSTRUCT}")
    if check_only:
        return 1
    if not _present(QWEN35_BASE, installed):
        if _present("qwen3.5:4b", installed):
            print(f"  ollama cp qwen3.5:4b {QWEN35_BASE}")
            proc = subprocess.run([ollama, "cp", "qwen3.5:4b", QWEN35_BASE], check=False)
            if proc.returncode != 0:
                return proc.returncode
        else:
            print(f"  pulling {QWEN35_BASE} ...")
            proc = subprocess.run([ollama, "pull", QWEN35_BASE], check=False)
            if proc.returncode != 0:
                return proc.returncode
    if not INSTRUCT_MODELFILE.is_file():
        print(f"  missing Modelfile {INSTRUCT_MODELFILE}", file=sys.stderr)
        return 2
    print(f"  ollama create {QWEN35_INSTRUCT}")
    proc = subprocess.run(
        [ollama, "create", QWEN35_INSTRUCT, "-f", str(INSTRUCT_MODELFILE)],
        check=False,
    )
    return proc.returncode


def _install(ollama: str, model_id: str, installed: set[str], *, check_only: bool) -> int:
    if model_id == QWEN35_INSTRUCT:
        return ensure_qwen35_instruct(ollama, installed, check_only=check_only)
    if _present(model_id, installed):
        print(f"  [ok] {model_id}")
        return 0
    print(f"  [missing] {model_id}")
    if check_only:
        return 1
    print(f"  Pulling {model_id} ...")
    proc = subprocess.run([ollama, "pull", model_id], check=False)
    if proc.returncode != 0:
        return proc.returncode
    print(f"  Pulled {model_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull configured local VLMs via Ollama")
    parser.add_argument("--check", action="store_true", help="list installed vs configured; do not pull")
    parser.add_argument("--model", action="append", default=None, metavar="ID")
    args = parser.parse_args(argv)

    cfg = load_config()
    rows = _pull_rows(cfg, args.model)
    ollama = _ollama_bin()
    version = ollama_version(ollama)
    if version:
        print(f"Ollama {version[0]}.{version[1]}.{version[2]}")
    installed = installed_tags(ollama)
    failures = 0
    seen: set[str] = set()
    for model_id, _provider in rows:
        if model_id in seen:
            continue
        seen.add(model_id)
        code = _install(ollama, model_id, installed, check_only=args.check)
        if code:
            failures = code if not args.check else 1
            if not args.check:
                return code
        elif not args.check and model_id == QWEN35_INSTRUCT:
            installed = installed_tags(ollama)
    if args.check:
        return 0 if not failures else 1
    print("Local VLMs are installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

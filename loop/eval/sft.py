from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from loop.agent.prompts import SYSTEM_NO_SKILL, SYSTEM_TURN_NO_SKILL, prompt_id, system_for
from loop.config import DEFAULT_MODEL, project_root
from loop.eval.export import (
    V2_DECISION_SCHEMA,
    V2_PROMPT_ID,
    decision_schema_report,
    export_sft_rows,
    notebook_schema_report,
    prompt_hash_report,
    v2_user_prompt,
    write_sft_jsonl,
)
from loop.model.gateway import VlmGateway

DEFAULT_HF_SFT = os.getenv("AVATAR_SFT_HF_MODEL", "unsloth/Qwen3.5-4B")
SFT_USER = (
    "Screenshot of the game. Emit one unlabeled device chord as JSON. "
    "stick_l moves the body. stick_r only moves the camera. "
    "Idle or empty JSON is invalid."
)
SFT_USER_TURN = (
    "Screenshot of a turn-based game with a 0-1000 grid and numbered marks. "
    "Emit one JSON chord: mark, or gx,gy. Play the next move. "
    "Idle or empty JSON is invalid."
)


@lru_cache(maxsize=1)
def _turn_priors() -> dict[str, str]:
    return {
        prompt_id(arm=arm, pace="turn"): system_for(arm, pace="turn")
        for arm in ("no_skill", "dump_all", "ratchet")
    }


def system_prior_for_row(row: dict[str, Any]) -> str | None:
    """System text that matches this row's prompt_id. None if the hash is unknown."""
    if str(row.get("fixture") or "") != "sts2":
        return SYSTEM_NO_SKILL
    hid = str(row.get("prompt_id") or "")
    if not hid:
        return SYSTEM_TURN_NO_SKILL
    return _turn_priors().get(hid)


_system_prior_for_row = system_prior_for_row


def unload_planner(model: str = DEFAULT_MODEL) -> None:
    """Drop Ollama VRAM so QLoRA / DQN can load. PLAN → DETECT → TRAIN."""
    VlmGateway(model=model).unload()


def chord_json(chord: dict[str, Any], *, turn: bool = False) -> str:
    if turn or chord.get("mark") is not None or chord.get("click_x") is not None or chord.get("target") is not None:
        payload: dict[str, Any] = {}
        if chord.get("mark") is not None:
            payload["mark"] = chord.get("mark")
        click_x, click_y = chord.get("click_x"), chord.get("click_y")
        if isinstance(click_x, (int, float)) and isinstance(click_y, (int, float)):
            payload["gx"] = int(round(float(click_x) * 1000))
            payload["gy"] = int(round(float(click_y) * 1000))
        if chord.get("target") is not None:
            payload["target"] = chord.get("target")
        click_x2, click_y2 = chord.get("click_x2"), chord.get("click_y2")
        if isinstance(click_x2, (int, float)) and isinstance(click_y2, (int, float)):
            payload["gx2"] = int(round(float(click_x2) * 1000))
            payload["gy2"] = int(round(float(click_y2) * 1000))
        if chord.get("hover"):
            payload["hover"] = True
        if chord.get("name") and "mark" not in payload and "gx" not in payload:
            payload["action"] = chord.get("name")
        return json.dumps(payload, ensure_ascii=False)
    payload = {
        "stick_l": chord.get("stick_l"),
        "stick_r": chord.get("stick_r"),
        "held": list(chord.get("held") or []),
        "tapped": list(chord.get("tapped") or []),
        "duration_ms": chord.get("duration_ms") or 400,
    }
    if chord.get("name") and not (
        payload["stick_l"]
        or payload["stick_r"]
        or payload["held"]
        or payload["tapped"]
    ):
        payload["action"] = chord.get("name")
    return json.dumps(payload, ensure_ascii=False)


def sft_chat_row(row: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """Unsloth FastVisionModel conversation. Assistant is the verified chord JSON."""
    if str(row.get("decision_schema") or "") == V2_DECISION_SCHEMA:
        intent = row.get("intent")
        if not isinstance(intent, dict) or not intent.get("kind"):
            raise ValueError("v2 SFT row missing typed intent")
        return {
            "messages": [
                {"role": "system", "content": "Return exactly one ActionIntent JSON object matching the supplied schema. No prose."},
                {"role": "user", "content": row.get("user") or v2_user_prompt(row)},
                {"role": "assistant", "content": json.dumps(intent, ensure_ascii=False, separators=(",", ":"))},
            ],
            "decision_schema": V2_DECISION_SCHEMA,
            "prompt_id": str(row.get("prompt_id") or V2_PROMPT_ID),
        }
    obs = str(row.get("obs") or "")
    base = Path(str(row["run_dir"])) if row.get("run_dir") else run_dir
    image = str((base / obs).resolve()) if obs else ""
    turn = str(row.get("fixture") or "") == "sts2"
    system = system_prior_for_row(row)
    if system is None:
        raise ValueError(f"unknown prompt_id {row.get('prompt_id')}")
    logged = str(row.get("user_prompt_text") or "").strip()
    user_text = logged or (SFT_USER_TURN if turn else SFT_USER)
    return {
        "messages": [
            {"role": "system", "content": system.strip()},
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": user_text},
                ],
            },
            {"role": "assistant", "content": chord_json(row.get("chord") or {}, turn=turn)},
        ]
    }


def sandbox_sft_dataset(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """High-score only. Fail / DINO-only / rewritten chords must already be gone."""
    if not rows:
        return False, "no high-score traces"
    schemas = {str(row.get("decision_schema") or "v1") for row in rows}
    if len(schemas) > 1:
        return False, "mixed decision_schema; v1 and v2 training rows cannot mix"
    for row in rows:
        if str(row.get("decision_schema") or "v1") == V2_DECISION_SCHEMA:
            intent = row.get("intent")
            if not isinstance(intent, dict) or intent.get("kind") not in {
                "play_card", "end_turn", "click_control", "inspect"
            }:
                return False, "v2 row missing valid ActionIntent"
            forbidden = {"obs", "image", "chord", "mark", "gx", "gy", "click_x", "click_y"}
            if forbidden.intersection(row):
                return False, "v2 row contains legacy visual/chord fields"
            continue
        if str(row.get("fixture") or "") == "sts2" and system_prior_for_row(row) is None:
            return False, f"unknown prompt_id {row.get('prompt_id')}"
        chord = row.get("chord") or {}
        if not (
            chord.get("stick_l")
            or chord.get("stick_r")
            or chord.get("held")
            or chord.get("tapped")
            or chord.get("name")
            or chord.get("mark") is not None
            or chord.get("click_x") is not None
        ):
            return False, "empty chord in SFT row"
        outcome = row.get("outcome") or {}
        if not (
            outcome.get("view_changed")
            or outcome.get("prompt_cleared")
            or outcome.get("pixel_changed")
            or float(outcome.get("turn_reward") or 0) > 0
        ):
            return False, "row missing view_changed / prompt_cleared / pixel_changed"
    return True, "ok"


def write_sft_chat_jsonl(
    run_dir: Path,
    dest: Path | None = None,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> Path:
    dest = dest or (run_dir / "sft_chat.jsonl")
    rows = export_sft_rows(run_dir, run_dirs=run_dirs, source=source)
    dest.write_text(
        "".join(json.dumps(sft_chat_row(row, run_dir=run_dir)) + "\n" for row in rows),
        encoding="utf-8",
    )
    return dest


def train_sft(
    run_dir: Path,
    *,
    execute: bool = False,
    hf_model: str = DEFAULT_HF_SFT,
    max_steps: int = 30,
    out_dir: Path | None = None,
    planner_model: str = DEFAULT_MODEL,
    run_dirs: list[Path] | None = None,
    merge: bool = True,
    source: str = "vlm",
) -> dict[str, Any]:
    """Unload Ollama, sandbox export, write Unsloth chat JSONL, optionally QLoRA."""
    unload_planner(planner_model)
    traces = write_sft_jsonl(run_dir, run_dirs=run_dirs, source=source)
    rows = export_sft_rows(run_dir, run_dirs=run_dirs, source=source)
    ok, reason = sandbox_sft_dataset(rows)
    report: dict[str, Any] = {
        "ok": ok,
        "reason": reason,
        "n": len(rows),
        "chat": "",
        "traces": str(traces),
        "ollama_unloaded": True,
        "executed": False,
        "hf_model": hf_model,
        "dropped_prompt_hashes": None,
        "source": source,
    }
    hashes = prompt_hash_report(run_dirs or [run_dir])
    schema = notebook_schema_report(run_dirs or [run_dir])
    decision_schema = decision_schema_report(run_dirs or [run_dir])
    report["prompt"] = hashes
    report["notebook_schema"] = schema
    report["decision_schema"] = decision_schema
    if hashes.get("mixed"):
        report["ok"] = False
        report["reason"] = f"mixed prompt_id hashes {hashes.get('prompt_ids')}; refuse train"
        return report
    if schema.get("mixed"):
        report["ok"] = False
        report["reason"] = f"mixed notebook_schema {schema.get('versions')}; refuse train"
        return report
    if decision_schema.get("mixed"):
        report["ok"] = False
        report["reason"] = "mixed decision_schema; refuse train"
        return report
    if not ok:
        return report
    chat_path = write_sft_chat_jsonl(run_dir, run_dirs=run_dirs, source=source)
    report["chat"] = str(chat_path)
    if not execute:
        report["reason"] = "dry-run: chat jsonl ready; pass --execute for Unsloth QLoRA"
        return report
    trained = _run_unsloth_sft(
        chat_path,
        hf_model=hf_model,
        max_steps=max_steps,
        out_dir=out_dir or (run_dir / "sft_adapter"),
        master_dir=run_dir / "sft_master",
        merge=merge,
    )
    report.update(trained)
    return report


def _run_unsloth_sft(
    chat_path: Path,
    *,
    hf_model: str,
    max_steps: int,
    out_dir: Path,
    master_dir: Path | None = None,
    merge: bool = True,
) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from PIL import Image
        from trl import SFTConfig, SFTTrainer
        from unsloth import FastVisionModel
        from unsloth.trainer import UnslothVisionDataCollator
    except ImportError as exc:
        return {
            "ok": False,
            "executed": False,
            "reason": f"unsloth stack missing ({exc}). Dataset is at {chat_path}",
        }

    samples: list[dict[str, Any]] = []
    for line in chat_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        messages = row.get("messages") or []
        converted: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                parts: list[dict[str, Any]] = []
                for part in content:
                    if part.get("type") == "image":
                        path = Path(str(part.get("image") or ""))
                        if not path.exists():
                            return {
                                "ok": False,
                                "executed": False,
                                "reason": f"missing SFT image {path}",
                            }
                        parts.append({"type": "image", "image": Image.open(path).convert("RGB")})
                    else:
                        parts.append(part)
                converted.append({"role": msg.get("role"), "content": parts})
            elif isinstance(content, str):
                converted.append(
                    {
                        "role": msg.get("role"),
                        "content": [{"type": "text", "text": content}],
                    }
                )
        samples.append({"messages": converted})
    if not samples:
        return {"ok": False, "executed": False, "reason": "chat jsonl empty after parse"}

    model, tokenizer = FastVisionModel.from_pretrained(
        hf_model,
        load_in_4bit=True,
        use_gradient_checkpointing="unsloth",
        max_seq_length=4096,
    )
    model = FastVisionModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        finetune_vision_layers=False,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
    )
    FastVisionModel.for_training(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        data_collator=UnslothVisionDataCollator(model, tokenizer),
        train_dataset=Dataset.from_list(samples),
        args=SFTConfig(
            per_device_train_batch_size=1,
            gradient_accumulation_steps=4,
            warmup_steps=min(5, max(0, max_steps // 6)),
            max_steps=max(1, max_steps),
            learning_rate=2e-4,
            logging_steps=1,
            optim="adamw_8bit",
            output_dir=str(out_dir),
            report_to="none",
            remove_unused_columns=False,
            dataset_text_field="",
            dataset_kwargs={"skip_prepare_dataset": True},
            max_length=2048,
        ),
    )
    trainer.train()
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    report: dict[str, Any] = {
        "ok": True,
        "executed": True,
        "reason": "ok",
        "adapter": str(out_dir),
        "max_steps": max_steps,
    }
    if merge:
        master = master_dir or (out_dir.parent / "sft_master")
        shipped = _save_master_and_gguf(model, tokenizer, master)
        report.update(shipped)
        if not shipped.get("gguf_ok") and shipped.get("master"):
            report["reason"] = (
                f"master saved; GGUF/ollama skipped ({shipped.get('gguf_reason')}). "
                "Serve eval from the full-precision master, do not GGUF an older GGUF."
            )
    return report


def _ollama_modelfile(gguf_path: Path) -> str:
    """FROM the generation GGUF; keep sampling params from the instruct Modelfile."""
    template = project_root() / "configs" / "modelfiles" / "qwen3.5-4b-instruct.Modelfile"
    params: list[str] = []
    if template.exists():
        for line in template.read_text(encoding="utf-8").splitlines():
            if line.startswith("PARAMETER"):
                params.append(line)
    if not params:
        params = [
            "PARAMETER temperature 0.7",
            "PARAMETER top_p 0.8",
            "PARAMETER top_k 20",
            "PARAMETER presence_penalty 1.5",
        ]
    return f"FROM {gguf_path.resolve().as_posix()}\n" + "\n".join(params) + "\n"


def _save_master_and_gguf(model: Any, tokenizer: Any, master_dir: Path) -> dict[str, Any]:
    """Full-precision merge first; GGUF only from that master. Never GGUF→GGUF."""
    import subprocess

    master_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"master": str(master_dir), "gguf_ok": False, "ollama_ok": False}
    try:
        save_merged = getattr(model, "save_pretrained_merged", None)
        if callable(save_merged):
            save_merged(str(master_dir), tokenizer, save_method="merged_16bit")
        else:
            merged = getattr(model, "merge_and_unload", lambda: model)()
            merged.save_pretrained(str(master_dir))
            tokenizer.save_pretrained(str(master_dir))
    except Exception as exc:  # noqa: BLE001 — keep adapter even if merge fails
        out["gguf_reason"] = f"merge failed: {exc}"
        return out
    gguf_dir = master_dir / "gguf"
    gguf_dir.mkdir(parents=True, exist_ok=True)
    try:
        save_gguf = getattr(model, "save_pretrained_gguf", None)
        if not callable(save_gguf):
            out["gguf_reason"] = "unsloth save_pretrained_gguf missing"
            return out
        save_gguf(str(gguf_dir), tokenizer, quantization_method="q4_k_m")
    except Exception as exc:  # noqa: BLE001
        out["gguf_reason"] = f"gguf failed: {exc}"
        return out
    blobs = sorted(gguf_dir.glob("*.gguf"))
    weights = [p for p in blobs if "mmproj" not in p.name.lower()]
    gguf_path = weights[0] if weights else (blobs[0] if blobs else None)
    if gguf_path is None:
        out["gguf_reason"] = "no .gguf written from master"
        return out
    out["gguf"] = str(gguf_path)
    out["gguf_ok"] = True
    modelfile = master_dir / "Modelfile"
    modelfile.write_text(_ollama_modelfile(gguf_path), encoding="utf-8")
    out["modelfile"] = str(modelfile)
    try:
        created = subprocess.run(
            ["ollama", "create", DEFAULT_MODEL, "-f", str(modelfile)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        out["ollama_ok"] = created.returncode == 0
        out["ollama_stderr"] = (created.stderr or "")[-500:]
        if created.returncode != 0:
            out["gguf_reason"] = f"ollama create failed: {out['ollama_stderr']}"
    except Exception as exc:  # noqa: BLE001
        out["gguf_reason"] = f"ollama create skipped: {exc}"
    return out

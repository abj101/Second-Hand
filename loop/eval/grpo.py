from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loop.config import DEFAULT_MODEL
from loop.eval.sft import DEFAULT_HF_SFT, chord_json, unload_planner
from loop.log.schema import ActionLog
from loop.os.chords import parse_action
from loop.reward.frozen_score import frozen_score

GRPO_NUM_CTX = 4096
GRPO_GROUP = 8
FREE_MIN_MIB = 500


def _nvidia_smi() -> dict[str, int | None]:
    try:
        raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        return {"used": None, "free": None, "total": None}
    parts = [p.strip() for p in raw.split(",")]
    try:
        used, free, total = (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
    except (IndexError, ValueError):
        return {"used": None, "free": None, "total": None}
    return {"used": used, "free": free, "total": total}


def vram_probe(*, hf_model: str = DEFAULT_HF_SFT, planner_model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Load 4-bit policy + LoRA (shared backbone, no second copy). Refuse if free < 500 MiB."""
    unload_planner(planner_model)
    before = _nvidia_smi()
    report: dict[str, Any] = {
        "ok": False,
        "before": before,
        "after": None,
        "hf_model": hf_model,
        "num_ctx": GRPO_NUM_CTX,
        "reason": "",
    }
    try:
        from unsloth import FastVisionModel
    except ImportError as exc:
        report["reason"] = f"unsloth missing ({exc})"
        return report
    try:
        model, tokenizer = FastVisionModel.from_pretrained(
            hf_model,
            load_in_4bit=True,
            use_gradient_checkpointing="unsloth",
            max_seq_length=GRPO_NUM_CTX,
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
        FastVisionModel.for_inference(model)
        _ = tokenizer
        del model
    except Exception as exc:  # noqa: BLE001
        report["reason"] = f"load failed: {exc}"
        report["after"] = _nvidia_smi()
        return report
    after = _nvidia_smi()
    report["after"] = after
    free = after.get("free")
    report["ok"] = isinstance(free, int) and free >= FREE_MIN_MIB
    report["reason"] = "ok" if report["ok"] else f"free {free} MiB below {FREE_MIN_MIB}"
    return report


def _parse_chord(text: str) -> ActionLog:
    blob = (text or "").strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        start, end = blob.find("{"), blob.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(blob[start : end + 1])
            except json.JSONDecodeError:
                parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return parse_action(parsed, "sts")


def _score_completion(frame: dict[str, Any], text: str) -> float:
    action = _parse_chord(text)
    return float(frozen_score(dict(frame.get("metrics") or {}), action))


def train_grpo(
    frames_path: Path,
    *,
    execute: bool = False,
    live: bool = False,
    group_size: int = GRPO_GROUP,
    hf_model: str = DEFAULT_HF_SFT,
    max_steps: int = 20,
    out_dir: Path | None = None,
    planner_model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Path A: frozen-frame GRPO. Refuses a live Steam window."""
    if live:
        return {"ok": False, "reason": "refuse live window; Path A is frozen frames only"}
    if not frames_path.exists():
        return {"ok": False, "reason": f"missing frames {frames_path}"}
    frames: list[dict[str, Any]] = []
    for line in frames_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            frames.append(json.loads(line))
    if not frames:
        return {"ok": False, "reason": "frozen frame bank empty"}
    scored: list[dict[str, Any]] = []
    for frame in frames[: min(8, len(frames))]:
        dummy = [
            chord_json({"mark": 1, "click_x": 0.5, "click_y": 0.7}, turn=True),
            chord_json({"mark": 9, "click_x": 0.5, "click_y": 0.18}, turn=True),
        ]
        rewards = [_score_completion(frame, text) for text in dummy]
        scored.append({"obs": frame.get("obs"), "rewards": rewards, "mean": sum(rewards) / len(rewards)})
    report: dict[str, Any] = {
        "ok": True,
        "executed": False,
        "reason": "dry-run: frozen_score groups ready; pass --execute after vram-probe",
        "frames": len(frames),
        "group_size": group_size,
        "num_ctx": GRPO_NUM_CTX,
        "sample": scored[:3],
        "hf_model": hf_model,
    }
    if not execute:
        return report
    probe = vram_probe(hf_model=hf_model, planner_model=planner_model)
    report["vram_probe"] = probe
    if not probe.get("ok"):
        report["ok"] = False
        report["reason"] = f"vram-probe failed: {probe.get('reason')}"
        return report
    trained = _run_unsloth_grpo(
        frames,
        hf_model=hf_model,
        group_size=group_size,
        max_steps=max_steps,
        out_dir=out_dir or (frames_path.parent / "grpo_adapter"),
    )
    report.update(trained)
    return report


def gspo_config_or_reason(grpo_config_cls: Any, args_kwargs: dict[str, Any]) -> tuple[Any | None, str | None]:
    """Build GRPOConfig with GSPO kwargs. Fail closed if the TRL build lacks them."""
    try:
        return grpo_config_cls(
            loss_type="dr_grpo",
            importance_sampling_level="sequence",
            **args_kwargs,
        ), None
    except TypeError as exc:
        return None, f"GSPO kwargs unsupported: {exc}"


def _run_unsloth_grpo(
    frames: list[dict[str, Any]],
    *,
    hf_model: str,
    group_size: int,
    max_steps: int,
    out_dir: Path,
) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from PIL import Image
        from trl import GRPOConfig, GRPOTrainer
        from unsloth import FastVisionModel
    except ImportError as exc:
        return {
            "ok": False,
            "executed": False,
            "reason": f"unsloth/trl GRPO missing ({exc})",
        }

    rows: list[dict[str, Any]] = []
    metrics_by_i: list[dict[str, Any]] = []
    for frame in frames:
        obs = Path(str(frame.get("run_dir") or "")) / str(frame.get("obs") or "")
        if not obs.exists():
            continue
        image = Image.open(obs).convert("RGB")
        prompt = str(frame.get("user") or "Emit one JSON chord.")
        rows.append(
            {
                "prompt": [
                    {"role": "system", "content": str(frame.get("system") or "")},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": image},
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                "metrics_i": len(metrics_by_i),
            }
        )
        metrics_by_i.append(dict(frame.get("metrics") or {}))
    if not rows:
        return {"ok": False, "executed": False, "reason": "no frozen frames with images on disk"}

    def reward_fn(completions: list[str], metrics_i: list[int], **_kwargs: Any) -> list[float]:
        out: list[float] = []
        for text, idx in zip(completions, metrics_i):
            action = _parse_chord(str(text))
            hud = metrics_by_i[int(idx)] if 0 <= int(idx) < len(metrics_by_i) else {}
            out.append(float(frozen_score(hud, action)))
        return out

    model, tokenizer = FastVisionModel.from_pretrained(
        hf_model,
        load_in_4bit=True,
        use_gradient_checkpointing="unsloth",
        max_seq_length=GRPO_NUM_CTX,
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
    args_kwargs: dict[str, Any] = {
        "output_dir": str(out_dir),
        "per_device_train_batch_size": 1,
        "num_generations": max(2, group_size),
        "max_completion_length": 128,
        "max_steps": max(1, max_steps),
        "learning_rate": 5e-6,
        "logging_steps": 1,
        "report_to": "none",
        "remove_unused_columns": False,
        "temperature": 0.8,
    }
    grpo_args, gspo_reason = gspo_config_or_reason(GRPOConfig, args_kwargs)
    if gspo_reason:
        return {"ok": False, "executed": False, "reason": gspo_reason}
    try:
        trainer = GRPOTrainer(
            model=model,
            processing_class=tokenizer,
            reward_funcs=reward_fn,
            args=grpo_args,
            train_dataset=Dataset.from_list(rows),
        )
    except TypeError:
        trainer = GRPOTrainer(
            model=model,
            tokenizer=tokenizer,
            reward_funcs=reward_fn,
            args=grpo_args,
            train_dataset=Dataset.from_list(rows),
        )
    trainer.train()
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    return {
        "ok": True,
        "executed": True,
        "reason": "ok",
        "adapter": str(out_dir),
        "max_steps": max_steps,
        "group_size": group_size,
        "num_ctx": GRPO_NUM_CTX,
    }

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loop.config import DEFAULT_MODEL
from loop.eval.export import (
    V2_DECISION_SCHEMA,
    V2_PROMPT_ID,
    decision_schema_report,
    export_dpo_pairs,
    notebook_schema_report,
    prompt_hash_report,
    v2_user_prompt,
    write_dpo_jsonl,
)
from loop.eval.sft import (
    DEFAULT_HF_SFT,
    SFT_USER_TURN,
    chord_json,
    system_prior_for_row,
    unload_planner,
)


def _pair_prompt_ids(pair: dict[str, Any]) -> tuple[str, str]:
    chosen = pair.get("chosen") if isinstance(pair.get("chosen"), dict) else {}
    rejected = pair.get("rejected") if isinstance(pair.get("rejected"), dict) else {}
    shared = str(pair.get("prompt_id") or "")
    left = str(pair.get("chosen_prompt_id") or chosen.get("prompt_id") or shared)
    right = str(pair.get("rejected_prompt_id") or rejected.get("prompt_id") or shared)
    return left, right


def _is_same_frame(pair: dict[str, Any]) -> bool:
    if "same_frame" in pair:
        return bool(pair["same_frame"])
    chosen_obs = str(pair.get("chosen_obs") or "")
    rejected_obs = str(pair.get("rejected_obs") or "")
    if chosen_obs and rejected_obs:
        return chosen_obs == rejected_obs
    return bool(pair.get("obs"))


def _pair_schema(pair: dict[str, Any]) -> tuple[str, str | None]:
    chosen = pair.get("chosen") if isinstance(pair.get("chosen"), dict) else {}
    rejected = pair.get("rejected") if isinstance(pair.get("rejected"), dict) else {}
    left = str(pair.get("decision_schema") or chosen.get("decision_schema") or "v1")
    right = str(pair.get("rejected_decision_schema") or rejected.get("decision_schema") or left)
    if left != right:
        return left, "mismatched decision_schema"
    return left, None


def sandbox_dpo_pairs(pairs: list[dict[str, Any]]) -> tuple[bool, str]:
    """Fail-closed DPO gate. Prefer same-frame (equal obs); refuse inverted rewards or hashes."""
    if not pairs:
        return False, "no dpo pairs"
    hashes: set[str] = set()
    schemas: set[str] = set()
    same_frame_n = 0
    for pair in pairs:
        schema, schema_error = _pair_schema(pair)
        if schema_error:
            return False, f"{schema_error} on pair"
        schemas.add(schema)
        try:
            chosen_r = float(pair["chosen_reward"])
            rejected_r = float(pair["rejected_reward"])
        except (KeyError, TypeError, ValueError):
            return False, "pair missing chosen_reward / rejected_reward"
        if chosen_r <= rejected_r:
            return False, "chosen_reward must exceed rejected_reward"
        left, right = _pair_prompt_ids(pair)
        if left and right and left != right:
            return False, f"mismatched prompt_id on pair {left} vs {right}"
        if schema == V2_DECISION_SCHEMA and left != right:
            return False, f"mismatched v2 prompt_id on pair {left} vs {right}"
        hid = left or right
        if hid:
            hashes.add(hid)
            if schema != V2_DECISION_SCHEMA and system_prior_for_row({"fixture": "sts2", "prompt_id": hid}) is None:
                return False, f"unknown prompt_id {hid}"
        if _is_same_frame(pair):
            same_frame_n += 1
    if len(hashes) > 1:
        return False, f"mismatched prompt_id hashes {sorted(hashes)}"
    if len(schemas) > 1:
        return False, "mixed decision_schema; v1 and v2 DPO pairs cannot mix"
    return True, f"ok same_frame={same_frame_n}/{len(pairs)}"


def _pair_user_prompt(pair: dict[str, Any]) -> str:
    logged = str(pair.get("user_prompt_text") or "").strip()
    if logged:
        return logged
    for key in ("chosen", "rejected"):
        row = pair.get(key)
        if isinstance(row, dict):
            text = str(row.get("user_prompt_text") or "").strip()
            if text:
                return text
    return ""


def dpo_chat_row(pair: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """Unsloth DPO row: prompt is system+user+image; chosen/rejected are chord JSON."""
    schema, schema_error = _pair_schema(pair)
    if schema_error:
        raise ValueError(schema_error)
    if schema == V2_DECISION_SCHEMA:
        chosen = pair.get("chosen")
        rejected = pair.get("rejected")
        if not isinstance(chosen, dict) or not isinstance(rejected, dict):
            raise ValueError("v2 DPO pair missing typed intents")
        if not chosen.get("kind") or not rejected.get("kind"):
            raise ValueError("v2 DPO pair missing ActionIntent kind")
        prompt = pair.get("user") or v2_user_prompt(pair)
        return {
            "prompt": [
                {"role": "system", "content": "Return exactly one ActionIntent JSON object matching the supplied schema. No prose."},
                {"role": "user", "content": prompt},
            ],
            "chosen": [{"role": "assistant", "content": json.dumps(chosen, ensure_ascii=False, separators=(",", ":"))}],
            "rejected": [{"role": "assistant", "content": json.dumps(rejected, ensure_ascii=False, separators=(",", ":"))}],
            "decision_schema": V2_DECISION_SCHEMA,
            "prompt_id": _pair_prompt_ids(pair)[0] or V2_PROMPT_ID,
            "same_frame": _is_same_frame(pair),
        }
    obs = str(pair.get("obs") or "")
    base = Path(str(pair["run_dir"])) if pair.get("run_dir") else run_dir
    image = str((base / obs).resolve()) if obs else ""
    hid = _pair_prompt_ids(pair)[0] or _pair_prompt_ids(pair)[1]
    system = system_prior_for_row({"fixture": "sts2", "prompt_id": hid})
    if system is None:
        raise ValueError(f"unknown prompt_id {hid}")
    user_text = _pair_user_prompt(pair) or SFT_USER_TURN
    return {
        "prompt": [
            {"role": "system", "content": system.strip()},
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": user_text},
                ],
            },
        ],
        "chosen": [{"role": "assistant", "content": chord_json(pair.get("chosen") or {}, turn=True)}],
        "rejected": [{"role": "assistant", "content": chord_json(pair.get("rejected") or {}, turn=True)}],
        "same_frame": _is_same_frame(pair),
    }


def write_dpo_chat_jsonl(
    run_dir: Path,
    dest: Path | None = None,
    *,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> Path:
    dest = dest or (run_dir / "dpo_chat.jsonl")
    pairs = export_dpo_pairs(run_dir, run_dirs=run_dirs, source=source)
    dest.write_text(
        "".join(json.dumps(dpo_chat_row(pair, run_dir=run_dir)) + "\n" for pair in pairs),
        encoding="utf-8",
    )
    return dest


def _is_qwen_4b_base(hf_model: str) -> bool:
    return "qwen3.5-4b-base" in hf_model.lower().replace("_", "-")


def train_dpo(
    run_dir: Path,
    *,
    execute: bool = False,
    hf_model: str = DEFAULT_HF_SFT,
    max_steps: int = 30,
    out_dir: Path | None = None,
    planner_model: str = DEFAULT_MODEL,
    run_dirs: list[Path] | None = None,
    source: str = "vlm",
) -> dict[str, Any]:
    """Unload Ollama, sandbox DPO pairs, write Unsloth chat JSONL, optionally DPOTrainer."""
    unload_planner(planner_model)
    pairs_path = write_dpo_jsonl(run_dir, run_dirs=run_dirs, source=source)
    pairs = export_dpo_pairs(run_dir, run_dirs=run_dirs, source=source)
    ok, reason = sandbox_dpo_pairs(pairs)
    report: dict[str, Any] = {
        "ok": ok,
        "reason": reason,
        "n": len(pairs),
        "chat": "",
        "pairs": str(pairs_path),
        "ollama_unloaded": True,
        "executed": False,
        "hf_model": hf_model,
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
    chat_path = write_dpo_chat_jsonl(run_dir, run_dirs=run_dirs, source=source)
    report["chat"] = str(chat_path)
    if not execute:
        report["reason"] = "dry-run: dpo jsonl ready; pass --execute for Unsloth DPO"
        return report
    trained = _run_unsloth_dpo(
        chat_path,
        hf_model=hf_model,
        max_steps=max_steps,
        out_dir=out_dir or (run_dir / "dpo_adapter"),
    )
    report.update(trained)
    return report


def _convert_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    converted: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for part in content:
                if part.get("type") == "image":
                    from PIL import Image

                    path = Path(str(part.get("image") or ""))
                    if not path.exists():
                        return None, f"missing DPO image {path}"
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
        else:
            converted.append(msg)
    return converted, None


def _run_unsloth_dpo(
    chat_path: Path,
    *,
    hf_model: str,
    max_steps: int,
    out_dir: Path,
) -> dict[str, Any]:
    if _is_qwen_4b_base(hf_model):
        return {
            "ok": False,
            "executed": False,
            "reason": f"refuse {hf_model}; use unsloth/Qwen3.5-4B (never Qwen3.5-4B-Base)",
        }
    try:
        from datasets import Dataset
        from trl import DPOConfig, DPOTrainer
        from unsloth import FastVisionModel
    except ImportError as exc:
        return {
            "ok": False,
            "executed": False,
            "reason": f"unsloth stack missing ({exc}). Dataset is at {chat_path}",
        }
    try:
        from unsloth import PatchDPOTrainer
    except ImportError:
        PatchDPOTrainer = None
    if PatchDPOTrainer is not None:
        PatchDPOTrainer()

    samples: list[dict[str, Any]] = []
    for line in chat_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        prompt, err = _convert_messages(list(row.get("prompt") or []))
        if err:
            return {"ok": False, "executed": False, "reason": err}
        chosen, err = _convert_messages(list(row.get("chosen") or []))
        if err:
            return {"ok": False, "executed": False, "reason": err}
        rejected, err = _convert_messages(list(row.get("rejected") or []))
        if err:
            return {"ok": False, "executed": False, "reason": err}
        samples.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    if not samples:
        return {"ok": False, "executed": False, "reason": "dpo chat jsonl empty after parse"}

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
    config = DPOConfig(
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        warmup_steps=min(5, max(0, max_steps // 6)),
        max_steps=max(1, max_steps),
        learning_rate=5e-7,
        logging_steps=1,
        optim="adamw_8bit",
        output_dir=str(out_dir),
        report_to="none",
        remove_unused_columns=False,
        beta=0.1,
    )
    trainer_kwargs: dict[str, Any] = {
        "model": model,
        "ref_model": None,
        "args": config,
        "train_dataset": Dataset.from_list(samples),
    }
    try:
        trainer = DPOTrainer(processing_class=tokenizer, **trainer_kwargs)
    except TypeError:
        trainer = DPOTrainer(tokenizer=tokenizer, **trainer_kwargs)
    trainer.train()
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    return {
        "ok": True,
        "executed": True,
        "reason": "ok",
        "adapter": str(out_dir),
        "max_steps": max_steps,
    }

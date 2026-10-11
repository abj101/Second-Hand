"""Replay Gemma's chooser rows against a code baseline and local Decider-2b."""
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MODEL = "Mapika/decider-2b"
REVISION = "b37f7e1ba3fbc9238004cf531fabbee2619973fd"

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
def failed(recent, action):
    key = (str(action.get("verb", "")).casefold(), str(action.get("target", "")).casefold())
    return any(r.get("verdict") in ("miss", "surprise") and isinstance(r.get("choice"), dict) and
               (str(r["choice"].get("verb", "")).casefold(),
                str(r["choice"].get("target", "")).casefold()) == key for r in recent)


def run(directory):
    root = Path(directory)
    chooser = rows(root / "chooser.jsonl")
    if not chooser:
        print("no chooser rows")
        return None
    trace = rows(root / "trace.jsonl")
    trace_by_step = {r.get("step"): r for r in trace}
    from agent.decide import code_choice
    import torch
    from decider.infer import Decider
    from huggingface_hub import snapshot_download
    if not torch.cuda.is_available():
        raise RuntimeError("Decider-2b comparison requires CUDA")
    model_path = snapshot_download(MODEL, revision=REVISION, local_files_only=True)
    model = Decider(model_path, device="cuda", dtype=torch.bfloat16)
    compared, latencies, failed_picks = {"code_choice": [], "decider_2b": []}, [], {"code_choice": 0, "decider_2b": 0}
    gemma_failed = gemma_picks = 0
    for row in chooser:
        affs = row.get("affordances") or []
        if not affs:
            continue
        current = trace_by_step.get(row.get("step"), {})
        place = row.get("place", current.get("place"))
        frame_hash = row.get("frame_hash", current.get("frame_hash"))
        recent = row.get("recent") or [r for r in trace if r.get("step", 0) < row.get("step", 0)
                  and frame_hash and r.get("place") == place and r.get("frame_hash") == frame_hash][-5:]
        ids = row.get("view_ids") or []
        baseline = code_choice({"affordances": affs}, ids, recent)
        gemma = (row.get("choice") or {}).get("i")
        if isinstance(gemma, int) and 0 <= gemma < len(affs):
            gemma_picks += 1
            gemma_failed += failed(recent, affs[gemma])
        for name, index in (("code_choice", baseline.i),):
            compared[name].append(index == gemma)
            failed_picks[name] += failed(recent, affs[index])
        if len(affs) == 1:
            index, elapsed = 0, None
        else:
            criteria = {f"a{i}": json.dumps(action, ensure_ascii=False) for i, action in enumerate(affs)}
            state = {"directive": row.get("task", {}), "observation": row.get("view", "")}
            questions = {"action": {"type": "choice", "instructions":
                "Which currently valid action best advances the directive from this observation?",
                "criteria": criteria}}
            start = time.perf_counter()
            answer = model.system_one(state, questions, independent=True, layout="state_first")
            elapsed = (time.perf_counter() - start) * 1000
            choice = answer.get("answers", {}).get("action", {}).get("choice") if isinstance(answer, dict) else None
            index = int(choice[1:]) if isinstance(choice, str) and choice.startswith("a") and choice[1:].isdigit() else -1
            if not 0 <= index < len(affs):
                index = -1
            latencies.append(elapsed)
        compared["decider_2b"].append(index == gemma)
        failed_picks["decider_2b"] += index >= 0 and failed(recent, affs[index])
    summary = {name: {"agreement_with_gemma": sum(values) / len(values) if values else None,
                      "share_already_failed": failed_picks[name] / len(values) if values else None,
                      "rows": len(values)} for name, values in compared.items()}
    summary["gemma"] = {"share_already_failed": gemma_failed / gemma_picks if gemma_picks else None,
                        "rows": gemma_picks}
    summary["decider_2b"]["p50_latency_ms"] = statistics.median(latencies) if latencies else None
    summary["model"] = {"id": MODEL, "revision": REVISION, "device": "cuda", "dtype": "bfloat16"}
    (root / "chooser_comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: compare_chooser.py runs/<id>")
    run(sys.argv[1])

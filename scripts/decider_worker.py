"""Persistent JSON-lines worker for local Decider judgments."""
import contextlib, json, sys


def main():
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from decider.infer import Decider
        from huggingface_hub import snapshot_download
        model = Decider(snapshot_download("Mapika/decider-2b", revision="b37f7e1ba3fbc9238004cf531fabbee2619973fd", local_files_only=True),
                        device="cuda", dtype=torch.bfloat16)
        model.system_one({"x": 1}, {"q": {"type": "noul", "instructions": "warm up?"}})
        if model.eng is not None:  # one question per request -> batch 1; capture its length buckets now, not mid-game
            from decider.engine import T_BUCKETS
            model.eng.warmup(tuple((1, t) for t in T_BUCKETS if t <= 1024))  # ponytail: ~400-600-token requests today; longer ones compile on first use
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr): answer = model.system_one(
                request["state"], request["questions"], independent=True, layout="state_first")
            print(json.dumps(answer), flush=True)
        except Exception as error: print(json.dumps({"error": str(error)}), flush=True)


if __name__ == "__main__":
    try: main()
    except Exception as error: print(json.dumps({"error": str(error)}), flush=True)

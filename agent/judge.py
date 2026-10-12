import json, os, queue, subprocess, threading; from pathlib import Path
class Judge:
    # Startup (GPU load + compile, ~30s measured) gets its own window; per-call answers take ~0.1s.
    def __init__(self, timeout_s=10, startup_s=180, log=None): self.timeout_s, self.startup_s, self.log, self.process, self.broken = timeout_s, startup_s, log, None, False
    def _line(self, timeout):
        result, stdout = queue.Queue(1), self.process.stdout
        threading.Thread(target=lambda: result.put(stdout.readline()), daemon=True).start(); return json.loads(result.get(timeout=timeout))
    def start(self):
        if self.process is not None and self.process.poll() is None: return
        root = Path(__file__).resolve().parents[1]
        with open(self.log, "a") if self.log else open(os.devnull, "w") as err:
            self.process = subprocess.Popen([str(root / ".venv-decider/Scripts/python.exe"), "-u", str(root / "scripts/decider_worker.py")], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True)
        try: ready = self._line(self.startup_s)
        except Exception: ready = None
        if ready != {"ready": True}: self.broken = True; raise RuntimeError(f"decider worker did not start: {ready}")
    def rank(self, state, questions):
        if self.broken: return None
        try:
            self.start(); self.process.stdin.write(json.dumps({"state": state, "questions": questions}) + "\n"); self.process.stdin.flush()
            answer = self._line(self.timeout_s)
            if isinstance(answer, dict) and "error" not in answer: return answer
        except Exception: pass
        process, self.process = self.process, None
        try: process.kill(); process.wait(timeout=1)
        except (AttributeError, OSError, subprocess.TimeoutExpired): pass
        return None

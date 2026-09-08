"""Always-on-top operator popup for the collect loop.

    python -m loop ui
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from loop.config import DEFAULT_CLOUD_MODEL, DEFAULT_MODEL, project_root
from loop.live import clear_stop, list_runs, publish, read, request_stop, tail_text


_BG = "#1b1b1b"
_FG = "#e8e8e8"
_MUTED = "#9a9a9a"
_PANEL = "#242424"
_ACCENT = "#6aa6ff"
_MONO = ("Consolas", 9)
_UI = ("Segoe UI", 9)
_UI_B = ("Segoe UI", 10, "bold")


def _fmt_metrics(metrics: object) -> str:
    if not isinstance(metrics, dict) or not metrics:
        return "—"
    parts: list[str] = []
    for key in ("health_frac", "stamina_frac", "runes", "fail_screen"):
        if key in metrics and metrics[key] is not None:
            value = metrics[key]
            if isinstance(value, float):
                parts.append(f"{key}={value:.2f}")
            else:
                parts.append(f"{key}={value}")
    return "  ".join(parts) if parts else "—"


class OperatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Loop operator")
        self.root.geometry("560x640+40+40")
        self.root.minsize(480, 560)
        self.root.configure(bg=_BG)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.proc: subprocess.Popen[str] | None = None
        self.log_q: queue.Queue[str] = queue.Queue()
        self._selected_run: Path | None = None
        self._build()
        self._tick()

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        self.phase = tk.StringVar(value="IDLE")
        self.meta = tk.StringVar(value="no job")
        self.thought = tk.StringVar(value="—")
        self.action = tk.StringVar(value="—")
        self.metrics = tk.StringVar(value="—")
        self.pin = tk.BooleanVar(value=True)

        head = tk.Frame(self.root, bg=_BG)
        head.pack(fill="x", **pad)
        tk.Label(head, textvariable=self.phase, font=_UI_B, fg=_ACCENT, bg=_BG).pack(side="left")
        tk.Checkbutton(
            head,
            text="on top",
            variable=self.pin,
            command=self._pin,
            fg=_FG,
            bg=_BG,
            selectcolor=_PANEL,
            activebackground=_BG,
            activeforeground=_FG,
        ).pack(side="right")
        tk.Label(head, textvariable=self.meta, font=_UI, fg=_MUTED, bg=_BG).pack(side="right", padx=8)

        for label, var in (
            ("thought", self.thought),
            ("action", self.action),
            ("hud", self.metrics),
        ):
            row = tk.Frame(self.root, bg=_PANEL)
            row.pack(fill="x", padx=10, pady=2)
            tk.Label(row, text=label, width=8, anchor="w", fg=_MUTED, bg=_PANEL, font=_UI).pack(
                side="left", padx=6, pady=4
            )
            tk.Label(
                row,
                textvariable=var,
                anchor="w",
                justify="left",
                wraplength=460,
                fg=_FG,
                bg=_PANEL,
                font=_MONO,
            ).pack(side="left", fill="x", expand=True, padx=4, pady=4)

        form = tk.LabelFrame(self.root, text="collect", fg=_FG, bg=_BG, font=_UI)
        form.pack(fill="x", padx=10, pady=6)
        self.fixture = tk.StringVar(value="fake")
        self.arm = tk.StringVar(value="ratchet")
        self.lives = tk.StringVar(value="10")
        self.max_steps = tk.StringVar(value="32")
        self.skip_models = tk.BooleanVar(value=True)
        self.cloud = tk.BooleanVar(value=False)
        grid = tk.Frame(form, bg=_BG)
        grid.pack(fill="x", padx=6, pady=4)
        self._combo(grid, 0, "fixture", self.fixture, ("fake", "elden", "sts2"))
        self._combo(grid, 2, "arm", self.arm, ("no_skill", "dump_all", "ratchet"))
        self.fixture.trace_add("write", self._on_fixture)
        self._entry(grid, 4, "lives", self.lives, 6)
        self._entry(grid, 6, "steps", self.max_steps, 6)
        checks = tk.Frame(form, bg=_BG)
        checks.pack(anchor="w", padx=8, fill="x")
        tk.Checkbutton(
            checks,
            text="skip-models",
            variable=self.skip_models,
            fg=_FG,
            bg=_BG,
            selectcolor=_PANEL,
            activebackground=_BG,
            activeforeground=_FG,
        ).pack(side="left")
        tk.Checkbutton(
            checks,
            text=f"cloud LLM1 ({DEFAULT_CLOUD_MODEL})",
            variable=self.cloud,
            fg=_FG,
            bg=_BG,
            selectcolor=_PANEL,
            activebackground=_BG,
            activeforeground=_FG,
        ).pack(side="left", padx=12)

        btns = tk.Frame(self.root, bg=_BG)
        btns.pack(fill="x", padx=10, pady=4)
        for text, cmd in (
            ("Start collect", self.start_collect),
            ("Stop", self.stop_job),
            ("Perceive", self.start_perceive),
            ("Curate", self.start_curate),
            ("Skills", self.start_status),
            ("Analyze", self.start_analyze),
        ):
            tk.Button(btns, text=text, command=cmd, font=_UI).pack(side="left", padx=2)

        mid = tk.Frame(self.root, bg=_BG)
        mid.pack(fill="both", expand=True, padx=10, pady=4)
        left = tk.Frame(mid, bg=_BG)
        left.pack(side="left", fill="y")
        tk.Label(left, text="runs", fg=_MUTED, bg=_BG, font=_UI).pack(anchor="w")
        self.runs = tk.Listbox(
            left, height=8, width=28, font=_MONO, bg=_PANEL, fg=_FG, selectbackground="#3a3a3a"
        )
        self.runs.pack(fill="y", expand=True)
        self.runs.bind("<<ListboxSelect>>", self._on_run)
        tk.Button(left, text="Refresh", command=self.refresh_runs, font=_UI).pack(fill="x", pady=4)

        right = tk.Frame(mid, bg=_BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(right, text="log", fg=_MUTED, bg=_BG, font=_UI).pack(anchor="w")
        self.log = tk.Text(
            right, height=12, wrap="word", font=_MONO, bg=_PANEL, fg=_FG, insertbackground=_FG
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self.refresh_runs()

    def _combo(self, parent: tk.Frame, col: int, label: str, var: tk.StringVar, values: tuple[str, ...]) -> None:
        tk.Label(parent, text=label, fg=_MUTED, bg=_BG, font=_UI).grid(row=0, column=col, sticky="w", padx=4)
        ttk.Combobox(parent, textvariable=var, values=values, width=12, state="readonly").grid(
            row=0, column=col + 1, padx=4
        )

    def _entry(self, parent: tk.Frame, col: int, label: str, var: tk.StringVar, width: int) -> None:
        tk.Label(parent, text=label, fg=_MUTED, bg=_BG, font=_UI).grid(row=0, column=col, sticky="w", padx=4)
        tk.Entry(parent, textvariable=var, width=width, font=_MONO).grid(row=0, column=col + 1, padx=4)

    def _on_fixture(self, *_: object) -> None:
        if self.fixture.get() == "sts2":
            self.arm.set("dump_all")

    def _pin(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.pin.get()))
        except tk.TclError:
            pass

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        if not text.endswith("\n"):
            self.log.insert("end", "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start_collect(self) -> None:
        argv = [
            "collect",
            "--fixture",
            self.fixture.get(),
            "--arm",
            self.arm.get(),
            "--lives",
            self.lives.get() or "1",
            "--no-learn",
            "--model",
            DEFAULT_MODEL,
        ]
        if self.fixture.get() == "sts2":
            argv.append("--until-fail")
            argv.extend(["--architecture", "two_stage"])
        else:
            argv.extend(
                [
                    "--max-steps",
                    self.max_steps.get() or "32",
                    "--timeout-s",
                    "600",
                ]
            )
        if self.skip_models.get():
            argv.append("--skip-models")
        if self.cloud.get():
            argv.append("--cloud")
        if self.fixture.get() == "fake":
            argv.extend(["--countdown", "0"])
        self._spawn(argv, job="collect")

    def start_perceive(self) -> None:
        argv = ["perceive", "--fixture", self.fixture.get()]
        if self.skip_models.get():
            argv.append("--skip-models")
        self._spawn(argv, job="perceive")

    def start_curate(self) -> None:
        argv = ["curate", "--fixture", self.fixture.get()]
        if self.skip_models.get():
            argv.append("--skip-models")
        self._spawn(argv, job="curate")

    def start_status(self) -> None:
        self._spawn(["status", "--fixture", self.fixture.get()], job="status")

    def start_analyze(self) -> None:
        self._spawn(["analyze", "--fixture", self.fixture.get()], job="analyze")

    def stop_job(self) -> None:
        request_stop()
        publish(phase="stopping", message="stop requested")
        self._append("stop requested")
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return

        def _kill() -> None:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.terminate()
                self.log_q.put("[killed]\n")

        threading.Thread(target=_kill, daemon=True).start()

    def _spawn(self, argv: list[str], *, job: str) -> None:
        self._spawn_cmd([sys.executable, "-u", "-m", "loop", *argv], job=job)

    def _spawn_cmd(self, cmd: list[str], *, job: str) -> None:
        if self._busy():
            self._append("already running — stop first")
            return
        clear_stop()
        publish(job=job, phase="starting", message=" ".join(cmd[-6:]), thought="", action="")
        self._append("> " + " ".join(cmd))
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(project_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            creationflags=flags,
        )
        publish(pid=self.proc.pid)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log_q.put(line)
        code = proc.wait()
        self.log_q.put(f"[exit {code}]\n")
        self.log_q.put("__REFRESH_RUNS__\n")

    def refresh_runs(self) -> None:
        self.runs.delete(0, "end")
        for path in list_runs():
            self.runs.insert("end", path.name)

    def _on_run(self, _evt: object = None) -> None:
        sel = self.runs.curselection()
        if not sel:
            return
        name = self.runs.get(sel[0])
        path = project_root() / "runs" / name
        self._selected_run = path
        chunks: list[str] = [f"# {name}"]
        summary = path / "summary.json"
        if summary.exists():
            chunks.append(summary.read_text(encoding="utf-8", errors="replace"))
        turns = tail_text(path / "turns.jsonl", lines=12)
        if turns:
            pretty: list[str] = []
            for line in turns.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    pretty.append(line)
                    continue
                step = row.get("step_id") or row.get("turn")
                thought = str(row.get("thought") or row.get("reasoning") or "")[:160]
                action = row.get("action")
                name_a = ""
                if isinstance(action, dict):
                    name_a = str(action.get("name") or "")
                pretty.append(f"step {step}  {name_a}  {thought}")
            chunks.append("\n".join(pretty))
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(chunks) + "\n")
        self.log.configure(state="disabled")

    def _tick(self) -> None:
        while True:
            try:
                line = self.log_q.get_nowait()
            except queue.Empty:
                break
            if line == "__REFRESH_RUNS__\n":
                self.refresh_runs()
                continue
            self._append(line.rstrip("\n"))
        status = read()
        phase = str(status.get("phase") or "idle").upper()
        job = str(status.get("job") or "idle")
        self.phase.set(f"{job}  {phase}")
        bits = []
        if status.get("fixture"):
            bits.append(str(status["fixture"]))
        if status.get("arm"):
            bits.append(str(status["arm"]))
        ep = status.get("episode")
        lives = status.get("lives")
        if ep:
            bits.append(f"ep {ep}/{lives or '?'}")
        step = status.get("step")
        if step:
            bits.append(f"step {step}/{status.get('max_steps') or '?'}")
        if status.get("skill_on"):
            bits.append(f"skill {status.get('active_skill_id') or 'on'}")
        self.meta.set("  ".join(bits) or str(status.get("message") or "idle"))
        thought = str(status.get("thought") or status.get("message") or "—")
        self.thought.set(thought[:400] or "—")
        self.action.set(str(status.get("action") or "—"))
        self.metrics.set(_fmt_metrics(status.get("metrics")))
        self.root.after(250, self._tick)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Always-on-top operator popup")
    parser.parse_args(argv)
    root = tk.Tk()
    OperatorApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

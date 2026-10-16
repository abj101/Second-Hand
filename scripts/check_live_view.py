"""Local visible-runtime check with a delayed fake model; never makes model requests."""
from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import statistics
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import sdl2

from agent.env_pyboy import LiveEnvClosed, LivePyBoyHost, PyBoyEnv
from agent.loop import Loop
from agent.memory import Memory
from agent.model import ModelCancelled, Ollama
from scripts.run_agent import _checkpoint, run_continuous
from scripts.truth_pokered import read
from scripts.watch_run import Follow, FramePublisher, LIVE_MAX, handler, read_live_frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--model-delay", type=float, default=5)
    parser.add_argument("--output", type=Path, default=Path(".tmp/live-view-check"))
    parser.add_argument("--state", type=Path)
    args = parser.parse_args(argv)
    if args.model_delay <= 0 or args.seconds < args.model_delay + 6:
        parser.error("seconds must exceed a positive model-delay by at least 6 seconds")
    run = args.output.resolve() / f"probe-{uuid.uuid4().hex[:8]}"
    run.mkdir(parents=True)
    state = args.state or Path(".tmp/e2/red_2f.state")
    if not state.is_file():
        state = run / "seed.state"
        seed = PyBoyEnv()
        try:
            seed.tick(5000); seed.press("START"); seed.tick(120); seed.save(str(state))
        finally: seed.close()

    host = LivePyBoyHost(state=str(state))
    publisher = FramePublisher(run)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Follow(run, run.parent)))
    threading.Thread(target=server.serve_forever, name="probe-http", daemon=True).start()
    address, port = server.server_address
    print(json.dumps({"url": f"http://{address}:{port}/", "run": str(run), "seconds": args.seconds}), flush=True)

    started = time.monotonic()
    samples, http_samples, delays, inputs, rows, saves, native = [], [], [], [], [], [], []
    monitoring = threading.Event()

    def monitor():
        while not monitoring.is_set():
            connection = http.client.HTTPConnection(address, port, timeout=1)
            try:
                connection.request("GET", "/live-frame"); response = connection.getresponse()
                response.read()
                if response.status == 200:
                    http_samples.append({"at": time.monotonic(), "seq": int(response.getheader("X-Frame-Seq")),
                                         "age_ms": float(response.getheader("X-Frame-Age-Ms"))})
            except (OSError, http.client.HTTPException): pass
            finally: connection.close()
            monitoring.wait(.033)
    monitor_thread = threading.Thread(target=monitor, name="probe-client", daemon=True)
    monitor_thread.start()

    class ProbePublisher:
        def __init__(self): self.minimized = self.restored = self.closed = False
        def publish(self, frame):
            samples.append((frame.frame_seq, frame.captured_monotonic))
            publisher.publish(frame)
            elapsed = time.monotonic() - started
            window = sdl2.SDL_GetWindowFromID(1)
            if window and not native:
                native.append({"event": "shown", "seq": frame.frame_seq, "flags": int(sdl2.SDL_GetWindowFlags(window))})
            if window and elapsed >= args.seconds - 4 and not self.minimized:
                self.minimized = True; sdl2.SDL_MinimizeWindow(window)
                native.append({"event": "minimize", "seq": frame.frame_seq, "flags": int(sdl2.SDL_GetWindowFlags(window))})
            if window and elapsed >= args.seconds - 3 and not self.restored:
                self.restored = True; sdl2.SDL_RestoreWindow(window)
                native.append({"event": "restore", "seq": frame.frame_seq, "flags": int(sdl2.SDL_GetWindowFlags(window))})
            if elapsed >= args.seconds and not self.closed:
                self.closed = True
                event = sdl2.SDL_Event(); event.type = sdl2.SDL_QUIT
                sdl2.SDL_PushEvent(ctypes.byref(event))
                native.append({"event": "quit", "seq": frame.frame_seq})
        def close(self, **kwargs): publisher.close(**kwargs)

    def work(env, cancelled):
        model = Ollama("offline-runtime-probe", cancel_event=cancelled)
        delayed = False
        def fake_post(payload):
            nonlocal delayed
            if not delayed:
                delayed = True; begin = time.monotonic()
                cancelled.wait(args.model_delay)
                delays.append((begin, time.monotonic()))
            properties = payload["format"].get("properties", {}) if isinstance(payload["format"], dict) else {}
            if "screen" in properties: body = {"screen": "menu", "text": [], "entities": []}
            elif "action" in properties:
                body = {"place": "", "action": {"buttons": ["B"]}, "text": None, "expect": "screen_changes", "why": "offline clock probe"}
            else: body = {"quests": [], "beliefs": [], "facts": [], "note": "", "why": "offline clock probe"}
            return httpx.Response(200, json={"message": {"content": json.dumps(body)}})
        model._post = fake_post
        env.tile_px = 16
        loop = Loop(env, model, Memory(run / "memory"), "pokemon_red")
        try:
            env.press("START", frames=2, on_frame=lambda image: inputs.append(image.info["frame_seq"]))
            for _ in range(2):
                row = loop.step(); rows.append(row["step"])
                if delayed: break
            truth = read(env)
            (run / "truth-probe.json").write_text(json.dumps(truth))
            begin = time.monotonic(); _checkpoint(env, loop, run)
            saves.append({"elapsed_ms": (time.monotonic() - begin) * 1000})
            # Deliberately slow off-owner persistence and a retry-like wait.
            cancelled.wait(.5)
            cancelled.wait(max(0, args.seconds - (time.monotonic() - started) + 1))
            return 0
        except (ModelCancelled, LiveEnvClosed): return 0

    exit_code = None
    try:
        exit_code = run_continuous(host, ProbePublisher(), work)
    finally:
        monitoring.set(); monitor_thread.join(2)
        server.shutdown(); server.server_close()

    in_delay = [(seq, at) for seq, at in samples if delays and delays[0][0] <= at <= delays[0][1]]
    fps = (in_delay[-1][0] - in_delay[0][0]) / (in_delay[-1][1] - in_delay[0][1]) if len(in_delay) > 1 else 0
    gaps = [(b[1] - a[1]) * 1000 for a, b in zip(samples, samples[1:]) if a[1] >= started + 1]
    # Keep post-close staleness visible in raw samples, but measure delivery while the source was alive.
    ages = [sample["age_ms"] for sample in http_samples if samples and sample["at"] <= samples[-1][1]]
    artifact = read_live_frame(run)
    native_events = {event["event"] for event in native}
    checks = {"worker_exit": exit_code == 0, "delayed_model_called": bool(delays),
              "fps_during_model_wait": 54 <= fps <= 66, "max_source_gap_ms": bool(gaps) and max(gaps) <= 250,
              "http_source_age_ms": bool(ages) and max(ages) < 250,
              "input_frames_exact": len(inputs) == 10 and inputs == list(range(inputs[0], inputs[0] + 10)),
              "checkpoint_written": bool(saves), "native_window_lifecycle": {"shown", "minimize", "restore", "quit"} <= native_events,
              "bounded_artifact": artifact is not None and (run / "live-frame.bin").stat().st_size <= LIVE_MAX and not (run / "stream.bin").exists()}
    report = {"checks": checks, "source_fps_during_model_wait": fps, "max_source_gap_ms": max(gaps, default=None),
              "http_frame_age_median_ms": statistics.median(ages) if ages else None,
              "http_frame_age_max_ms": max(ages, default=None), "source_frame_count": len(samples),
              "http_post_close_age_max_ms": max((sample["age_ms"] for sample in http_samples), default=None),
              "http_frame_count": len(http_samples), "model_delay_windows": delays, "input_frame_sequences": inputs,
              "loop_steps": rows, "checkpoint": saves, "native_events": native,
              "artifact_bytes": (run / "live-frame.bin").stat().st_size if artifact else None,
              "buffer_limits": {"idle_frames": 120, "active_press_frames": 608, "publisher_pending": 1, "artifact_bytes": LIVE_MAX},
              "source_samples": samples, "http_samples": http_samples, "run": str(run),
              "browser_validation": "separate real-browser check required"}
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key not in ("source_samples", "http_samples")}), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__": raise SystemExit(main())

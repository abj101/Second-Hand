import http.client, json, os, shutil, subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import time
from PIL import Image
import pytest
from scripts.watch_run import (LIVE_MAX, LIVE_PNG_MAX, Follow, FramePublisher, RunState,
                               handler, read_live_frame, stream_writer)


def test_stream_records_every_frame_with_repeats_empty(tmp_path):
    write = stream_writer(tmp_path / "stream.bin")
    for color in (0, 0, 255): write(Image.new("RGB", (160, 144), (color, 0, 0)))
    write(Image.new("RGB", (160, 144), (255, 0, 0)))
    at, size, data = RunState(tmp_path).stream(0); lens, i = [], 0
    while i < len(data): n = int.from_bytes(data[i:i+4], "little"); lens.append(n); i += 4 + n
    assert at == 0 and size == len(data) and len(lens) == 4 and lens[0] and lens[1] == 0 and lens[2] and lens[3] == 0
    assert RunState(tmp_path).stream(-1) == (4 + lens[0] + 4, size, data[4 + lens[0] + 4:])  # join live: newest real frame, then its repeat
    assert RunState(tmp_path).stream(size) == (size, size, b"") and RunState(tmp_path / "none").stream(0) == (0, 0, b"")


def test_follow_switches_to_newest_trace_unless_pinned(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for i, run in enumerate((old, new)):
        run.mkdir(); (run / "trace.jsonl").write_text("", encoding="utf-8"); os.utime(run / "trace.jsonl", (i, i))
    assert Follow(None, tmp_path).current().run == new
    os.utime(old / "trace.jsonl", (9, 9))
    assert Follow(None, tmp_path).current().run == old
    assert Follow(new, tmp_path).current().run == new


def test_state_tails_trace_dedupes_tiles_and_guards_frames(tmp_path):
    (tmp_path / "memory").mkdir(); (tmp_path / "frames").mkdir()
    (tmp_path / "trace.jsonl").write_text(json.dumps({"step": 1}) + "\n" + json.dumps({"step": 2}) + "\n" + '{"step": 3', encoding="utf-8")
    tile = {"id": "p@0,0", "place": "p", "x": 0, "y": 0}
    (tmp_path / "memory" / "tiles.jsonl").write_text(json.dumps({**tile, "floor": None}) + "\n" + json.dumps({**tile, "floor": True}) + "\n", encoding="utf-8")
    (tmp_path / "presses.jsonl").write_text(json.dumps({"step": 2, "keys": ["LEFT", "A"]}) + "\n", encoding="utf-8")
    (tmp_path / "calls.jsonl").write_text("".join(json.dumps({"step": 2, "role": r}) + "\n" for r in ("perceive", "act")), encoding="utf-8")
    run = RunState(tmp_path)
    state = run.state(0)
    assert [r["step"] for r in state["rows"]] == [1, 2]  # partial row 3 skipped
    assert state["tiles"] == [{**tile, "floor": True}] and state["presses"] == {"2": ["LEFT", "A"]}
    assert [c["role"] for c in state["call_rows"]["2"]] == ["perceive", "act"] and state["calls"] == 2
    with (tmp_path / "trace.jsonl").open("a", encoding="utf-8") as f: f.write("}\n")
    assert [r["step"] for r in run.state(2)["rows"]] == [3]
    (tmp_path / "frames" / "000001.png").write_bytes(b"png")
    assert run.frame("000001.png") == b"png" and run.frame("../trace.jsonl") is None
    (tmp_path / "frames" / "000001_after.png").write_bytes(b"after")
    assert run.frame("000001_after.png") == b"after" and run.state(0)["pending"] is None
    (tmp_path / "live.json").write_text(json.dumps({"step": 4}), encoding="utf-8")
    assert run.state(3)["pending"] == {"step": 4}


def _wait_live(run, seq, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        live = read_live_frame(run)
        if live and live[0]["frame_seq"] == seq: return live
        time.sleep(.01)
    raise AssertionError(f"publisher did not publish frame {seq}")


def _snapshot(seq, color, generation="g1", captured=None):
    return SimpleNamespace(generation=generation, frame_seq=seq,
                           captured_monotonic=time.monotonic() if captured is None else captured,
                           image=Image.new("RGB", (160, 144), color))


def test_frame_publisher_is_bounded_latest_slot_and_reuses_unchanged_pixels(tmp_path):
    publisher = FramePublisher(tmp_path)
    try:
        for seq in range(1, 51): publisher.publish(_snapshot(seq, (20, 30, 40)))
        header, png = _wait_live(tmp_path, 50)
        assert header["generation"] == "g1" and len(png) <= LIVE_PNG_MAX
        assert (tmp_path / "live-frame.bin").stat().st_size <= LIVE_MAX
        assert len(list(tmp_path.iterdir())) == 1
        publisher.publish(_snapshot(51, (200, 0, 0), generation="g2"))
        assert _wait_live(tmp_path, 51)[0]["generation"] == "g2"
    finally: publisher.close()
    assert not (tmp_path / ".live-frame.tmp").exists()


def test_live_artifact_rejects_oversize_truncated_and_malformed_records(tmp_path):
    path = tmp_path / "live-frame.bin"
    for record in (b"", b"\xff" * (LIVE_MAX + 1), (2).to_bytes(4, "little") + b"{}",
                   (1).to_bytes(4, "little") + b"{" + b"not png"):
        path.write_bytes(record)
        assert read_live_frame(tmp_path) is None
    path.write_bytes((4097).to_bytes(4, "little") + b" " * 4097)
    assert read_live_frame(tmp_path) is None


def test_frame_publisher_retries_windows_replace_contention_without_losing_previous(monkeypatch, tmp_path):
    publisher = FramePublisher(tmp_path)
    try:
        publisher.publish(_snapshot(1, (1, 1, 1))); _wait_live(tmp_path, 1)
        original, denied = Path.replace, __import__("threading").Event()
        def replace(path, target):
            if path.name == ".live-frame.tmp" and target.name == "live-frame.bin" and not denied.is_set():
                denied.set(); raise PermissionError("reader holds destination")
            return original(path, target)
        monkeypatch.setattr(Path, "replace", replace)
        publisher.publish(_snapshot(2, (2, 2, 2)))
        assert denied.wait(1) and read_live_frame(tmp_path)[0]["frame_seq"] == 1
        assert _wait_live(tmp_path, 2)[0]["frame_seq"] == 2
        assert len(list(tmp_path.iterdir())) <= 2
    finally: publisher.close()


def test_follow_detects_live_startup_and_run_state_resets_on_generation_restart(tmp_path):
    run = tmp_path / "fresh"; run.mkdir()
    publisher = FramePublisher(run)
    try:
        publisher.publish(_snapshot(10, (0, 0, 0), "first")); _wait_live(run, 10)
        assert Follow(None, tmp_path).current().run == run
        (run / "trace.jsonl").write_text(json.dumps({"step": 8}) + "\n", encoding="utf-8")
        state = RunState(run)
        assert [r["step"] for r in state.state(0)["rows"]] == [8]
        publisher.publish(_snapshot(1, (255, 0, 0), "second")); _wait_live(run, 1)
        (run / "trace.jsonl").write_text(json.dumps({"step": 1}) + "\n" + json.dumps({"step": 2}) + "\n", encoding="utf-8")
        assert [r["step"] for r in state.state(8)["rows"]] == [1, 2]
    finally: publisher.close()


def test_follow_first_discovery_ranks_source_capture_time_not_artifact_mtime(tmp_path):
    newer, older = tmp_path / "newer", tmp_path / "older"
    newer.mkdir(); older.mkdir()
    publishers = [FramePublisher(newer), FramePublisher(older)]
    try:
        publishers[0].publish(_snapshot(4, (0, 0, 0), "fresh", time.monotonic() - 60))
        _wait_live(newer, 4)
        publishers[1].publish(_snapshot(90, (1, 1, 1), "stale", time.monotonic() - 3600))
        _wait_live(older, 90)  # the stale run's artifact has the newer disk mtime
        assert Follow(None, tmp_path).current().run == newer
    finally:
        for publisher in publishers: publisher.close()


def test_live_state_uses_source_age_not_publisher_heartbeat(tmp_path):
    publisher = FramePublisher(tmp_path)
    try:
        publisher.publish(_snapshot(1, (1, 2, 3), captured=time.monotonic() - 2))
        _wait_live(tmp_path, 1)
        state = RunState(tmp_path).state(0)
        assert state["publisher_age_ms"] < 1000 and state["frame_age_ms"] >= 2000
        assert state["live"] is False and state["frame_live"] is False
    finally: publisher.close()


def test_live_frame_endpoint_sends_source_metadata_and_explicit_unavailable(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Follow(tmp_path, tmp_path)))
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        conn.request("GET", "/live-frame"); missing = conn.getresponse()
        assert missing.status == 503
        missing.read()
        publisher = FramePublisher(tmp_path)
        try:
            publisher.publish(_snapshot(7, (9, 8, 7), "endpoint")); _wait_live(tmp_path, 7)
            conn.request("GET", "/live-frame"); response = conn.getresponse(); data = response.read()
            assert response.status == 200 and response.getheader("Cache-Control") == "no-store"
            assert response.getheader("X-Run") == tmp_path.name
            assert response.getheader("X-Generation") == "endpoint" and response.getheader("X-Frame-Seq") == "7"
            assert float(response.getheader("X-Captured-Monotonic")) > 0
            assert int(response.getheader("X-Frame-Age-Ms")) >= 0 and Image.open(__import__("io").BytesIO(data)).size == (160, 144)
        finally: publisher.close()
    finally:
        conn.close(); server.shutdown(); server.server_close(); thread.join(1)


def test_browser_frame_loop_rejects_late_epoch_and_closes_bitmaps():
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not os.path.exists(node): pytest.skip("Node.js is unavailable")
    from scripts.watch_run import PAGE
    start, end = PAGE.index("let frameEpoch="), PAGE.index('document.addEventListener("visibilitychange"')
    production = PAGE[start:end]
    harness = r'''
const assert = require("node:assert/strict");
const live = {dataset:{}, getContext:()=>({drawImage(bitmap){drawn.push(bitmap.seq)}, clearRect(){drawn.length=0}})};
const fstep = {textContent:""}, els={live,fstep};
function $(id){return els[id]}
let last={}, after=0; globalThis.document={hidden:false,addEventListener(){}};
let drawn=[], closed=[], fetchCount=0, activeFetch=0, maxFetch=0, activeBitmap=0, maxBitmap=0, releaseDecode;
const fetches=[], rafs=[], timers=[];
const deferred=()=>{let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve}};
globalThis.fetch=()=>{fetchCount++; activeFetch++; maxFetch=Math.max(maxFetch,activeFetch); const d=fetches.shift(); return d.promise.finally(()=>activeFetch--)};
globalThis.createImageBitmap=async blob=>{
  activeBitmap++; maxBitmap=Math.max(maxBitmap,activeBitmap);
  if(blob.delay) await new Promise(resolve=>releaseDecode=resolve);
  return {seq:blob.seq,close(){closed.push(blob.seq);activeBitmap--}};
};
globalThis.requestAnimationFrame=fn=>rafs.push(fn);
globalThis.setTimeout=fn=>timers.push(fn);
function response(run,generation,seq,{delay=false}={}){
  const headers={"X-Run":run,"X-Generation":generation,"X-Frame-Seq":String(seq),"X-Captured-Monotonic":"10","X-Frame-Age-Ms":"7"};
  return {ok:true,headers:{get:k=>headers[k]||null},blob:async()=>({seq,delay})};
}
(async()=>{
'''
    tail = r'''
  last={run:"old",generation:"g1"};
  const oldFetch=deferred(); fetches.push(oldFetch);
  const oldTask=pullFrame(); await Promise.resolve(); assert.equal(fetchCount,1);
  last={run:"new",generation:"g2"}; resetFrames();
  oldFetch.resolve(response("old","g1",1,{delay:true}));
  await new Promise(setImmediate); assert.equal(activeBitmap,1);
  pullFrame(); pullFrame(); assert.equal(fetchCount,1); // old decode remains single-flight
  const newFetch=deferred(); fetches.push(newFetch); releaseDecode();
  await oldTask; await new Promise(setImmediate); assert.equal(fetchCount,2);
  newFetch.resolve(response("new","g2",2));
  await new Promise(setImmediate);
  assert.deepEqual(drawn,[2]); assert.deepEqual(closed,[1,2]);
  assert.equal(maxBitmap,1); assert.equal(maxFetch,1);
  assert.equal(live.dataset.frameSeq,"2"); assert.equal(live.dataset.capturedMonotonic,"10");
  assert.equal(fstep.textContent,"LIVE / FRAME 2 / 7 MS");
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run([node, "-e", production + harness + tail], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_keeps_last_frame_on_fetch_failure_and_visibility_change():
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not os.path.exists(node): pytest.skip("Node.js is unavailable")
    from scripts.watch_run import PAGE
    start = PAGE.index("let frameEpoch=")
    end = PAGE.index('document.addEventListener("visibilitychange"')
    production = PAGE[start:PAGE.index("\n", end)]
    harness = r'''
const assert = require("node:assert/strict");
let drawn=[], timers=[], rafs=[], fetches=[], listener, fetchCount=0;
const live={dataset:{},getContext:()=>({drawImage(b){drawn.push(b.seq)},clearRect(){drawn.length=0}})};
const els={live,fstep:{textContent:""}};
function $(id){return els[id]}
let last={run:"run",generation:"g1"}, after=0;
globalThis.document={hidden:false,addEventListener(event,fn){listener=fn}};
globalThis.fetch=async()=>{fetchCount++;return fetches.shift()};
globalThis.createImageBitmap=async blob=>({seq:blob.seq,close(){}});
globalThis.requestAnimationFrame=fn=>rafs.push(fn);
globalThis.setTimeout=fn=>timers.push(fn);
function response(seq){
  const h={"X-Run":"run","X-Generation":"g1","X-Frame-Seq":String(seq)};
  return {ok:true,headers:{get:k=>h[k]},blob:async()=>({seq})};
}
'''
    tail = r'''
(async()=>{
  fetches.push(response(2)); await pullFrame();
  assert.deepEqual(drawn,[2]); assert.equal(live.dataset.frameSeq,"2");
  fetches.push({ok:false}); await pullFrame();
  assert.deepEqual(drawn,[2]); assert.equal(live.dataset.frameSeq,"2");
  assert.equal(timers.length,1); assert.equal(frameConnected,false);
  fetches.push(response(3)); await timers.shift()();
  assert.deepEqual(drawn,[2,3]); assert.equal(live.dataset.frameSeq,"3");
  document.hidden=true; listener();
  assert.deepEqual(drawn,[2,3]); assert.equal(live.dataset.frameSeq,"3");
  fetches.push(response(4)); document.hidden=false; listener();
  await new Promise(setImmediate);
  assert.equal(fetchCount,4); assert.deepEqual(drawn,[2,3,4]);
  assert.equal(live.dataset.frameSeq,"4");
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run([node, "-e", harness + production + tail], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr

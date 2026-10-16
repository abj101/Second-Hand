"""Read-only live viewer for an agent run: decision feed, goals, and spatial map.

    python scripts/watch_run.py                      # follow whichever run under runs/ was written last
    python scripts/watch_run.py --run runs/m1_gp     # pin one run
Keep the page open: when a new run starts writing trace.jsonl, the page switches to it.
"""
import argparse, io, json, math, re, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.memory import Memory
from PIL import Image

FRAME = re.compile(r"\d{6}(_after\.png|_motion\.gif|\.png)")
STREAM_CHUNK = 4 << 20
LIVE_FILE, LIVE_TEMP = "live-frame.bin", ".live-frame.tmp"
LIVE_HEADER_MAX, LIVE_PNG_MAX, LIVE_MAX = 4096, 256 << 10, 4 + (4096) + (256 << 10)
LIVE_SIZE = (160, 144)


class FramePublisher:
    """Publish the newest duck-typed RGB snapshot through one bounded atomic artifact."""
    def __init__(self, run: Path) -> None:
        self.run = Path(run)
        self.run.mkdir(parents=True, exist_ok=True)
        self.path, self.temp = self.run / LIVE_FILE, self.run / LIVE_TEMP
        self._cv, self._pending = threading.Condition(), None
        self._closed = False
        self._thread = threading.Thread(target=self._work, name="frame-publisher", daemon=True)
        self._thread.start()

    def publish(self, snapshot) -> None:
        """Replace the one pending slot; the caller never encodes or writes pixels."""
        with self._cv:
            if not self._closed:
                self._pending = snapshot
                self._cv.notify()

    def close(self, timeout: float = 1) -> None:
        with self._cv:
            self._closed = True
            self._pending = None
            self._cv.notify_all()
        self._thread.join(timeout)
        if not self._thread.is_alive():
            try: self.temp.unlink()
            except OSError: pass

    def _work(self) -> None:
        last_pixels = last_png = None
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed: return
                snap, self._pending = self._pending, None
            try:
                image = snap.image
                if image.mode != "RGB" or image.size != LIVE_SIZE: continue
                pixels = image.tobytes()
                if pixels != last_pixels:
                    buf = io.BytesIO(); image.save(buf, "PNG", compress_level=1); png = buf.getvalue()
                    if len(png) > LIVE_PNG_MAX: continue
                    last_pixels, last_png = pixels, png
                header = json.dumps({"run": self.run.name, "generation": str(snap.generation),
                                     "frame_seq": int(snap.frame_seq), "captured_monotonic": float(snap.captured_monotonic),
                                     "width": LIVE_SIZE[0], "height": LIVE_SIZE[1],
                                     "published_monotonic": time.monotonic()}, separators=(",", ":")).encode()
                if len(header) > LIVE_HEADER_MAX or not last_png: continue
                record = len(header).to_bytes(4, "little") + header + last_png
                with self.temp.open("wb") as f:
                    f.write(record); f.flush()
                for delay in (0, .025, .05, .1):
                    if delay: time.sleep(delay)
                    try:
                        self.temp.replace(self.path)
                        break
                    except OSError: pass
            except Exception:
                continue


def read_live_frame(run: Path) -> tuple[dict, bytes] | None:
    """Read and validate one coherent, bounded artifact; malformed data is unavailable."""
    try:
        path = Path(run) / LIVE_FILE
        if path.stat().st_size > LIVE_MAX: return None
        with path.open("rb") as f:
            nbytes = f.read(4)
            if len(nbytes) != 4: return None
            n = int.from_bytes(nbytes, "little")
            if not 0 < n <= LIVE_HEADER_MAX: return None
            raw = f.read(n)
            if len(raw) != n: return None
            header = json.loads(raw.decode("utf-8"))
            png = f.read(LIVE_PNG_MAX + 1)
            if not png or len(png) > LIVE_PNG_MAX or f.read(1): return None
        if not isinstance(header, dict) or header.get("width") != LIVE_SIZE[0] or header.get("height") != LIVE_SIZE[1]: return None
        if not isinstance(header.get("run"), str) or not isinstance(header.get("generation"), str): return None
        if type(header.get("frame_seq")) is not int or header["frame_seq"] < 0: return None
        for key in ("captured_monotonic", "published_monotonic"):
            if not isinstance(header.get(key), (int, float)) or not math.isfinite(header[key]): return None
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or image.size != LIVE_SIZE: return None
            image.verify()
        return header, png
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def stream_writer(path: Path):
    """Sink for PyBoyEnv.frame_sink: appends every emulated frame to stream.bin as [u32 len][png]; len 0 = same as the last frame.
    Write errors are swallowed: the viewer must never stop or alter the game."""
    try: file = path.open("wb", buffering=0)  # truncate, not unlink: Windows refuses to delete a file the viewer has open
    except OSError: return None
    last = [None]
    # ponytail: stream.bin grows per changed frame for one invocation (reset on start); rotate if runs get long enough to matter
    def write(image):
        try:
            raw = image.tobytes(); data = b""
            if raw != last[0]: last[0] = raw; buf = io.BytesIO(); image.save(buf, "PNG", compress_level=1); data = buf.getvalue()
            file.write(len(data).to_bytes(4, "little") + data)
        except OSError: pass
    return write


class RunState:
    def __init__(self, run: Path) -> None:
        self.run, self.offset, self.rows, self.notebook, self.generation = Path(run), 0, [], {}, None

    def _tail(self):
        path = self.run / "trace.jsonl"
        if not path.exists(): return
        if path.stat().st_size < self.offset: self.offset, self.rows = 0, []  # --resume truncated the trace
        with path.open("rb") as stream:
            stream.seek(self.offset)
            for line in stream:
                if not line.endswith(b"\n"): break  # partial row still being written
                self.offset += len(line)
                try: self.rows.append(json.loads(line))
                except json.JSONDecodeError: continue

    def state(self, since: int) -> dict:
        live = read_live_frame(self.run)
        header = live[0] if live else None
        if header and header["generation"] != self.generation:
            self.generation, self.offset, self.rows, self.notebook, since = header["generation"], 0, [], {}, 0
        self._tail()
        try: self.notebook = json.loads((self.run / "memory" / "notebook.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): pass  # rewritten mid-run; keep last good copy
        trace, calls = self.run / "trace.jsonl", self.run / "calls.jsonl"
        # ponytail: re-reads all memory jsonl per poll; switch to an offset tail if polls get slow on long runs.
        mem = Memory(self.run / "memory") if (self.run / "memory").exists() else None
        age = max(0.0, (time.monotonic() - header["captured_monotonic"]) * 1000) if header else None
        return {"run": self.run.name, "generation": header["generation"] if header else self.generation,
                "frame_seq": header["frame_seq"] if header else None, "frame_age_ms": age,
                "publisher_age_ms": max(0.0, (time.monotonic() - header["published_monotonic"]) * 1000) if header and "published_monotonic" in header else None,
                "frame_live": age is not None and age < 250, "live": age is not None and age < 250,
                "pending": self._pending(),
                "rows": [r for r in self.rows if int(r.get("step", 0)) > since],
                "presses": self._by_step("presses.jsonl", lambda row: row["keys"]),
                "call_rows": self._by_step("calls.jsonl", list),
                "notebook": self.notebook,
                "places": mem.rows("places") if mem else [], "tiles": mem.rows("tiles") if mem else [],
                "calls": sum(1 for _ in calls.open(encoding="utf-8")) if calls.exists() else 0}

    def _by_step(self, name: str, pick) -> dict:
        """{step: pick(row)} for presses.jsonl, or {step: [rows]} for calls.jsonl (pick=list)."""
        path, out = self.run / name, {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line); step = str(row["step"])
                    if pick is list: out.setdefault(step, []).append(row)
                    else: out[step] = pick(row)
                except (json.JSONDecodeError, KeyError, TypeError): continue
        return out

    def _pending(self) -> dict | None:
        """live.json: the step the agent has decided but not finished acting on (agent/loop.py Loop._live)."""
        try: return json.loads((self.run / "live.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return None

    def frame(self, name: str) -> bytes | None:
        path = self.run / "frames" / name
        return path.read_bytes() if FRAME.fullmatch(name) and path.exists() else None

    def stream(self, start: int) -> tuple[int, int, bytes]:
        """(offset, file size, bytes of stream.bin from offset); start < 0 joins at the newest whole frame, so a fresh page starts live.
        The page keeps only whole records and resets when size < its offset."""
        try:
            with (self.run / "stream.bin").open("rb") as f:
                size = f.seek(0, 2)
                if start < 0:  # ponytail: walks every record header on join; keep a last-frame offset in the writer if joins get slow
                    at = start = 0; f.seek(0)
                    while len(head := f.read(4)) == 4 and (at := at + 4 + (n := int.from_bytes(head, "little"))) <= size:
                        if n: start = at - 4 - n
                        f.seek(at)
                f.seek(min(start, size)); return min(start, size), size, f.read(STREAM_CHUNK)
        except OSError: return 0, 0, b""


def _mtime(path: Path) -> float:
    try: return path.stat().st_mtime
    except OSError: return 0.0  # a new run's boot reset can delete it between glob and stat


class Follow:
    """Pinned run or most recently active run, considering trace and source-frame progress."""
    def __init__(self, run: Path | None, root: Path) -> None:
        self.pinned, self.root, self.states, self.activity, self.observed = run, Path(root), {}, {}, {}
        self._selection, self._rescan_at = None, 0

    def current(self) -> RunState:
        if self.pinned: run = self.pinned
        else:
            mono_now = time.monotonic()
            if self._selection is not None and mono_now < self._rescan_at:
                return self.states.setdefault(self._selection.resolve(), RunState(self._selection))
            candidates, wall_now = set(), time.time()
            if self.root.exists(): candidates.update(p.parent for p in self.root.glob("*/trace.jsonl"))
            if self.root.exists(): candidates.update(p.parent for p in self.root.glob(f"*/{LIVE_FILE}"))
            for path in candidates:
                key, trace = path.resolve(), _mtime(path / "trace.jsonl")
                live = read_live_frame(path)
                marker = (live[0].get("generation"), live[0].get("frame_seq")) if live else None
                old_trace, old_marker = self.observed.get(key, (None, None))
                source_time = (wall_now - max(0, mono_now - live[0]["captured_monotonic"])) if live else 0
                if key not in self.observed: self.activity[key] = max(trace, source_time)
                elif trace != old_trace: self.activity[key] = max(self.activity.get(key, 0), trace)
                if marker and marker != old_marker: self.activity[key] = max(self.activity.get(key, 0), source_time)
                self.observed[key] = trace, marker
            run = max(candidates, key=lambda p: self.activity.get(p.resolve(), 0)) if candidates else self.root
            self._selection, self._rescan_at = run, mono_now + .25
        return self.states.setdefault(run.resolve(), RunState(run))


def handler(follow: Follow):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def _send(self, code, body, kind, headers=()):
            self.send_response(code); self.send_header("Content-Type", kind); self.send_header("Cache-Control", "no-store")
            for k, v in headers: self.send_header(k, v)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/": return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if url.path == "/state":
                query, run_state = parse_qs(url.query), follow.current()
                since = int((query.get("since") or ["0"])[0] or 0) if (query.get("run") or [""])[0] == run_state.run.name else 0
                return self._send(200, json.dumps(run_state.state(since), default=str).encode(), "application/json")
            if url.path == "/stream":
                run_state = follow.current(); at, size, data = run_state.stream(int((parse_qs(url.query).get("from") or ["0"])[0] or 0))
                return self._send(200, data, "application/octet-stream", (("X-Run", run_state.run.name), ("X-From", str(at)), ("X-Size", str(size))))
            if url.path == "/live-frame":
                run_state = follow.current(); live = read_live_frame(run_state.run)
                if live is None: return self._send(503, b"live frame unavailable", "text/plain")
                header, png = live
                age = max(0, int((time.monotonic() - float(header["captured_monotonic"])) * 1000))
                run_name = re.sub(r"[^A-Za-z0-9_.-]", "_", run_state.run.name)
                return self._send(200, png, "image/png", (("X-Run", run_name), ("X-Generation", header["generation"]),
                    ("X-Frame-Seq", str(header["frame_seq"])), ("X-Captured-Monotonic", str(header["captured_monotonic"])),
                    ("X-Frame-Age-Ms", str(age))))
            if url.path.startswith("/frames/") and (data := follow.current().frame(url.path[8:])) is not None:
                return self._send(200, data, "image/gif" if url.path.endswith(".gif") else "image/png")
            self._send(404, b"not found", "text/plain")
    return Handler


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Avatar Run</title>
<style>
/* palette and type copied from loop/adaptive/diagnostics_window.py */
:root{--bg:#080808;--panel:#0d0d0d;--fg:#f2f2f2;--muted:#858585;--dim:#303030;--subtle:#171717}
*{box-sizing:border-box;scrollbar-width:thin;scrollbar-color:var(--dim) transparent}*::-webkit-scrollbar{width:8px;height:8px}*::-webkit-scrollbar-track{background:transparent}*::-webkit-scrollbar-thumb{background:var(--dim);border:2px solid transparent;background-clip:content-box}*::-webkit-scrollbar-thumb:hover{background-color:#555}
body{margin:0;height:100vh;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);color:var(--fg);font:12px/1.45 Consolas,"Courier New",monospace}
header{display:flex;gap:18px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--fg)}
header b{font-size:14px}.muted{color:var(--muted)}
.badge{display:flex;align-items:center;gap:6px;padding:2px 9px;border:1px solid var(--dim);background:var(--panel);font-size:10px;color:var(--muted)}
.badge i{width:6px;height:6px;background:var(--muted)}.badge.live{border-color:var(--fg);color:var(--fg)}.badge.live i{background:var(--fg)}
main{flex:1;min-height:0;display:grid;grid-template-rows:minmax(0,1.5fr) minmax(0,1fr);grid-template-columns:minmax(420px,1.6fr) repeat(2,minmax(320px,1fr));gap:18px;padding:18px}
.col{display:flex;flex-direction:column;gap:18px;min-width:0;min-height:0}section{min-width:0;flex:0 0 auto}
/* fit the viewport: .grow sections share the leftover height and scroll (or scale) their body inside */
.grow{flex:1 1 0;min-height:0;display:flex;flex-direction:column}.grow>:last-child{flex:1 1 0;min-height:0;overflow:auto}
h3{display:flex;justify-content:space-between;margin:0 0 10px;padding-bottom:5px;border-bottom:1px solid var(--dim);font-size:11px;color:var(--muted);letter-spacing:.04em}
h3 span{font-weight:normal;font-size:10px}
#frame,#live{width:100%;object-fit:contain;image-rendering:pixelated;background:#000;border:1px solid var(--dim)}
.out{position:relative;flex:none;background:var(--panel);border:1px solid var(--fg);padding:7px 12px 7px 18px}
.out:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--fg)}
.out .k{font-size:10px;color:var(--muted);font-weight:bold}.out .big{font-size:16px;font-weight:bold;margin:2px 0;word-break:break-word}
.kv{display:grid;grid-template-columns:84px minmax(0,1fr);overflow-wrap:anywhere;gap:3px 10px;align-content:start;margin-top:10px}.kv .k{color:var(--muted);text-transform:uppercase;font-size:10px;padding-top:1px}
.kv .g{grid-column:1/-1;margin-top:8px;padding-bottom:2px;border-bottom:1px solid var(--subtle);color:var(--muted);font-size:10px;font-weight:bold;letter-spacing:.06em}.kv .g:first-child{margin-top:0}
.stage{display:flex}.stage>*{flex:1;min-width:0;min-height:0;height:100%}.stage [hidden]{display:none}
#controls{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px}.ctl{display:grid;grid-template-columns:58px 1fr 18px;gap:8px;align-items:center}
.ctl .bar{height:8px;background:var(--subtle);border:1px solid var(--dim)}.ctl .bar i{display:block;height:100%;background:var(--muted)}
.ctl.on{color:var(--fg);font-weight:bold}.ctl.on .bar i{background:var(--fg)}.ctl:not(.on){color:var(--muted)}.ctl span:last-child{text-align:right;font-size:10px}
#feed div{padding:3px 4px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-bottom:1px solid var(--subtle);color:var(--muted)}
#feed div:hover{background:var(--subtle)}#feed div.sel{color:var(--fg);background:var(--panel);outline:1px solid var(--dim)}
.v-surprise{text-decoration:underline}.v-fail,.v-failed{color:var(--fg);font-weight:bold}
#goals div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted);padding:1px 0}#goals .focus{color:var(--fg);font-weight:bold}#goals .done{text-decoration:line-through}
button,select{background:var(--panel);color:var(--fg);border:1px solid var(--dim);padding:2px 8px;font:inherit;font-size:10px}
#after{width:64px}#overlay{width:92px}
canvas{display:block;width:100%;background:var(--bg);border:1px solid var(--dim)}
#edges div{color:var(--muted);border-bottom:1px solid var(--subtle);padding:2px 0}
footer{display:flex;justify-content:space-between;border-top:1px solid var(--fg);padding:10px 18px;color:var(--muted);font-size:10px}
@media (max-width:1100px){body{height:auto;overflow:auto}main{grid-template-columns:1fr;grid-template-rows:none}.grow{flex:none}.grow>:last-child{flex:none;max-height:420px}}
</style></head><body>
<header><b>AVATAR / RUN</b><span id="run" class="muted"></span><span style="flex:1"></span>
<button id="follow">FOLLOW LIVE</button><span id="badge" class="badge"><i></i><span>IDLE</span></span></header>
<main>
<!-- row-major 3x2 grid: top-row panels share one bottom seam -->
<section class="grow"><h3>FRAME <span><span id="lcount"></span> <button id="after">LIVE</button> <button id="overlay">OVERLAY OFF</button> <span id="fstep"></span></span></h3><div class="stage"><canvas id="live" width="160" height="144"></canvas><img id="frame" alt="" hidden><canvas id="lens" hidden></canvas></div></section>
<section class="grow"><div id="card" class="out"><div class="k">MODEL OUTPUT</div><div class="big">WAITING FOR TRACE</div><div class="muted">start scripts/run_agent.py on this run</div></div><div id="detail" class="kv"></div></section>
<section class="grow"><h3>MAP <select id="place"></select></h3><canvas id="map"></canvas></section>
<div class="col"><section><h3>CONTROLS <span id="ckind"></span></h3><div id="controls"></div></section>
<section class="grow"><h3>QUESTS / BELIEFS / NOTE <span id="gfocus"></span></h3><div id="goals"></div></section></div>
<section class="grow"><h3>FEED <span id="fcount"></span></h3><div id="feed"></div></section>
<section class="grow"><h3>PLACE LINKS <span>learned moves between places</span></h3><div id="edges"></div></section>
</main>
<footer><span id="fstat">STEP —</span><span id="fcalls"></span></footer>
<script>
const $=id=>document.getElementById(id), rows=[], esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const PAD=["UP","DOWN","LEFT","RIGHT","A","B","START","SELECT"], DIR={UP:"↑",DOWN:"↓",LEFT:"←",RIGHT:"→"};
let pinned=null, chosenPlace=null, last={};
// LIVE: newest current source frame; MOTION: the step's presses frame by frame (run_agent.py)
const VIEWS=["LIVE","BEFORE","AFTER","MOTION"]; let after=0; const setView=v=>{after=v; $("after").textContent=VIEWS[v]; render()}; $("after").onclick=()=>setView((after+1)%4);
let overlay=false; $("overlay").onclick=()=>{overlay=!overlay; $("overlay").textContent=overlay?"OVERLAY ON":"OVERLAY OFF"; render()};
let frameEpoch=0, frameAbort=null, frameConnected=false, displayedSeq=-1, visible=true, frameBusy=false, frameWake=false;
function resetFrames(){frameEpoch++; if(frameAbort) frameAbort.abort(); frameAbort=null; frameConnected=false; displayedSeq=-1; $("live").dataset.frameSeq=""; $("live").dataset.capturedMonotonic=""; $("fstep").textContent=""; $("live").getContext("2d").clearRect(0,0,160,144)}
async function pullFrame(){
  if(frameBusy){frameWake=true;return}
  if(!visible||document.hidden){setTimeout(pullFrame,250);return}
  frameBusy=true;
  const epoch=frameEpoch, expectedRun=last.run, expectedGeneration=last.generation, controller=new AbortController(); frameAbort=controller;
  try{
    const res=await fetch("/live-frame",{cache:"no-store",signal:controller.signal});
    if(!res.ok) throw Error("frame unavailable");
    const run=res.headers.get("X-Run"), generation=res.headers.get("X-Generation"), seq=+res.headers.get("X-Frame-Seq"), captured=res.headers.get("X-Captured-Monotonic"), age=res.headers.get("X-Frame-Age-Ms");
    const bitmap=await createImageBitmap(await res.blob());
    try{
      if(epoch===frameEpoch&&run===expectedRun&&generation===expectedGeneration&&run===last.run&&generation===last.generation&&seq>displayedSeq){
        $("live").getContext("2d").drawImage(bitmap,0,0); displayedSeq=seq;
        $("live").dataset.frameSeq=String(seq); $("live").dataset.capturedMonotonic=captured;
        if(after===0) $("fstep").textContent=`LIVE / FRAME ${seq} / ${age} MS`;
      }
    }finally{bitmap.close()}
    if(epoch===frameEpoch&&run===expectedRun&&generation===expectedGeneration&&run===last.run&&generation===last.generation) frameConnected=true;
  }catch(e){if(!controller.signal.aborted&&frameConnected){resetFrames()}}
  if(frameAbort===controller) frameAbort=null;
  frameBusy=false;
  if(frameWake){frameWake=false;queueMicrotask(pullFrame)}else if(frameConnected) requestAnimationFrame(pullFrame); else setTimeout(pullFrame,250);
}
document.addEventListener("visibilitychange",()=>{visible=!document.hidden;resetFrames();if(visible)pullFrame()});
pullFrame();
$("follow").onclick=()=>{pinned=null;chosenPlace=null;setView(0)};
$("place").onchange=e=>{chosenPlace=e.target.value;render()};
async function poll(){
  try{const since=rows.length?rows[rows.length-1].step:0, s=await (await fetch(`/state?since=${since}&run=${encodeURIComponent(last.run||"")}`)).json();
    if(s.run!==last.run||s.generation!==last.generation){pinned=null;chosenPlace=null;rows.length=0;resetFrames()} else if(s.rows.length&&rows.length&&s.rows[0].step<=since) rows.length=0; rows.push(...s.rows); last=s; render();}catch(e){}
  setTimeout(poll,500);
}
// the decided-but-unfinished step from live.json, shown until its trace row lands; stale files from an older run are ignored
const pending=()=>{const p=last.pending, n=rows.length?rows[rows.length-1].step:0; return p&&p.step===n+1?{...p,pending:true,verdict:"acting"}:null};
const sel=()=>pinned!=null?rows.find(r=>r.step===pinned):pending()||rows[rows.length-1];
const label=id=>{const p=(last.places||[]).find(p=>p.id===id);return p?`${p.name||id} [${id}]`:id||"?"};
function keysOf(r){ // real presses from presses.jsonl; older runs fall back to the chosen buttons
  const k=(last.presses||{})[r.step]; if(k) return [k,"PRESSED"];
  const c=r.choice||{}; return [c.verb==="buttons"?String(c.target||"").split(/\s+/).filter(Boolean):[],"FROM CHOICE"];
}
function render(){
  const r=sel(), live=rows[rows.length-1];
  $("run").textContent=last.run||""; $("badge").className="badge"+(last.live?" live":""); $("badge").lastChild.textContent=last.live?"LIVE":"IDLE";
  $("fstat").textContent=live?`STEP ${String(live.step).padStart(5,"0")} / ${label(live.place).toUpperCase()}`:"STEP —"; $("fcalls").textContent=`${last.calls||0} MODEL CALLS`;
  $("live").hidden=overlay||after!==0; $("frame").hidden=overlay||after===0; $("lens").hidden=!overlay;
  if(r){ const src="/"+(after===3&&r.frame?r.frame.replace(".png","_motion.gif"):after===2&&r.after?r.after:r.frame); if(after&&!$("frame").src.endsWith(src)) $("frame").src=src; if(after) $("fstep").textContent="STEP "+r.step+(pinned!=null?" / PINNED":"");
    const c=r.choice||{}, sk=r.skill||{}, p=r.plan, o=r.outcome||{}, a=r.act||{}, pr=r.pred||{}, j=v=>v==null?"":typeof v==="object"?JSON.stringify(v):String(v);
    const ents=r.entities||[];
    const groups=[  // only what the vision and act calls returned, plus the outcome and any replan
      ["VISION",[["screen",r.screen+(r.perception_reused?" (cached look)":"")],["things",ents.map(e=>String(e.kind).toLowerCase()==="player"?"you":e.name||e.kind).join(", ")]]],
      ["ACT",[["place",a.place],["action",j(a.action)],["typed",c.text],["expects",pr.effect],["notebook",a.ops?`${a.ops} edit${a.ops>1?"s":""}`:""],["why",c.why||a.why]]],
      ["RESULT",[["saw",(o.effects||[]).join(", ")],["verdict",r.verdict],["moved to",r.next_place&&r.next_place!==r.place?label(r.next_place):""]]],
      ["REPLAN",p?[["focus",p.focus],["why",p.why]]:[]]];
    $("card").innerHTML=`<div class="k">MODEL OUTPUT</div><div class="big">${esc(`${c.verb||"wait"} ${c.target||""}`.toUpperCase())}</div>
      <div class="muted">${r.pending?`ACTING...  /  ${esc(r.screen)}`:`${esc(String(r.verdict||"").toUpperCase())}  /  ${esc(r.screen)}  /  ${r.calls} CALLS  /  ${r.ms} MS`}</div>`;
    $("detail").innerHTML=groups.map(([g,kv])=>{kv=kv.filter(([,v])=>v!==""&&v!=null);return kv.length?`<div class="g">${g}</div>`+kv.map(([k,v])=>`<div class="k">${esc(k)}</div><div>${esc(v)}</div>`).join(""):""}).join("");
    lens(r);
    const [keys,kind]=keysOf(r), n=k=>keys.filter(x=>x===k).length, most=Math.max(1,...PAD.map(n)); $("ckind").textContent=`${keys.length} ${kind}`;
    $("controls").innerHTML=PAD.map(k=>`<div class="ctl ${n(k)?"on":""}"><span>${n(k)?"> ":"  "}${k}</span><div class="bar"><i style="width:${100*n(k)/most}%"></i></div><span>${n(k)}</span></div>`).join("");}
  $("fcount").textContent=rows.length+" STEPS";
  const pend=pending(), pc=(pend&&pend.choice)||{};
  $("feed").innerHTML=(pend?`<div class="${r===pend||r&&r.pending?"sel":""}">${r&&r.pending?">":" "} ${String(pend.step).padStart(4,"0")}  ${esc(label(pend.place))}  ${esc(pc.verb||"")} ${esc(pc.target||"")} -> acting...</div>`:"")+rows.slice(-300).reverse().map(x=>{const c=x.choice||{};return `<div data-s="${x.step}" class="${x.step===(r&&r.step)?"sel":""}">${x.step===(r&&r.step)?">":" "} ${String(x.step).padStart(4,"0")}  ${esc(label(x.place))}  ${esc(c.verb||"")} ${esc(c.target||"")} -> <span class="v-${esc(x.verdict)}">${esc(x.verdict)}</span>${x.plan?"  [REPLAN]":""}</div>`}).join("");
  goals(); map(r&&r.pending?rows[rows.length-1]:r);  // the pending step has no tile until it lands
}
$("feed").onclick=e=>{const d=e.target.closest("[data-s]"); if(d){pinned=+d.dataset.s; chosenPlace=null; after===0?setView(1):render();}};  // a pinned step shows its own frames, not the live stream
const img=new Image(); img.onload=()=>lens(sel());
function lens(r){ // the perception layer over a small copy of the frame: detected entities, the self box, the player fix
  const cv=$("lens"), ctx=cv.getContext("2d"); if(cv.width!==cv.clientWidth||cv.height!==cv.clientHeight){cv.width=cv.clientWidth;cv.height=cv.clientHeight}
  ctx.clearRect(0,0,cv.width,cv.height); if(!r) return;
  const src="/"+r.frame; if(!img.src.endsWith(src)){img.src=src; return}
  if(!img.complete||!img.naturalWidth) return;
  const k=Math.min(cv.width/img.naturalWidth,cv.height/img.naturalHeight), W=img.naturalWidth*k, H=img.naturalHeight*k, X=(cv.width-W)/2, Y=(cv.height-H)/2;
  ctx.imageSmoothingEnabled=false; ctx.globalAlpha=.55; ctx.drawImage(img,X,Y,W,H); ctx.globalAlpha=1;
  const at=p=>[X+p[0]*W,Y+p[1]*H], ents=(r.entities||[]).filter(e=>Array.isArray(e.at)), t=16*k;
  ctx.font="10px Consolas"; ctx.textBaseline="bottom"; $("lcount").textContent=`${ents.length} ENTITIES${r.perception_reused?" / CACHED":""}`;
  if(Array.isArray(r.self_spot)){const [x,y]=at(r.self_spot); ctx.strokeStyle="#f2f2f2"; ctx.lineWidth=2; ctx.strokeRect(x-t/2,y-t/2,t,t); ctx.lineWidth=1}
  if(r.player_fix&&r.player_fix.from&&r.player_fix.to){const [a,b]=at(r.player_fix.from),[c,d]=at(r.player_fix.to); ctx.strokeStyle="#858585"; ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(a,b); ctx.lineTo(c,d); ctx.stroke(); ctx.setLineDash([])}
  for(const e of ents){ const [x,y]=at(e.at), me=String(e.kind).toLowerCase()==="player";
    ctx.strokeStyle=me?"#f2f2f2":"#c8c8c8"; ctx.beginPath(); ctx.moveTo(x-5,y); ctx.lineTo(x+5,y); ctx.moveTo(x,y-5); ctx.lineTo(x,y+5); ctx.stroke();
    const text=me?"YOU":(e.name||e.kind||"?")+(e.id?" ["+e.id+"]":""), w=ctx.measureText(text).width+6, lx=Math.min(Math.max(x-w/2,0),cv.width-w), ly=Math.max(y-7,13);
    ctx.fillStyle="#080808"; ctx.fillRect(lx,ly-12,w,12); ctx.strokeStyle="#303030"; ctx.strokeRect(lx,ly-12,w,12); ctx.fillStyle=me?"#f2f2f2":"#c8c8c8"; ctx.fillText(text,lx+3,ly);}
}
function goals(){
  const nb=last.notebook||{}, open1=Array.isArray(nb.quests)?nb.quests.find(q=>q.status==="open"):null, planned=[...rows].reverse().find(r=>r.plan&&r.plan.focus), focus=(open1&&open1.key)||nb.focus||(planned&&planned.plan.focus)||"", out=[]; $("gfocus").textContent=focus?"FOCUS "+focus:"";
  if(Array.isArray(nb.quests)){
    for(const q of nb.quests){const key=q.key||q.id||"quest", check=q.check?`${q.check.kind}: ${q.check.arg}`:"";
      out.push(`<div class="${key===focus?"focus ":""}${esc(q.status)}">${esc(key)}  <span>${esc(q.text)} / ${esc(q.status||"active")}${check?` / check ${esc(check)}`:""}</span></div>`);}
    const beliefs=nb.beliefs||{};
    for(const [key,b] of Object.entries(beliefs)){const state=b.stance||b.status||"held";
      out.push(`<div>${esc(state==="ruled_out"||state==="ruled out"?"RULED OUT":"HELD")}  <span>${esc(key)} / ${esc(b.text||b.value||"")}${b.reason?` / ${esc(b.reason)}`:""}</span></div>`);}
    const notes=Array.isArray(nb.notes)?nb.notes:[], note=notes[notes.length-1];
    if(note) out.push(`<div>LATEST NOTE  <span>${esc(typeof note==="string"?note:note.text||note.note||"")}</span></div>`);
  }else{
    const g=nb.goals||{};
    const walk=(parent,depth)=>Object.entries(g).filter(([,v])=>(v.parent||null)===parent).forEach(([k,v])=>{
      out.push(`<div class="${k===focus?"focus":""} ${esc(v.status)}" style="padding-left:${depth*14}px" title="${esc(v.done_when)}">${k===focus?"> ":"  "}${esc(k)}  <span>${esc(v.text)} / ${esc(v.status||"active")}</span></div>`); walk(k,depth+1);});
    walk(null,0);
  }
  $("goals").innerHTML=out.join("")||'<span class="muted">NO NOTEBOOK YET</span>';
}
function map(r){
  const places=(last.places||[]).filter(p=>p.state!=="merged"), cur=chosenPlace||(r&&r.place);
  $("place").innerHTML=places.map(p=>`<option value="${esc(p.id)}" ${p.id===cur?"selected":""}>${esc(label(p.id))}</option>`).join("");
  const tiles=(last.tiles||[]).filter(t=>t.place===cur), cv=$("map"), ctx=cv.getContext("2d");
  if(cv.width!==cv.clientWidth||cv.height!==cv.clientHeight){cv.width=cv.clientWidth;cv.height=cv.clientHeight} // drawing buffer follows the fitted size
  ctx.clearRect(0,0,cv.width,cv.height);
  const me=r&&r.place===cur&&r.tile?r.tile:null, xs=tiles.map(t=>t.x).concat(me?[me[0]]:[]), ys=tiles.map(t=>t.y).concat(me?[me[1]]:[]);
  ctx.font="11px Consolas"; ctx.textAlign="center"; ctx.textBaseline="middle";
  if(xs.length){
    const x0=Math.min(...xs)-1,y0=Math.min(...ys)-1,w=Math.max(...xs)-x0+2,h=Math.max(...ys)-y0+2, s=Math.max(6,Math.min(40,Math.floor(Math.min(cv.width/w,cv.height/h))));
    const px=x=>(x-x0)*s, py=y=>(y-y0)*s;
    ctx.strokeStyle="#171717"; for(let i=0;i<=w;i++){ctx.beginPath();ctx.moveTo(i*s,0);ctx.lineTo(i*s,h*s);ctx.stroke()} for(let j=0;j<=h;j++){ctx.beginPath();ctx.moveTo(0,j*s);ctx.lineTo(w*s,j*s);ctx.stroke()}
    for(const t of tiles){ const X=px(t.x),Y=py(t.y);
      ctx.fillStyle=t.floor===true?"#303030":"#0d0d0d"; ctx.fillRect(X+1,Y+1,s-2,s-2);
      if(t.floor===false){ctx.strokeStyle="#858585";ctx.beginPath();ctx.moveTo(X+3,Y+3);ctx.lineTo(X+s-3,Y+s-3);ctx.moveTo(X+s-3,Y+3);ctx.lineTo(X+3,Y+s-3);ctx.stroke()}
      ctx.strokeStyle="#f2f2f2"; ctx.lineWidth=3;
      for(const d of t.blocked||[]){ctx.beginPath(); if(d==="UP"){ctx.moveTo(X,Y);ctx.lineTo(X+s,Y)} if(d==="DOWN"){ctx.moveTo(X,Y+s);ctx.lineTo(X+s,Y+s)} if(d==="LEFT"){ctx.moveTo(X,Y);ctx.lineTo(X,Y+s)} if(d==="RIGHT"){ctx.moveTo(X+s,Y);ctx.lineTo(X+s,Y+s)} ctx.stroke()}
      ctx.lineWidth=1;
      if(t.exit){ ctx.fillStyle=t.exit.src==="observed"?"#f2f2f2":"#858585"; ctx.fillText(DIR[t.exit.dir]||"*",X+s/2,Y+s/2); ctx.fillStyle="#858585"; ctx.fillText(t.exit.to,X+s/2,Y-6);}}
    if(me){ctx.strokeStyle="#f2f2f2"; ctx.lineWidth=2; ctx.strokeRect(px(me[0])+s*.2,py(me[1])+s*.2,s*.6,s*.6); ctx.fillStyle="#f2f2f2"; ctx.fillRect(px(me[0])+s*.38,py(me[1])+s*.38,s*.24,s*.24); ctx.lineWidth=1;}
  } else { ctx.fillStyle="#858585"; ctx.fillText("NO TILES FOR THIS PLACE YET",cv.width/2,24); }
  $("edges").innerHTML=places.flatMap(p=>(p.edges||[]).map(e=>`<div>${esc(label(p.id))} -> ${esc(label(e.to))}  via ${esc(e.via)} / ok ${esc(e.ok)}</div>`)).join("")||'<span class="muted">NO PLACE EDGES YET</span>';
}
poll();
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--run", type=Path); ap.add_argument("--runs", type=Path, default=Path(__file__).resolve().parents[1] / "runs"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true"); a = ap.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), handler(Follow(a.run, a.runs)))
    url = f"http://127.0.0.1:{a.port}/"; print(f"watching {a.run or f'newest run in {a.runs}'} at {url}", flush=True)
    if not a.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    return 0


if __name__ == "__main__": raise SystemExit(main())

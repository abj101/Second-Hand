"""Compare motion, YOLO-World, and SAM 2 on Infinite Mario. No frames saved."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from loop.adaptive.evidence import iou
from loop.adaptive.telemetry import percentile

TILE_MAX_AREA = 0.01
WARMUP_FRAMES = 3
LATENCY_CAP_MS = 1000.0


def load_track_prompts() -> list[str]:
    path = Path(__file__).with_name("track_prompts.json")
    return list(json.loads(path.read_text(encoding="utf-8"))["prompts"])


def _area(bbox) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _center(bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def _mean_jaccard(sets: list[set[str]], *, skip: int = 0) -> float:
    rows = sets[skip:]
    if len(rows) < 2:
        return 1.0
    scores = [_jaccard(rows[i], rows[i + 1]) for i in range(len(rows) - 1)]
    return sum(scores) / len(scores)


def _tile_ids(hits) -> set[str]:
    groups: dict[bytes, list] = {}
    for hit in hits:
        if hit.state != "new" or _area(hit.bbox) <= 0.0 or _area(hit.bbox) > TILE_MAX_AREA:
            continue
        groups.setdefault(bytes(hit.appearance), []).append(hit.track_id)
    out: set[str] = set()
    for ids in groups.values():
        if len(ids) >= 2:
            out.update(ids)
    return out


def _player_id(hits, *, player_box, prev_centers: dict[str, tuple[float, float]]) -> str | None:
    if not hits:
        return None
    if player_box is not None:
        ranked = sorted(hits, key=lambda hit: iou(tuple(hit.bbox), tuple(player_box)) if hit.bbox else -1.0)
        best = ranked[-1]
        if best.bbox is None or iou(tuple(best.bbox), tuple(player_box)) < 0.3:
            return None
        return str(best.track_id)
    tiles = _tile_ids(hits)
    pool = [hit for hit in hits if hit.track_id not in tiles and hit.bbox]
    if not pool:
        return str(hits[0].track_id) if len(hits) == 1 else None
    moving = []
    for hit in pool:
        prev = prev_centers.get(hit.track_id)
        if prev is None:
            continue
        center = _center(hit.bbox)
        if abs(center[0] - prev[0]) + abs(center[1] - prev[1]) > 0.005:
            moving.append(hit)
    chosen = moving or pool
    return str(max(chosen, key=lambda hit: _area(hit.bbox)).track_id)


def summarize_arm(name: str, episodes: list[list[dict]], *, load_ms: float, unavailable: bool, associator: str) -> dict:
    if unavailable:
        return {
            "arm": name,
            "ran": False,
            "unavailable": True,
            "associator": associator,
            "load_ms": load_ms,
            "id_switches": None,
            "latency_median_ms": None,
        }
    flat = [frame for episode in episodes for frame in episode]
    id_sets = [{str(hit.track_id) for hit in frame["hits"]} for frame in flat]
    semantic_calls = 0
    hit_count = 0
    new_count = 0
    returned_count = 0
    tiles = 0
    switches = 0
    visible = 0
    covered: dict[str, int] = {}
    for episode in episodes:
        prev_player = None
        prev_centers: dict[str, tuple[float, float]] = {}
        for frame in episode:
            hits = frame["hits"]
            hit_count += len(hits)
            for hit in hits:
                if hit.state == "new":
                    new_count += 1
                elif hit.state == "returned":
                    returned_count += 1
            semantic_calls += sum(1 for hit in hits if hit.state in {"new", "returned"})
            tiles += len(_tile_ids(hits))
            player = _player_id(hits, player_box=frame.get("player_box"), prev_centers=prev_centers)
            if player is not None:
                visible += 1
                covered[player] = covered.get(player, 0) + 1
                if prev_player is not None and player != prev_player:
                    switches += 1
                prev_player = player
            prev_centers = {
                hit.track_id: _center(hit.bbox) for hit in hits if hit.bbox
            }
    latencies = [float(frame["latency_ms"]) for frame in flat]
    coverage = (max(covered.values()) / visible) if visible else 0.0
    return {
        "arm": name,
        "ran": True,
        "unavailable": False,
        "associator": associator,
        "load_ms": load_ms,
        "frames": len(flat),
        "episodes": len(episodes),
        "id_switches": switches,
        "player_frames": visible,
        "player_id_coverage": coverage,
        "semantic_calls": semantic_calls,
        "new_count": new_count,
        "returned_count": returned_count,
        "semantic_equals_new_plus_returned": semantic_calls == new_count + returned_count,
        "semantic_calls_per_frame": (semantic_calls / len(flat)) if flat else 0.0,
        "hit_count": hit_count,
        "jaccard_mean": _mean_jaccard(id_sets),
        "jaccard_after_warmup": _mean_jaccard(id_sets, skip=WARMUP_FRAMES),
        "latency_median_ms": percentile(latencies, 50) if latencies else None,
        "latency_p95_ms": percentile(latencies, 95) if latencies else None,
        "tile_explosion": tiles,
    }


def pick_winner(arms: dict[str, dict]) -> str:
    """Fewer switches than `none` and a median frame under 1000 ms. Otherwise none."""

    baseline = arms.get("none") or {}
    base = baseline.get("id_switches")
    if not isinstance(base, int):
        return "none"
    winner = "none"
    best = base
    for name in ("yolo-world", "sam2"):
        row = arms.get(name) or {}
        if row.get("unavailable") or not row.get("ran"):
            continue
        switches = row.get("id_switches")
        latency = row.get("latency_median_ms")
        if not isinstance(switches, int) or not isinstance(latency, (int, float)):
            continue
        if switches < base and float(latency) < LATENCY_CAP_MS and switches < best:
            winner = name
            best = switches
    return winner


def _player_box(result) -> tuple[float, float, float, float] | None:
    info = getattr(result, "privileged_info", None) or {}
    raw = info.get("player_bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    box = tuple(float(v) for v in raw)
    if max(box) > 1.0:
        image = result.observation
        width, height = image.size
        box = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
    return box  # type: ignore[return-value]


def _eval_none(episodes) -> tuple[list[list[dict]], float]:
    from benchmarks.infinite_mario.perceive import MarioPerception

    perception = MarioPerception()
    measured: list[list[dict]] = []
    for episode in episodes:
        perception.reset()
        frames = []
        previous: set[str] = set()
        for result in episode:
            started = time.perf_counter()
            snap = perception.qualify(result)
            elapsed = (time.perf_counter() - started) * 1000.0
            hits = []
            ids = []
            for ent in snap.entities:
                state = "continuing" if ent.stable_id in previous else "new"
                hits.append(_Hit(ent.stable_id, None, b"", state))
                ids.append(ent.stable_id)
            previous = set(ids)
            frames.append({"hits": hits, "latency_ms": elapsed, "player_box": _player_box(result)})
        measured.append(frames)
    return measured, 0.0


class _Hit:
    def __init__(self, track_id, bbox, appearance, state) -> None:
        self.track_id = track_id
        self.bbox = bbox
        self.appearance = appearance
        self.state = state


def _eval_tracker(name: str, episodes) -> tuple[list[list[dict]], float, bool, str]:
    from loop.adaptive.tracker import open_tracker

    tracker = open_tracker(name, prompts=load_track_prompts())
    if tracker is None or tracker.status != "ok":
        status = "unavailable" if tracker is not None else "none"
        return [], float(getattr(tracker, "load_ms", 0.0)), True, status
    measured: list[list[dict]] = []
    for episode in episodes:
        tracker.reset()
        frames = []
        for result in episode:
            started = time.perf_counter()
            hits = tracker.update(result.observation)
            elapsed = (time.perf_counter() - started) * 1000.0
            frames.append({"hits": hits, "latency_ms": elapsed, "player_box": _player_box(result)})
        measured.append(frames)
    return measured, float(tracker.load_ms), False, str(tracker.associator)


def _train_seeds(repo_root: Path) -> list[int]:
    path = repo_root / "benchmarks" / "infinite_mario" / "seed-manifests" / "train.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [int(seed) for seed in data["seeds"][:5]]


async def _collect(cache: Path, seeds: list[int], frames_each: int):
    from benchmarks.infinite_mario.adapter import MarioEnvironment

    env = MarioEnvironment(cache, headless=True)
    await env.start()
    try:
        episodes = []
        for seed in seeds:
            result = await env.reset(seed, f"track-{seed}", 1)
            episode = [result]
            for _ in range(frames_each - 1):
                episode.append(await env.step("noop"))
            episodes.append(episode)
        return episodes
    finally:
        await env.close()


def _write_arm(directory: Path, summary: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"track_{summary['arm']}.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def run_comparison(*, repo_root: Path, workspace: Path, frames_each: int = 200) -> dict:
    cache = repo_root / ".cache" / "benchmarks" / "infinite-mario"
    seeds = _train_seeds(repo_root)
    report: dict = {
        "ran": False,
        "seeds": seeds,
        "frames_each": frames_each,
        "winner": "none",
        "default_tracker": "none",
        "reason": "",
        "arms": {},
    }
    if not cache.is_dir():
        report["reason"] = f"Mario page cache missing at {cache}"
        return report
    try:
        episodes = await _collect(cache, seeds, frames_each)
    except Exception as exc:  # noqa: BLE001 — benchmark env is optional
        report["reason"] = str(exc)
        return report
    report["ran"] = True
    none_frames, none_load = _eval_none(episodes)
    arms = {
        "none": summarize_arm(
            "none", none_frames, load_ms=none_load, unavailable=False, associator="motion"
        )
    }
    for name in ("yolo-world", "sam2"):
        frames, load_ms, unavailable, associator = _eval_tracker(name, episodes)
        arms[name] = summarize_arm(
            name,
            frames,
            load_ms=load_ms,
            unavailable=unavailable,
            associator=associator,
        )
    winner = pick_winner(arms)
    report["arms"] = arms
    report["winner"] = winner
    report["default_tracker"] = "none"
    report["reason"] = (
        "winner beat the motion sensor under 1000 ms"
        if winner != "none"
        else "neither arm beat the motion sensor under 1000 ms; default stays none"
    )
    for summary in arms.values():
        _write_arm(workspace, summary)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mario spatial tracker comparison")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    report = asyncio.run(
        run_comparison(repo_root=repo_root, workspace=workspace, frames_each=max(1, args.frames))
    )
    path = workspace / "track_comparison.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

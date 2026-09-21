"""Adaptive collect CLI (workspace + optional live window)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from loop.adaptive.orchestrate import Trace, compile_schema, orchestrate, promote
from loop.adaptive.perceive import SceneAssembler, derive_interactions
from loop.adaptive.runtime import InvocationError, normalized_click_point, select_invocation
from loop.adaptive.schema import PrimitiveStep, SceneGraph
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld


def _ocr(world: NeutralWorld):
    return lambda _im: world.ocr_tokens()


def _as_rgb(pixels):
    if pixels is None:
        raise RuntimeError("pixel observation requires PIL")
    return pixels.convert("RGB") if getattr(pixels, "mode", "RGB") != "RGB" else pixels


def _make_runner(world: NeutralWorld, scene_holder: list):
    def runner(steps: tuple[PrimitiveStep, ...], bindings: dict) -> None:
        for st in steps:
            if st.op == "click":
                target = bindings.get("target") or bindings.get("node_id")
                if target is None:
                    raise InvocationError("click binding missing target")
                scene = scene_holder[0]
                if scene is not None:
                    try:
                        x, y = normalized_click_point(scene, str(target))
                        world.click_at(x, y)
                        continue
                    except InvocationError:
                        pass
                world.click(str(target))
            elif st.op == "sleep":
                world.noop()

    return runner


def _try_promote(session: Session, traces: list[Trace], recs, *, kinds: frozenset[str] | None = None) -> None:
    before_rev = session.schema.revision.revision_id
    schema = session.schema
    policy = schema.policy
    for proposal in orchestrate(tuple(recs), schema):
        if kinds is not None and proposal.kind not in kinds:
            continue
        try:
            schema = promote(
                schema, proposal, traces, policy, store_dir=session.schema_dir
            )
            session.accept_schema(schema)
        except ValueError:
            continue
    compiled = compile_schema(session.schema)
    if compiled is not session.schema:
        session.set_schema(compiled)
    after_rev = session.schema.revision.revision_id
    if after_rev != before_rev:
        session.ledger.append_event(
            "promotion",
            {
                "revision_id": after_rev,
                "action_ids": [a.action_id for a in session.schema.actions],
            },
        )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _track_ids(graph: SceneGraph) -> frozenset[str]:
    return frozenset(n.node_id for n in graph.nodes if n.node_id.startswith("track_"))


def _field_keys(graph: SceneGraph) -> tuple[str, ...]:
    return tuple(sorted(n.node_id for n in graph.nodes if n.node_id.startswith("ocr_")))


def _shadow_schema_abort(session: Session, graph: SceneGraph) -> str | None:
    for action in session.schema.actions:
        if "track_" in action.action_id:
            return "schema action_id contains track_"
    for node in graph.nodes:
        if node.attributes.get("clickable"):
            return "shadow mode forbids clickable scene nodes"
    return None


_RAPID = None


def _rapid_engine():
    global _RAPID
    if _RAPID is False:
        return None
    if _RAPID is not None:
        return _RAPID
    try:
        from rapidocr import RapidOCR

        _RAPID = RapidOCR()
    except Exception:
        _RAPID = False
        return None
    return _RAPID


def _live_ocr_tokens(image):
    w, h = image.size
    if w <= 0 or h <= 0:
        return []
    engine = _rapid_engine()
    if engine is not None:
        try:
            import numpy as np

            out = engine(np.asarray(image.convert("RGB")), use_cls=False)
        except Exception:
            out = None
        txts = getattr(out, "txts", None) if out is not None else None
        boxes = getattr(out, "boxes", None) if out is not None else None
        if txts is None and isinstance(out, tuple) and len(out) >= 2:
            boxes, txts = out[0], out[1]
        tokens: list[tuple[str, tuple[float, float, float, float]]] = []
        if txts and boxes is not None:
            for text, box in zip(txts, boxes):
                blob = " ".join(str(text or "").split())
                if not blob:
                    continue
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                tokens.append(
                    (blob, (min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h))
                )
            if tokens:
                return tokens
    try:
        import pytesseract
    except ImportError:
        return []
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text or int(data["conf"][i]) < 0:
            continue
        x, y, bw, bh = (
            data["left"][i],
            data["top"][i],
            data["width"][i],
            data["height"][i],
        )
        out.append((text, (x / w, y / h, (x + bw) / w, (y + bh) / h)))
    return out


def _optional_vlm():
    base = os.getenv("OLLAMA_BASE_URL")
    if not base:
        return None
    try:
        import httpx

        httpx.get(f"{base.rstrip('/')}/api/tags", timeout=0.5)
    except Exception:
        return None
    try:
        from loop.model.gateway import VlmGateway

        gw = VlmGateway(base_url=base)
    except Exception:
        return None

    def _vlm(image):
        try:
            from loop.os.capture import encode_image

            frame = encode_image(image, longest_side=1280, jpeg_quality=85)
            call = gw.complete_text(
                system="Describe visible interface objects and relationships without assuming an application.",
                user="Describe this screen as neutral observations.",
                frames=[frame],
                json_mode=False,
            )
            return str(call.text or "")
        except Exception:
            return ""

    return _vlm


def _live_observe_factory(session: Session, target, assembler: SceneAssembler):
    from loop.os.window import grab_client_capture

    scene_holder: list = [None]
    ocr_fn = _live_ocr_tokens
    vlm_fn = _optional_vlm()

    def observe():
        image, provenance = grab_client_capture(target)
        if provenance.integrity != "trusted":
            raise RuntimeError(
                f"untrusted capture: {provenance.source}/{provenance.reason}"
            )
        image = _as_rgb(image)
        recs, graph = assembler.update(
            image, ocr=ocr_fn, vlm=vlm_fn, ledger=session.ledger
        )
        scene_holder[0] = graph
        snap = session.observe(graph)
        session.ledger.append_event(
            "observation",
            {
                "snapshot_id": graph.snapshot_id,
                "n_nodes": len(graph.nodes),
                "hwnd": int(target.hwnd),
                "capture_source": provenance.source,
                "capture_integrity": provenance.integrity,
            },
        )
        observe.provenance = {
            "integrity": provenance.integrity,
            "source": provenance.source,
            "hwnd": int(target.hwnd),
        }
        return recs, graph, snap, scene_holder

    return observe


def _make_screen_runner(target, scene_holder: list):
    from loop.os.mouse import Mouse
    from loop.os.primitives import MousePrimitive
    from loop.os.window import client_to_screen

    def runner(steps: tuple[PrimitiveStep, ...], bindings: dict) -> None:
        for st in steps:
            if st.op != "click":
                continue
            target_id = bindings.get("target") or bindings.get("node_id")
            if target_id is None:
                raise InvocationError("click binding missing target")
            scene = scene_holder[0]
            if scene is None:
                raise InvocationError("no scene for click")
            nx, ny = normalized_click_point(scene, str(target_id))
            sx, sy = client_to_screen(target, nx, ny)
            Mouse().execute(
                MousePrimitive(op="click", button="left"), screen_xy=(sx, sy)
            )

    return runner


def _resolve_live_target(window_title: str, hwnd: int | None = None):
    from loop.os.window import list_windows

    matches = list_windows(window_title)
    if hwnd is not None:
        matches = [target for target in matches if int(target.hwnd) == int(hwnd)]
    if not matches:
        raise RuntimeError("window not found")
    if len(matches) != 1:
        raise RuntimeError(
            f"window selector is ambiguous ({len(matches)} matches); pass --hwnd"
        )
    return matches[0]


def _bind_live_session(session: Session, target, window_title: str) -> None:
    from loop.os.window import target_identity

    session.bind_target(target_identity(target, selector=window_title))


def _shadow_metrics(frames: list[dict], *, warmup: int = 3) -> dict:
    track_sets = [frozenset(f["track_ids"]) for f in frames]
    jaccards = [
        _jaccard(track_sets[i], track_sets[i + 1])
        for i in range(len(track_sets) - 1)
    ]
    track_stability = sum(jaccards) / len(jaccards) if jaccards else 1.0

    tail = frames[warmup:] if len(frames) > warmup else frames
    first_keys: tuple[str, ...] | None = None
    continuity_hits = 0
    for f in tail:
        keys = f["field_keys"]
        if keys and first_keys is None:
            first_keys = keys
        if first_keys and keys == first_keys:
            continuity_hits += 1
    field_continuity = (
        continuity_hits / len(tail) if tail and first_keys else 0.0
    )

    rev_ids = {f["revision_id"] for f in frames}
    surfaces = [frozenset(f["surface_action_ids"]) for f in frames]
    surf_j = [
        _jaccard(surfaces[i], surfaces[i + 1]) for i in range(len(surfaces) - 1)
    ]
    action_surface_stability = sum(surf_j) / len(surf_j) if surf_j else 1.0

    return {
        "track_stability": track_stability,
        "field_continuity": field_continuity,
        "mode_ids": sorted(
            {m for f in frames for m in (f["mode_id"],) if m is not None}
        ),
        "revision_churn": max(0, len(rev_ids) - 1),
        "action_surface_stability": action_surface_stability,
        "restart_not_applicable": True,
    }


def _shadow_collect(
    session: Session, window_title: str, n_steps: int, hwnd: int | None = None
) -> tuple[int, dict]:
    try:
        target = _resolve_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
    except (RuntimeError, ValueError) as exc:
        err = {"mode": "shadow", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    assembler = SceneAssembler()
    observe = _live_observe_factory(session, target, assembler)
    n_frames = max(1, n_steps)
    frames: list[dict] = []
    traces: list[Trace] = []
    policy = session.schema.policy
    n_obs = max(policy.min_agreeing_reads, 1)

    for i in range(n_frames):
        recs, graph, snap, _holder = observe()
        reason = _shadow_schema_abort(session, graph)
        if reason:
            err = {"mode": "shadow", "error": reason}
            print(json.dumps(err), file=sys.stderr, flush=True)
            return 2, err
        frames.append(
            {
                "track_ids": sorted(_track_ids(graph)),
                "field_keys": _field_keys(graph),
                "mode_id": snap.mode_id,
                "revision_id": snap.schema_revision_id,
                "surface_action_ids": list(snap.surface.action_ids),
            }
        )
        traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
        if len(traces) >= n_obs:
            _try_promote(session, traces, recs)

    metrics = _shadow_metrics(frames)
    schema = session.schema
    summary = {
        "mode": "shadow",
        "frames": n_frames,
        **metrics,
        "revision_id": schema.revision.revision_id,
        "fields": [f.field_id for f in schema.fields],
        "actions": [a.action_id for a in schema.actions],
        "workspace": str(session.workspace.resolve()),
    }
    return 0, summary


def _armed_collect(
    session: Session, window_title: str, n_steps: int, hwnd: int | None = None
) -> tuple[int, dict]:
    try:
        target = _resolve_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
    except (RuntimeError, ValueError) as exc:
        err = {"mode": "armed", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    assembler = SceneAssembler()
    observe = _live_observe_factory(session, target, assembler)
    warm = max(3, min(n_steps, max(3, session.schema.policy.min_agreeing_reads)))
    traces: list[Trace] = []
    snap = None
    scene_holder = None
    for _ in range(warm):
        recs, graph, snap, scene_holder = observe()
        reason = _shadow_schema_abort(session, graph)
        if reason:
            err = {"mode": "armed", "error": reason}
            print(json.dumps(err), file=sys.stderr, flush=True)
            return 2, err
        traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
    _try_promote(session, traces, recs)

    # Re-project against the accepted revision, then require the complete
    # action surface to remain identical on three trusted observations.
    stable: list[tuple[str, ...]] = []
    for _ in range(3):
        _recs, _graph, snap, scene_holder = observe()
        stable.append(tuple(snap.surface.action_ids))
    if len(set(stable)) != 1:
        err = {"mode": "armed", "error": "unstable legal action surface"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    if snap is None or len(snap.surface.actions) != 1:
        err = {"mode": "armed", "error": "legal action surface is not unique"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    inv = select_invocation(snap) if snap is not None else None
    if inv is None:
        err = {"mode": "armed", "error": "no legal action"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    runner = _make_screen_runner(target, scene_holder or [None])
    try:
        ok = session.act(
            inv,
            runner,
            lambda: observe()[1],
            before=snap,
        )
    except (InvocationError, ValueError, OSError) as exc:
        err = {"mode": "armed", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    if not ok:
        err = {"mode": "armed", "error": "action not verified"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    schema = session.schema
    summary = {
        "mode": "armed",
        "verified": 1,
        "revision_id": schema.revision.revision_id,
        "workspace": str(session.workspace.resolve()),
    }
    return 0, summary


def _synthetic_collect(session: Session, n_steps: int) -> dict:
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    ocr = _ocr(world)
    scene_holder: list = [None]
    runner = _make_runner(world, scene_holder)
    traces: list[Trace] = []
    policy = session.schema.policy
    n_obs = max(policy.min_agreeing_reads, 1)
    verified = 0
    probes = 0
    chosen: str | None = None

    def observe():
        recs, graph = assembler.update(
            _as_rgb(world.observe()[0]), ocr=ocr, ledger=session.ledger
        )
        scene_holder[0] = graph
        snap = session.observe(graph)
        session.ledger.append_event(
            "observation",
            {"snapshot_id": graph.snapshot_id, "n_nodes": len(graph.nodes)},
        )
        return recs, graph, snap

    recs, _graph, snap = observe()
    traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
    for _ in range(n_obs - 1):
        recs, _graph, snap = observe()
        traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
    _try_promote(session, traces, recs)

    budget = max(1, n_steps)
    while probes < budget and not session.schema.actions:
        recs, graph, snap = observe()
        candidates = [n for n in graph.nodes if "numeric" not in n.attributes]
        if not candidates:
            break
        if chosen and any(n.node_id == chosen for n in candidates):
            node = next(n for n in candidates if n.node_id == chosen)
        else:
            node = candidates[probes % len(candidates)]
        before_fields = dict(snap.fields)
        x, y = normalized_click_point(graph, node.node_id)
        world.click_at(x, y)
        probes += 1
        recs2, graph2, snap2 = observe()
        ix = derive_interactions(graph, graph2, node.node_id)
        aid = str(ix[0].payload["action_id"]) if ix else None
        traces.append(
            Trace(before_fields, dict(snap2.fields), recs, recs2, action_id=aid)
        )
        _try_promote(session, traces, (*recs2, *ix))
        if ix:
            chosen = node.node_id
            assembler.note_clickable(node.node_id)

    while probes < budget and session.schema.actions:
        recs, graph, snap = observe()
        inv = select_invocation(snap)
        if inv is None:
            break
        ok = session.act(inv, runner, lambda: observe()[1], before=snap)
        session.ledger.append_event(
            "action",
            {"action_id": inv.action_id, "ok": ok, "bindings": dict(inv.bindings)},
        )
        probes += 1
        if ok:
            verified += 1

    schema = session.schema
    return {
        "mode": "synthetic",
        "steps_requested": n_steps,
        "probes": probes,
        "verified_clicks": verified,
        "revision_id": schema.revision.revision_id,
        "fields": [f.field_id for f in schema.fields],
        "entity_types": [e.type_id for e in schema.entity_types],
        "modes": [m.mode_id for m in schema.modes],
        "actions": [a.action_id for a in schema.actions],
        "workspace": str(session.workspace.resolve()),
    }


_OBSERVE_KINDS = frozenset({"field", "entity_type", "mode", "relation"})


def _load_scouts(cache_dir: Path):
    from loop.adaptive.experiment import make_gateway_scouts

    return make_gateway_scouts(cache_dir)


def _frame_row(graph, snap) -> dict:
    return {
        "track_ids": sorted(_track_ids(graph)),
        "field_keys": _field_keys(graph),
        "mode_id": snap.mode_id,
        "revision_id": snap.schema_revision_id,
        "surface_action_ids": list(snap.surface.action_ids),
    }


def _run_experiment(
    session: Session,
    n_steps: int,
    *,
    observe,
    click,
    rollback,
    legal_runner,
    arm: bool,
    live: bool,
    target,
) -> tuple[int, dict]:
    from loop.adaptive.experiment import (
        ExperimentController,
        admit_plan,
        execute_trial,
        observation_fingerprint,
        run_scouts,
        stability_ok,
    )
    from loop.adaptive.orchestrate import make_evidence_bundle

    ctrl = ExperimentController(session, min_frames=n_steps)
    recs, graph, snap, holder = observe()
    lock = session.recovery_state()
    if lock:
        if observation_fingerprint(snap, graph) == lock.get("baseline"):
            session.recovery_clear()
        else:
            err = {"mode": "experiment", "error": "workspace is recovery-locked"}
            print(json.dumps(err), file=sys.stderr, flush=True)
            return 2, err

    frames = [_frame_row(graph, snap)]
    graphs = [graph]
    traces = [Trace(dict(snap.fields), dict(snap.fields), recs, recs)]
    _try_promote(session, traces, recs, kinds=_OBSERVE_KINDS)
    for _ in range(max(0, n_steps - 1)):
        recs, graph, snap, holder = observe()
        frames.append(_frame_row(graph, snap))
        graphs.append(graph)
        traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
        _try_promote(session, traces, recs, kinds=_OBSERVE_KINDS)

    metrics = _shadow_metrics(frames)
    metrics["frames"] = len(frames)
    if not stability_ok(metrics, min_frames=n_steps):
        err = {"mode": "experiment", "error": "stability gate failed", **metrics}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    ctrl.set_state("propose")
    bundle = make_evidence_bundle(
        image_hash=graph.snapshot_id,
        scene=graph,
        schema=session.schema,
        evidence=recs,
        stability=metrics,
    )
    scouts = _load_scouts(session.workspace / "proposal_cache")
    schema_parsed, exp_parsed = {}, {"abstain": True, "candidate": None}
    if scouts is not None:
        schema_parsed, exp_parsed = run_scouts(
            bundle,
            cache_dir=session.workspace / "proposal_cache",
            schema_scout=scouts[0],
            experiment_scout=scouts[1],
        )
    plan = admit_plan(
        schema_parsed=schema_parsed,
        experiment_parsed=exp_parsed,
        evidence=recs,
        schema=session.schema,
        scenes=graphs[-3:],
        target_identity=session.target or {},
    )
    ctrl.accept_plan(plan)
    summary = {
        "mode": "experiment",
        "admitted": plan is not None,
        "armed": False,
        "plan_id": None if plan is None else plan.plan_id,
        **metrics,
        "revision_id": session.schema.revision.revision_id,
        "fields": [f.field_id for f in session.schema.fields],
        "actions": [a.action_id for a in session.schema.actions],
        "workspace": str(session.workspace.resolve()),
    }
    if plan is None or not arm:
        return 0, summary

    ctrl.set_state("arm")
    recs, graph, snap, holder = observe()
    baseline = observation_fingerprint(snap, graph)
    session.recovery_lock({"baseline": baseline, "plan_id": plan.plan_id})

    def after():
        _recs, graph2, snap2, _h = observe()
        return graph2, snap2

    prov = getattr(observe, "provenance", None) or {"integrity": "trusted"}
    identity = session.target
    try:
        ctrl.set_state("trial_1")
        recs, graph, snap, holder = observe()
        t1 = execute_trial(
            plan=plan,
            index=1,
            graph=graph,
            before=snap,
            click=click,
            observe_after=after,
            rollback=rollback,
            provenance=prov,
            live=live,
            target=target,
            identity=identity,
        )
        if not t1.rollback_ok:
            raise RuntimeError("rollback 1 failed")
        ctrl.record_trial(t1)
        ctrl.set_state("trial_2")
        recs, graph, snap, holder = observe()
        t2 = execute_trial(
            plan=plan,
            index=2,
            graph=graph,
            before=snap,
            click=click,
            observe_after=after,
            rollback=rollback,
            provenance=prov,
            live=live,
            target=target,
            identity=identity,
        )
        if not t2.rollback_ok:
            raise RuntimeError("rollback 2 failed")
        ctrl.record_trial(t2)
    except (RuntimeError, ValueError, OSError) as exc:
        err = {"mode": "experiment", "error": str(exc), "recovery_locked": True}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    if not ctrl.promotion_ready():
        err = {
            "mode": "experiment",
            "error": "promotion predicate failed",
            "recovery_locked": True,
        }
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    schema = ctrl.promote()
    compiled = compile_schema(schema)
    if compiled is not schema:
        session.set_schema(compiled)
    session.recovery_clear()

    stable: list[tuple[str, ...]] = []
    for _ in range(3):
        recs, graph, snap, holder = observe()
        stable.append(tuple(snap.surface.action_ids))
    if len(set(stable)) != 1 or snap is None or not snap.surface.actions:
        err = {"mode": "experiment", "error": "unstable legal action surface after promote"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    recs, graph, snap, holder = observe()
    inv = select_invocation(snap)
    if inv is None:
        err = {"mode": "experiment", "error": "promoted action not legal"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    try:
        ok = session.act(inv, legal_runner, lambda: observe()[1], before=snap)
    except (RuntimeError, ValueError, OSError, InvocationError) as exc:
        err = {"mode": "experiment", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    if not ok:
        err = {"mode": "experiment", "error": "promoted action not verified"}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    ctrl.set_state("postcheck")
    summary.update(
        {
            "armed": True,
            "promoted": True,
            "verified": 1,
            "revision_id": session.schema.revision.revision_id,
            "actions": [a.action_id for a in session.schema.actions],
            "next": "collect --arm after target restart",
        }
    )
    return 0, summary


def _synthetic_experiment(session: Session, n_steps: int, arm: bool) -> tuple[int, dict]:
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    ocr = _ocr(world)
    scene_holder: list = [None]

    def observe():
        recs, graph = assembler.update(
            _as_rgb(world.observe()[0]), ocr=ocr, ledger=session.ledger
        )
        scene_holder[0] = graph
        snap = session.observe(graph)
        observe.provenance = {"integrity": "trusted"}
        return recs, graph, snap, scene_holder

    def click(node_id: str) -> None:
        x, y = normalized_click_point(scene_holder[0], str(node_id))
        world.click_at(x, y)

    def rollback(_node_id: str) -> None:
        world.undo_click(CONTROL_NODE_ID)

    return _run_experiment(
        session,
        n_steps,
        observe=observe,
        click=click,
        rollback=rollback,
        legal_runner=_make_runner(world, scene_holder),
        arm=arm,
        live=False,
        target=None,
    )


def _live_experiment(
    session: Session, window_title: str, n_steps: int, hwnd: int | None, arm: bool
) -> tuple[int, dict]:
    try:
        target = _resolve_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
    except (RuntimeError, ValueError) as exc:
        err = {"mode": "experiment", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    assembler = SceneAssembler()
    observe = _live_observe_factory(session, target, assembler)
    scene_holder: list = [None]

    def wrapped():
        recs, graph, snap, holder = observe()
        scene_holder[0] = graph
        wrapped.provenance = getattr(observe, "provenance", {"integrity": "trusted"})
        return recs, graph, snap, holder

    def click(node_id: str) -> None:
        runner = _make_screen_runner(target, scene_holder)
        runner((PrimitiveStep("click", ("target",)),), {"target": node_id})

    return _run_experiment(
        session,
        n_steps,
        observe=wrapped,
        click=click,
        rollback=click,
        legal_runner=_make_screen_runner(target, scene_holder),
        arm=arm,
        live=True,
        target=target,
    )


def cmd_collect(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive collect session")
    parser.add_argument("--window", default=None, help="live window title substring")
    parser.add_argument("--hwnd", type=int, default=None, help="exact live window handle")
    parser.add_argument("--workspace", type=Path, required=True, help="run workspace directory")
    parser.add_argument("--steps", type=int, default=12, help="probe / shadow frame budget")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="run NeutralWorld instead of a live window",
    )
    parser.add_argument(
        "--arm",
        action="store_true",
        help="armed input barrier (promoted action, or an admitted experiment)",
    )
    parser.add_argument(
        "--experiment",
        action="store_true",
        help="proposal/admission; add --arm to execute one reversible candidate twice",
    )
    args = parser.parse_args(argv)

    if args.arm and not args.window and not args.synthetic:
        print(json.dumps({"error": "--arm requires --window"}), file=sys.stderr, flush=True)
        return 2

    session = Session(args.workspace, window_selector=args.window)

    if args.experiment:
        if args.synthetic or args.window is None:
            rc, summary = _synthetic_experiment(session, max(3, args.steps), args.arm)
        else:
            rc, summary = _live_experiment(
                session,
                args.window,
                30 if args.steps == 12 else max(3, args.steps),
                args.hwnd,
                args.arm,
            )
        session.write_step_log(args.workspace / "summary.json", summary)
        if rc == 0:
            print(json.dumps(summary, indent=2), flush=True)
        return rc

    if args.synthetic or args.window is None:
        summary = _synthetic_collect(session, max(1, args.steps))
        session.write_step_log(args.workspace / "summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    if args.arm:
        rc, summary = _armed_collect(session, args.window, max(1, args.steps), args.hwnd)
    else:
        rc, summary = _shadow_collect(session, args.window, max(1, args.steps), args.hwnd)

    session.write_step_log(args.workspace / "summary.json", summary)
    if rc == 0:
        print(json.dumps(summary, indent=2), flush=True)
    return rc

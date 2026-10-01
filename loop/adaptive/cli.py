"""Adaptive collect CLI (workspace + optional live window)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from loop.adaptive.decider_config import add_decider_args
from loop.adaptive.contracts import (
    OperatingState,
    OperatingTransition,
    TransitionFacts,
    after_preflight,
    decider_may_arm,
    transition_operating_state,
)
from loop.adaptive.orchestrate import (
    Trace,
    compile_schema,
    orchestrate,
    promote,
)
from loop.adaptive.game_brief import GameBrief, GameBriefError, select_game_brief
from loop.adaptive.perceive import SceneAssembler, derive_interactions
from loop.adaptive.runtime import InvocationError, normalized_click_point, select_invocation
from loop.adaptive.schema import PrimitiveStep, SceneGraph
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld


def _load_repo_env(path: Path | None = None) -> None:
    """Load simple KEY=VALUE settings without overriding the caller's environment."""

    env_path = path or Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value.strip("'\""))


_load_repo_env()

_BLACK_TRANSITION_TIMEOUT_S = 60.0
_BLACK_TRANSITION_POLL_S = 0.25


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


def _synthetic_verify_promoted(
    session: Session,
    inv,
    runner,
    before_snap,
    observe,
) -> bool:
    from loop.adaptive.runtime import execute, verify

    spec = next(
        (a for a in session.schema.actions if a.action_id == inv.action_id),
        None,
    )
    if spec is None:
        return False
    execute(inv, session.schema, runner)
    observed = observe()
    snap = observed[2] if len(observed) >= 3 else observed[-1]
    return verify(before_snap, snap, spec, bindings=inv.bindings)


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


def _ocr_node_ids(graph: SceneGraph) -> tuple[str, ...]:
    return tuple(sorted(n.node_id for n in graph.nodes if n.node_id.startswith("ocr_")))


def _valid_field_value(value: object) -> bool:
    return value is not None and value != ""


def _tracker_arg(value: str) -> str:
    if value in ("none", "fused-gpa") or (value.startswith("student:") and len(value) > len("student:")):
        return value
    raise argparse.ArgumentTypeError("expected none, fused-gpa or student:<concept library dir>")


def _assembler_perception_diagnostics(assembler) -> dict:
    """Cache and source counters. Missing tracker diagnostics stay unavailable."""

    if assembler is None:
        return {
            "frame_reused": None,
            "cache_hits": None,
            "measurement_digest": None,
            "screen_replaced": None,
            "source": None,
        }
    tracker = getattr(assembler, "_tracker", None)
    source = getattr(tracker, "diagnostics", None) if tracker is not None else None
    return {
        "frame_reused": bool(getattr(assembler, "frame_reused", False)),
        "cache_hits": int(getattr(assembler, "cache_hits", 0) or 0),
        "measurement_digest": getattr(assembler, "measurement_digest", None),
        "screen_replaced": bool(getattr(assembler, "screen_replaced", False)),
        "source": source if isinstance(source, dict) else None,
    }


def _shadow_schema_abort(session: Session, graph: SceneGraph) -> str | None:
    for action in session.schema.actions:
        if "track_" in action.action_id:
            return "schema action_id contains track_"
    for node in graph.nodes:
        if node.attributes.get("clickable"):
            return "shadow mode forbids clickable scene nodes"
    return None


# ponytail: 0.9 sits between logo garbage (~0.6) and UI text (>=0.98) on the measured screen.
# Upgrade path: per-token calibration if a real control lands in that gap.
_OCR_MIN_SCORE = 0.9
_RAPID = None
_RAPID_SMALL = None


def _rapid_engine(*, small: bool = False):
    global _RAPID, _RAPID_SMALL
    engine = _RAPID_SMALL if small else _RAPID
    if engine is False:
        return None
    if engine is not None:
        return engine
    try:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType

        model_type = ModelType.SMALL if small else ModelType.MEDIUM
        engine = RapidOCR(
            params={"Det.model_type": model_type, "Rec.model_type": model_type}
        )
    except Exception:
        engine = False
    if small:
        _RAPID_SMALL = engine
    else:
        _RAPID = engine
    if engine is False:
        return None
    return engine


def _live_ocr_tokens(image, *, small: bool = False):
    w, h = image.size
    if w <= 0 or h <= 0:
        return []
    engine = _rapid_engine(small=True) if small else _rapid_engine()
    if engine is not None:
        try:
            import numpy as np

            out = engine(np.asarray(image.convert("RGB")), use_cls=False)
        except Exception:
            out = None
        txts = getattr(out, "txts", None) if out is not None else None
        boxes = getattr(out, "boxes", None) if out is not None else None
        scores = getattr(out, "scores", None) if out is not None else None
        if txts is None and isinstance(out, tuple) and len(out) >= 3:
            boxes, txts, scores = out[0], out[1], out[2]
        tokens: list[tuple[str, tuple[float, float, float, float]]] = []
        if txts and boxes is not None and scores is not None:
            for text, box, score in zip(txts, boxes, scores):
                if float(score) < _OCR_MIN_SCORE:
                    continue
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
        if not text or float(data["conf"][i]) < _OCR_MIN_SCORE * 100:
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
    native_base = base.rstrip("/").removesuffix("/v1")
    try:
        import httpx

        httpx.get(f"{native_base}/api/tags", timeout=0.5).raise_for_status()
    except Exception:
        return None
    try:
        from loop.model.gateway import VlmGateway

        gw = VlmGateway(
            base_url=base,
            model=os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud"),
        )
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

    _vlm.model = gw.model
    return _vlm


def _live_ocr_for_assembler(assembler):
    tracker = getattr(assembler, "_tracker", None)
    if getattr(tracker, "require_visual_ownership", False):
        return partial(_live_ocr_tokens, small=True)
    return _live_ocr_tokens


def _live_observe_factory(session: Session, target, assembler: SceneAssembler):
    from dataclasses import replace

    from loop.os.window import grab_client_capture, is_foreground, target_identity

    scene_holder: list = [None]
    ocr_fn = _live_ocr_for_assembler(assembler)
    acquisition_seq = [0]

    def observe():
        # Focus is acquired once, before session binding.  A later focus loss is
        # a hard boundary: do not refocus, capture, append evidence, or promote.
        transition_started = None
        observe.transition_wait_s = 0.0
        while True:
            if not is_foreground(target):
                raise RuntimeError(
                    f"target lost foreground hwnd={int(target.hwnd)}; refusing observation"
                )
            identity_before = target_identity(target)
            if int(identity_before.get("hwnd") or 0) != int(target.hwnd):
                raise RuntimeError("pinned target identity changed before capture")
            image, provenance = grab_client_capture(target)
            identity_after = target_identity(target)
            if not is_foreground(target):
                raise RuntimeError(
                    f"target lost foreground hwnd={int(target.hwnd)} during capture"
                )
            if int(identity_after.get("hwnd") or 0) != int(target.hwnd):
                raise RuntimeError("pinned target identity changed after capture")
            before_pid = int(identity_before.get("pid") or 0)
            after_pid = int(identity_after.get("pid") or 0)
            if before_pid and after_pid and before_pid != after_pid:
                raise RuntimeError("pinned target process changed during capture")
            if (provenance.integrity == "trusted" or "hwnd-black" not in provenance.reason
                    or "focus-or-identity-changed" in provenance.reason):
                break
            if transition_started is None:
                transition_started = time.monotonic()
                on_transition = getattr(observe, "on_transition", None)
                if on_transition is not None:
                    on_transition()
            elapsed = time.monotonic() - transition_started
            if elapsed >= _BLACK_TRANSITION_TIMEOUT_S:
                raise RuntimeError(f"black transition exceeded {_BLACK_TRANSITION_TIMEOUT_S:g}s")
            time.sleep(min(_BLACK_TRANSITION_POLL_S, _BLACK_TRANSITION_TIMEOUT_S - elapsed))
        if transition_started is not None:
            observe.transition_wait_s = time.monotonic() - transition_started
            session.ledger.append_event("capture_transition", {
                "reason": "black", "wait_s": observe.transition_wait_s,
                "hwnd": int(target.hwnd),
            })
        if provenance.integrity != "trusted" or provenance.source not in {
            "hwnd-wgc",
            "hwnd-imagegrab",
            "hwnd-printwindow",
        }:
            raise RuntimeError(
                f"untrusted capture: {provenance.source}/{provenance.reason}"
            )
        reported_before = provenance.identity_before or identity_before
        reported_after = provenance.identity_after or identity_after
        if int(reported_before.get("hwnd") or 0) != int(target.hwnd):
            raise RuntimeError("capture reported a different pinned HWND before capture")
        if int(reported_after.get("hwnd") or 0) != int(target.hwnd):
            raise RuntimeError("capture reported a different pinned HWND after capture")
        reported_before_pid = int(reported_before.get("pid") or 0)
        reported_after_pid = int(reported_after.get("pid") or 0)
        if (
            reported_before_pid
            and reported_after_pid
            and reported_before_pid != reported_after_pid
        ):
            raise RuntimeError("capture reported a different target process")
        if any(
            value is not None and int(value) != int(target.hwnd)
            for value in (provenance.foreground_before, provenance.foreground_after)
        ):
            raise RuntimeError("capture reported a different foreground HWND")
        provenance = replace(
            provenance,
            pinned_hwnd=int(target.hwnd),
            identity_before=reported_before,
            identity_after=reported_after,
            foreground_before=provenance.foreground_before or int(target.hwnd),
            foreground_after=provenance.foreground_after or int(target.hwnd),
        )
        # One sequence step per capture, even for identical pixels; the closure
        # is scoped to this (session, target) observer and never reused.
        acquisition_seq[0] += 1
        sequence = acquisition_seq[0]
        image = _as_rgb(image)
        recs, graph = assembler.update(image, ocr=ocr_fn, ledger=session.ledger)
        scene_holder[0] = graph
        snap = session.observe(graph)
        session.ledger.append_event(
            "observation",
            {
                "snapshot_id": graph.snapshot_id,
                "n_nodes": len(graph.nodes),
                "hwnd": int(target.hwnd),
                "target_identity_before": provenance.identity_before,
                "target_identity_after": provenance.identity_after,
                "capture_source": provenance.source,
                "capture_integrity": provenance.integrity,
                "capture_ts_monotonic": provenance.acquisition_ts_monotonic,
                "capture_age_known": provenance.acquisition_age_known,
                "acquisition_origin": provenance.acquisition_origin,
                "observation_sequence": sequence,
            },
        )
        observe.provenance = {
            "integrity": provenance.integrity,
            "source": provenance.source,
            "hwnd": int(target.hwnd),
            "pid": int((provenance.identity_after or {}).get("pid") or 0),
            "pinned_hwnd": int(provenance.pinned_hwnd or target.hwnd),
            "identity_before": provenance.identity_before,
            "identity_after": provenance.identity_after,
            "foreground_before": provenance.foreground_before,
            "foreground_after": provenance.foreground_after,
            "capture_ts_monotonic": provenance.acquisition_ts_monotonic,
            "capture_age_known": provenance.acquisition_age_known,
            "acquisition_origin": provenance.acquisition_origin,
            "grab_requested_ts_monotonic": provenance.grab_requested_ts_monotonic,
            "observation_sequence": sequence,
        }
        observe.acquisition = {
            "capture_ts_monotonic": provenance.acquisition_ts_monotonic,
            "capture_age_known": provenance.acquisition_age_known,
            "acquisition_origin": provenance.acquisition_origin,
            "grab_requested_ts_monotonic": provenance.grab_requested_ts_monotonic,
            "observation_sequence": sequence,
        }
        observe.image = image
        return recs, graph, snap, scene_holder

    # Live Q1 keeps the optional cloud VLM off the capture path: the direct
    # perceive(vlm=...) hook is untouched, but a live collect never ships a
    # screenshot to an external provider.
    observe.vlm_model = None
    observe.assembler = assembler
    return observe


def _make_screen_runner(target, scene_holder: list):
    from loop.adaptive.runtime import make_native_primitives_runner
    from loop.os.input_owner import NativeInputOwner

    owner = NativeInputOwner.for_target(target)
    return make_native_primitives_runner(
        owner, get_scene=lambda: scene_holder[0]
    )


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


def _select_bound_game_brief(game: str | None, target) -> GameBrief | None:
    """Resolve automatic context from the target that was actually pinned."""

    return select_game_brief(game, target_title=str(getattr(target, "title", "")))


def _brief_payload(brief: GameBrief | None) -> tuple[dict | None, dict | None]:
    if brief is None:
        return None, None
    return brief.as_dict(), brief.provenance()


def _make_semantic_confirmer(mode: str, brief: GameBrief | None):
    if str(mode or "off").strip().lower() == "off":
        return None
    from loop.adaptive.semantic_confirmation import SemanticConfirmer
    from loop.model.gateway import ProposalGateway

    try:
        gateway = ProposalGateway.from_env()
    except Exception:  # noqa: BLE001 - unavailable confirmation stays withheld
        gateway = None
    return SemanticConfirmer(
        gateway,
        model=str(getattr(gateway, "model", "")),
        game_brief=_brief_payload(brief)[0],
    )


def _semantic_worker_for(semantic_confirmer, game_brief: GameBrief | None):
    """Wrap the configured confirmer in its bounded worker; None when semantic is off."""

    if semantic_confirmer is None:
        return None
    from loop.adaptive.semantic_confirmation import SemanticWorker

    worker = SemanticWorker(semantic_confirmer)
    worker.set_game_brief(_brief_payload(game_brief)[0])
    return worker


def _record_game_brief(session: Session, brief: GameBrief | None) -> None:
    if brief is None:
        return
    session.ledger.append_event("game_brief_selected", brief.provenance())


def _bind_live_session(session: Session, target, window_title: str) -> None:
    from loop.os.window import target_identity

    session.bind_target(target_identity(target, selector=window_title))


def _prepare_live_target(window_title: str, hwnd: int | None = None):
    """Resolve and focus one exact HWND before binding or observing it."""

    from loop.os.window import focus_window_and_settle

    target = _resolve_live_target(window_title, hwnd)
    if not focus_window_and_settle(target):
        raise RuntimeError(
            f"could not focus exact target hwnd={int(target.hwnd)}; refusing observation"
        )
    return target


def _shadow_metrics(frames: list[dict], *, schema_field_ids: tuple[str, ...] = (), warmup: int = 3) -> dict:
    from loop.adaptive.telemetry import qualification_metrics

    return qualification_metrics(
        frames, schema_field_ids=schema_field_ids, warmup=warmup
    )


def _shadow_policy_worker(
    *,
    inference_timeout_s: float,
    preflight_ok: bool | None,
    decider_model: str = "0.8b",
    use_graphs: bool = False,
    qwen_requested: bool = True,
) -> tuple[object | None, object | None, dict]:
    del use_graphs  # Qwen load does not use Decider CUDA graphs.
    from loop.adaptive.controller import Executor
    from loop.adaptive.decider_config import DECIDER_PROFILES
    from loop.adaptive.policy import DeterministicPolicy
    from loop.adaptive.policy_worker import PolicyWorker
    from loop.adaptive.qwen_policy import QwenReactionPolicy

    backend_info: dict = {
        "backend": "deterministic",
        "load_status": "not_requested",
        "decider_model": decider_model,
        "qwen_requested": bool(qwen_requested and preflight_ok is True),
    }
    if not backend_info["qwen_requested"]:
        worker = PolicyWorker(
            DeterministicPolicy(), inference_timeout_s=inference_timeout_s
        )
        return worker, Executor(), backend_info

    backend_info["load_status"] = "loading"
    try:
        model_id, revision = DECIDER_PROFILES[decider_model]
        policy = QwenReactionPolicy.from_pretrained(
            model_id=model_id,
            revision=revision,
            catalog_revision="",
        )
    except Exception as exc:
        backend_info.update(
            {
                "backend": "deterministic",
                "load_status": "failed",
                "load_error": f"{type(exc).__name__}: {exc}",
            }
        )
        return None, None, backend_info

    backend_info.update({"backend": "qwen", "load_status": "ok"})
    worker = PolicyWorker(policy, inference_timeout_s=inference_timeout_s)
    return worker, Executor(), backend_info


def qwen_qualification_gate(backend: dict, *, valid_inferences: int) -> dict:
    """Loaded Qwen plus one accepted inference. ``not_requested`` is not a pass."""

    loaded = backend.get("backend") == "qwen" and backend.get("load_status") == "ok"
    if not loaded:
        return {"pass": False, "reason": str(backend.get("load_status") or "not_requested")}
    if int(valid_inferences) < 1:
        return {"pass": False, "reason": "no_valid_inference"}
    return {"pass": True, "reason": "ok"}


def _stability_gate(metrics: dict, frames: list[dict]) -> dict:
    from loop.adaptive.telemetry import qualification_passed

    trusted = sum(
        1 for frame in frames if str(frame.get("capture_integrity") or "") == "trusted"
    )
    identities = [frame.get("target_identity") for frame in frames]
    ok, reason = qualification_passed(
        metrics, trusted_frames=trusted, identities=identities
    )
    return {"pass": ok, "reason": reason, "trusted_frames": trusted}


def _stamp_capture(row: dict, provenance: dict, identity: dict) -> dict:
    row["capture_integrity"] = str(provenance.get("integrity") or "")
    row["target_identity"] = {
        "hwnd": provenance.get("hwnd", identity.get("hwnd")),
        "pid": provenance.get("pid", identity.get("pid")),
    }
    # Acquisition bound travels with the frame so reports cannot substitute a
    # publication time for capture time.
    row["capture_ts_monotonic"] = provenance.get("capture_ts_monotonic")
    row["capture_age_known"] = bool(provenance.get("capture_age_known"))
    row["acquisition_origin"] = str(provenance.get("acquisition_origin") or "unknown")
    sequence = provenance.get("observation_sequence")
    if sequence is not None:
        row["observation_sequence"] = int(sequence)
    return row


def _capture_report(provenance: dict, identity: dict, target, pid: int) -> dict:
    """Capture block for a summary; carries the real acquisition bound."""

    return {
        "target_identity": dict(identity),
        "source": str(provenance.get("source") or "window"),
        "integrity": str(provenance.get("integrity") or "trusted"),
        "hwnd": int(provenance.get("hwnd") or getattr(target, "hwnd", 0)),
        "pid": pid,
        "capture_ts_monotonic": provenance.get("capture_ts_monotonic"),
        "capture_age_known": bool(provenance.get("capture_age_known")),
        "acquisition_origin": str(provenance.get("acquisition_origin") or "unknown"),
        "observation_sequence": provenance.get("observation_sequence"),
    }


def _qualification_frame_row(
    graph: SceneGraph,
    snap,
    *,
    semantic_required: bool,
    ledger,
    assembler=None,
    schema_field_ids: tuple[str, ...] = (),
) -> dict:
    from loop.adaptive.controller import registry_offer_diagnostics

    offers, exclusions, ambiguity = registry_offer_diagnostics(
        graph, ledger, semantic_required=semantic_required
    )
    ocr_ids = _ocr_node_ids(graph)
    registry_ids = sorted(
        {
            str(node.attributes["registry_id"])
            for node in graph.nodes
            if node.attributes.get("registry_id")
        }
    )
    field_values = dict(snap.fields)
    required_field_status = {
        fid: ("present" if _valid_field_value(field_values.get(fid)) else "unknown")
        for fid in schema_field_ids
    }
    withheld = sum(int(count) for count in (exclusions or {}).values())
    perception = _assembler_perception_diagnostics(assembler)
    return {
        "track_ids": sorted(_track_ids(graph)),
        "ocr_node_ids": ocr_ids,
        "field_keys": ocr_ids,
        "registry_ids": registry_ids,
        "eligible_offer_ids": [item.offer_id for item in offers],
        "exclusion_reasons": exclusions,
        "ambiguity_count": ambiguity,
        "mode_id": snap.mode_id,
        "revision_id": snap.schema_revision_id,
        "surface_action_ids": list(snap.surface.action_ids),
        "field_values": field_values,
        "required_field_status": required_field_status,
        "withheld_candidates": withheld,
        "eligible_offers": len(offers),
        "frame_reused": perception["frame_reused"],
        "cache_hits": perception["cache_hits"],
        "measurement_digest": perception["measurement_digest"],
        "screen_replaced": perception["screen_replaced"],
        "source_diagnostics": perception["source"],
    }


def _apply_semantic_observation(graph: SceneGraph, result) -> None:
    expected_ids = frozenset(st.candidate_id for st in result.candidates)
    current_ids = frozenset(
        str(node.attributes.get("registry_id"))
        for node in graph.nodes
        if node.attributes.get("registry_id")
    )
    if expected_ids != current_ids & expected_ids:
        return
    by_id = {st.candidate_id: st for st in result.candidates}
    for node in graph.nodes:
        rid = str(node.attributes.get("registry_id") or "")
        if rid not in by_id:
            continue
        st = by_id[rid]
        node.attributes["semantic_status"] = st.status
        if st.semantic_class is not None:
            node.attributes["semantic_class"] = st.semantic_class


def _semantic_candidates_from_graph(graph: SceneGraph):
    from loop.adaptive.semantic_confirmation import CandidateEvidence

    out: list[CandidateEvidence] = []
    for node in graph.nodes:
        if str(node.attributes.get("track_state") or "") == "continuing":
            continue
        rid = str(node.attributes.get("registry_id") or "").strip()
        if not rid:
            continue
        box = node.bbox
        token = str(node.attributes.get("appearance_token") or node.node_id or rid)
        out.append(
            CandidateEvidence(
                candidate_id=rid,
                appearance_token=token,
                box=box,
            )
        )
    return tuple(out)


_SEMANTIC_READY = "READY"
_SEMANTIC_PENDING = "PENDING"
_SEMANTIC_SHUTDOWN_TIMEOUT_S = 2.0


def _semantic_status_name(status) -> str:
    return str(getattr(status, "name", status) or "")


def _optional_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


class _LiveSemanticBridge:
    """Bounded, non-blocking semantic driver for the live collectors.

    One worker job per frame. A READY result is applied to the current graph
    only after the session owner validates it against a *later*, distinct,
    trusted observation of the same target, screen generation, schema, brief
    and candidate evidence. Everything else stays withheld, so an unresolved
    required control can never become eligible.

    The screen generation advances only on a real screen replacement reported
    by the assembler -- never on a pixel digest, which is the observation id.
    """

    def __init__(self, worker, *, timeout_s: float | None = None) -> None:
        self.worker = worker
        self._timeout_s = timeout_s
        self._generation = 0
        self._job = None
        self._frame_identity: dict | None = None
        self._origin_sequence: int | None = None
        self._origin_candidates: tuple = ()
        self._last_validated_job = 0
        self.applied = 0
        self.dropped_target_change = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def pending_job_id(self) -> int | None:
        return None if self._job is None else int(self._job.job_id)

    def begin_frame(self, assembler) -> int:
        """Advance the generation on a real replacement and reset unconfirmed work."""

        if getattr(assembler, "screen_replaced", False):
            self._generation += 1
            assembler.screen_replaced = False
            self.worker.clear_pending()
            self._job = None
            self._frame_identity = None
            self._origin_sequence = None
            self._origin_candidates = ()
        return self._generation

    def invalidate_returned(self, graph: SceneGraph) -> None:
        returned = [
            str(node.attributes["registry_id"])
            for node in graph.nodes
            if node.attributes.get("track_state") == "returned"
            and node.attributes.get("registry_id")
        ]
        if returned:
            self.worker.invalidate(returned)

    def step(self, *, graph: SceneGraph, snap, identity, prov, image) -> None:
        """Settle a previous result against this frame, then submit this frame."""

        sequence = _optional_int(prov.get("observation_sequence"))
        trusted = str(prov.get("integrity") or "") == "trusted"
        if self._job is not None:
            self._settle(
                graph=graph,
                snap=snap,
                identity=identity,
                sequence=sequence,
                trusted=trusted,
            )
        if image is None:
            return
        from loop.os.capture import encode_image

        candidates = _semantic_candidates_from_graph(graph)
        if not candidates:
            return
        job = self.worker.submit(
            target_identity=identity,
            screen_generation=self._generation,
            observation_id=graph.snapshot_id,
            candidates=candidates,
            frame=encode_image(image),
            schema_revision=str(getattr(snap, "schema_revision_id", "") or ""),
            timeout_s=self._timeout_s,
        )
        if self._job is None or job.job_id != self._job.job_id:
            self._origin_sequence = sequence
            self._origin_candidates = tuple(
                (candidate.candidate_id, candidate.appearance_token, candidate.box)
                for candidate in job.candidates
            )
        self._job = job
        self._frame_identity = dict(identity)

    def _settle(self, *, graph, snap, identity, sequence, trusted) -> None:
        job = self._job
        if self._frame_identity is not None and dict(identity) != self._frame_identity:
            # The target pinned for the origin observation is not the current one.
            self.dropped_target_change += 1
            self.worker.clear_pending()
            self._job = None
            return
        if job.job_id <= self._last_validated_job:
            self._job = None
            return
        result = self.worker.poll(job.job_id)
        fresh_ok = (
            trusted
            and sequence is not None
            and self._origin_sequence is not None
            and sequence > self._origin_sequence
        )
        current_candidates = tuple(
            (candidate.candidate_id, candidate.appearance_token, candidate.box)
            for candidate in _semantic_candidates_from_graph(graph)
        )
        accepted = self.worker.validate_result(
            result,
            fresh_observation_id=graph.snapshot_id,
            fresh_trusted=fresh_ok,
            target_identity_key=job.target_identity_key,
            screen_generation=self._generation,
            schema_revision=str(getattr(snap, "schema_revision_id", "") or ""),
            brief_digest=job.brief_digest,
            relevant_evidence_changed=current_candidates != self._origin_candidates,
        )
        if (
            _semantic_status_name(accepted.status) == _SEMANTIC_READY
            and accepted.validation_observation_id is not None
        ):
            _apply_semantic_observation(graph, accepted)
            self.applied += 1
            self._last_validated_job = job.job_id
        if _semantic_status_name(result.status) != _SEMANTIC_PENDING:
            self._job = None


def _qualification_summary_envelope(
    *,
    stage: str,
    frames: list[dict],
    session: Session,
    metrics: dict,
    backend_info: dict | None,
    gates: dict,
    capture: dict,
    semantic_mode: str,
    effective_config: dict,
    extra: dict | None = None,
) -> dict:
    schema = session.schema
    summary = {
        "stage": stage,
        "mode": stage,
        "semantic_confirmation": semantic_mode,
        "effective_config": effective_config,
        "backend": backend_info or {"backend": "none", "load_status": "not_used"},
        "gates": gates,
        "capture": capture,
        "metric_denominators": {
            "warmup_frames": metrics.get("warmup_frames", 3),
            "frame_count": len(frames),
            "schema_field_count": len(schema.fields),
        },
        "frames": len(frames),
        **metrics,
        "revision_id": schema.revision.revision_id,
        "fields": [f.field_id for f in schema.fields],
        "actions": [a.action_id for a in schema.actions],
        "workspace": str(session.workspace.resolve()),
    }
    if extra:
        summary.update(extra)
    return summary


def _perception_collect(
    session: Session,
    window_title: str,
    n_steps: int,
    hwnd: int | None = None,
    *,
    semantic_mode: str = "off",
    semantic_confirmer=None,
    tracker_name: str = "none",
    game_brief: GameBrief | None = None,
) -> tuple[int, dict]:
    try:
        target = _prepare_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
        game_brief = _select_bound_game_brief(None if game_brief is None else game_brief.game_id, target)
        _record_game_brief(session, game_brief)
    except (RuntimeError, ValueError) as exc:
        err = {"stage": "perception", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    from loop.adaptive.tracker import open_tracker

    tracker = open_tracker(tracker_name)
    assembler = SceneAssembler(tracker)
    observe = _live_observe_factory(session, target, assembler)
    n_frames = max(1, n_steps)
    frames: list[dict] = []
    traces: list[Trace] = []
    policy = session.schema.policy
    n_obs = max(policy.min_agreeing_reads, 1)
    semantic_required = semantic_mode == "required"
    semantic_worker = _semantic_worker_for(semantic_confirmer, game_brief)
    bridge = None if semantic_worker is None else _LiveSemanticBridge(semantic_worker)
    identity = session.target or {}
    pid = int(identity.get("pid") or 0)

    try:
        for _i in range(n_frames):
            recs, graph, snap, _holder = observe()
            reason = _shadow_schema_abort(session, graph)
            if reason:
                err = {"stage": "perception", "error": reason}
                print(json.dumps(err), file=sys.stderr, flush=True)
                return 2, err
            if bridge is not None:
                bridge.begin_frame(assembler)
                bridge.invalidate_returned(graph)
                bridge.step(
                    graph=graph,
                    snap=snap,
                    identity=identity,
                    prov=getattr(observe, "provenance", {}) or {},
                    image=getattr(observe, "image", None),
                )
            prov = getattr(observe, "provenance", {}) or {}
            frames.append(
                _stamp_capture(
                    _qualification_frame_row(
                        graph,
                        snap,
                        semantic_required=semantic_required,
                        ledger=session.ledger,
                        assembler=assembler,
                        schema_field_ids=tuple(f.field_id for f in session.schema.fields),
                    ),
                    prov,
                    identity,
                )
            )
            traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
            if len(traces) >= n_obs:
                _try_promote(session, traces, recs)
    finally:
        if semantic_worker is not None:
            semantic_worker.shutdown(wait=True, timeout_s=_SEMANTIC_SHUTDOWN_TIMEOUT_S)

    prov = getattr(observe, "provenance", {}) or {}
    metrics = _shadow_metrics(
        frames, schema_field_ids=tuple(f.field_id for f in session.schema.fields)
    )
    stability = _stability_gate(metrics, frames)
    trusted_ok = str(prov.get("integrity") or "") == "trusted"
    gates = {
        "qwen_load": {"pass": False, "reason": "perception_stage_no_reaction_worker"},
        "stability": stability,
        "trusted_capture": {
            "pass": trusted_ok,
            "reason": str(prov.get("integrity") or "missing"),
        },
    }
    summary = _qualification_summary_envelope(
        stage="perception",
        frames=frames,
        session=session,
        metrics=metrics,
        backend_info={"backend": "none", "load_status": "not_used"},
        gates=gates,
        capture=_capture_report(prov, identity, target, pid),
        semantic_mode=semantic_mode,
        effective_config={
            "stage": "perception",
            "semantic_confirmation": semantic_mode,
            "vlm_model": getattr(observe, "vlm_model", None),
            "frames": n_frames,
            "tracker": tracker_name,
            "tracker_status": assembler.tracker_status,
            "tracker_associator": str(getattr(tracker, "associator", "none")),
        },
    )
    rc = 0 if stability["pass"] and trusted_ok else 1
    return rc, summary


def _shadow_collect(
    session: Session,
    window_title: str,
    n_steps: int,
    hwnd: int | None = None,
    *,
    decider_timeout: float = 30.0,
    preflight_ok: bool | None = None,
    decider_model: str = "0.8b",
    use_graphs: bool = False,
    semantic_mode: str = "off",
    semantic_confirmer=None,
    tracker_name: str = "none",
    direct_experiment: bool = False,
    duration_s: float | None = None,
    game_brief: GameBrief | None = None,
) -> tuple[int, dict]:
    try:
        target = _prepare_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
        game_brief = _select_bound_game_brief(None if game_brief is None else game_brief.game_id, target)
        _record_game_brief(session, game_brief)
    except (RuntimeError, ValueError) as exc:
        err = {"stage": "shadow", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    from loop.adaptive.controller import NativeShadowConfig, NativeShadowLoop
    from loop.adaptive.contracts import OperatingState
    from loop.adaptive.telemetry import TelemetryStore
    from loop.os.window import is_foreground

    from loop.adaptive.tracker import open_tracker

    tracker = open_tracker(tracker_name)
    assembler = SceneAssembler(tracker)
    observe = _live_observe_factory(session, target, assembler)
    n_frames = max(1, n_steps)
    frames: list[dict] = []
    traces: list[Trace] = []
    policy = session.schema.policy
    n_obs = max(policy.min_agreeing_reads, 1)
    semantic_required = semantic_mode == "required"

    worker, executor, backend_info = _shadow_policy_worker(
        inference_timeout_s=decider_timeout,
        preflight_ok=preflight_ok,
        decider_model=decider_model,
        use_graphs=use_graphs,
    )
    qwen_gate = qwen_qualification_gate(backend_info, valid_inferences=0)
    if backend_info.get("load_status") == "failed":
        metrics = _shadow_metrics(
            frames, schema_field_ids=tuple(f.field_id for f in session.schema.fields)
        )
        summary = _qualification_summary_envelope(
            stage="shadow",
            frames=frames,
            session=session,
            metrics=metrics,
            backend_info=backend_info,
            gates={
                "qwen_load": qwen_gate,
                "stability": _stability_gate(metrics, frames),
            },
            capture={"target_identity": dict(session.target or {})},
            semantic_mode=semantic_mode,
            effective_config={
                "stage": "shadow",
                "semantic_confirmation": semantic_mode,
                "vlm_model": getattr(observe, "vlm_model", None),
                "decider_model": decider_model,
                "decider_timeout": decider_timeout,
            },
        )
        return 1, summary

    telemetry = TelemetryStore(
        session.workspace / "react.jsonl",
        workspace=session.workspace,
    )
    semantic_worker = _semantic_worker_for(semantic_confirmer, game_brief)
    bridge = None if semantic_worker is None else _LiveSemanticBridge(semantic_worker)
    owner = None
    # H1/H3: --direct-experiment must not arm an input owner. Product input
    # needs a frozen leased scene and a later distinct capture; this flag
    # remains accepted for loop/Qwen observation only.
    shadow = NativeShadowLoop(
        session,
        worker,
        executor,
        telemetry,
        config=NativeShadowConfig(
            decision_deadline_s=decider_timeout,
            snapshot_max_age_s=decider_timeout,
            semantic_required=semantic_required,
            actuate_transient_offers=False,
            game_brief=_brief_payload(game_brief)[0],
        ),
        owner=owner,
        operating_state=(OperatingState.ArmedReact if direct_experiment else None),
    )
    observe.on_transition = shadow.interrupt_for_capture_transition
    identity = session.target or {}
    pid = int(identity.get("pid") or 0)
    prov_last: dict = {}

    try:
        deadline = time.monotonic() + duration_s if duration_s is not None else None
        for _i in range(n_frames if deadline is None else 2_147_483_647):
            if deadline is not None and time.monotonic() >= deadline:
                break
            recs, graph, snap, _holder = observe()
            if deadline is not None:
                deadline += getattr(observe, "transition_wait_s", 0.0)
            reason = _shadow_schema_abort(session, graph)
            if reason:
                err = {"stage": "shadow", "error": reason}
                print(json.dumps(err), file=sys.stderr, flush=True)
                return 2, err
            if bridge is not None:
                bridge.begin_frame(assembler)
                bridge.invalidate_returned(graph)
                bridge.step(
                    graph=graph,
                    snap=snap,
                    identity=identity,
                    prov=getattr(observe, "provenance", {}) or {},
                    image=getattr(observe, "image", None),
                )
            prov = getattr(observe, "provenance", {}) or {}
            prov_last = prov
            integrity = str(prov.get("integrity") or "trusted")
            hwnd_val = int(prov.get("hwnd") or target.hwnd)
            sequence = prov.get("observation_sequence")
            shadow.process_frame(
                snap,
                hwnd=hwnd_val,
                pid=pid,
                capture_integrity=integrity,
                capture_provenance=(str(prov.get("source") or "window"),),
                capture_ts_monotonic=prov.get("capture_ts_monotonic"),
                capture_age_known=(
                    None if "capture_age_known" not in prov
                    else bool(prov.get("capture_age_known"))
                ),
                acquisition_origin=prov.get("acquisition_origin"),
                observation_sequence=(None if sequence is None else int(sequence)),
                focused=is_foreground(target),
                frame_image=getattr(observe, "image", None),
                scene_graph=graph,
            )
            frames.append(
                _stamp_capture(
                    _qualification_frame_row(
                        graph,
                        snap,
                        semantic_required=semantic_required,
                        ledger=session.ledger,
                        assembler=assembler,
                        schema_field_ids=tuple(f.field_id for f in session.schema.fields),
                    ),
                    prov,
                    identity,
                )
            )
            traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
            if len(traces) >= n_obs:
                _try_promote(session, traces, recs)
    finally:
        shadow.close()
        if semantic_worker is not None:
            semantic_worker.shutdown(wait=True, timeout_s=_SEMANTIC_SHUTDOWN_TIMEOUT_S)

    metrics = _shadow_metrics(
        frames, schema_field_ids=tuple(f.field_id for f in session.schema.fields)
    )
    stability = _stability_gate(metrics, frames)
    qwen_gate = qwen_qualification_gate(
        backend_info, valid_inferences=int(shadow.metrics().get("valid_inferences") or 0)
    )
    trusted_ok = str(prov_last.get("integrity") or "") == "trusted"
    summary = _qualification_summary_envelope(
        stage="direct" if direct_experiment else "shadow",
        frames=frames,
        session=session,
        metrics=metrics,
        backend_info=backend_info,
        gates={
            "qwen_load": qwen_gate,
            "stability": stability,
            "trusted_capture": {
                "pass": trusted_ok,
                "reason": str(prov_last.get("integrity") or "missing"),
            },
        },
        capture=_capture_report(prov_last, identity, target, pid),
        semantic_mode=semantic_mode,
        effective_config={
            "stage": "direct" if direct_experiment else "shadow",
            "semantic_confirmation": semantic_mode,
            "vlm_model": getattr(observe, "vlm_model", None),
            "decider_model": decider_model,
            "decider_timeout": decider_timeout,
            "decider_graphs": use_graphs,
            "tracker": tracker_name,
            "tracker_status": assembler.tracker_status,
            "tracker_associator": str(getattr(tracker, "associator", "none")),
        },
        extra=shadow.metrics(),
    )
    rc = 0 if qwen_gate["pass"] and stability["pass"] and trusted_ok else 1
    return rc, summary


def _armed_collect(
    session: Session, window_title: str, n_steps: int, hwnd: int | None = None,
    *, game_brief: GameBrief | None = None,
) -> tuple[int, dict]:
    from loop.adaptive.arm import run_armed_collect

    try:
        target = _prepare_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
        game_brief = _select_bound_game_brief(None if game_brief is None else game_brief.game_id, target)
        _record_game_brief(session, game_brief)
    except (RuntimeError, ValueError) as exc:
        err = {"mode": "armed", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    assembler = SceneAssembler()
    observe = _live_observe_factory(session, target, assembler)
    from loop.adaptive.arm import ArmConfig

    return run_armed_collect(
        session,
        target,
        observe,
        n_steps=n_steps,
        config=ArmConfig(game_brief=_brief_payload(game_brief)[0]),
    )


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
        ok = _synthetic_verify_promoted(session, inv, runner, snap, observe)
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


def _decider_preflight_ok(_args: argparse.Namespace) -> bool | None:
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())


def _react_gates_pass(
    preflight: bool | None, *, target_bound: bool, quality_trusted: bool
) -> bool:
    if not target_bound or not quality_trusted:
        return False
    if preflight is False:
        return False
    if preflight is True:
        return decider_may_arm(True)
    return True


def _initial_operating_state(session: Session) -> OperatingState:
    if session.recovery_state():
        return OperatingState.Halted
    return OperatingState.Boot


def apply_collect_operating_requests(
    current: OperatingState,
    *,
    experiment: bool,
    arm: bool,
    recover: bool,
    observation_stable: bool,
    surface_has_promoted_action: bool,
    preflight_ok: bool | None,
    target_bound: bool,
    quality_trusted: bool = True,
    generation_incremented: bool = False,
) -> OperatingState:
    """Map collect CLI flags to north-star transition requests (no direct state jumps)."""
    state = current
    facts = TransitionFacts(
        model_preflight_ok=preflight_ok,
        observation_stable=observation_stable,
        experiment_armed=experiment,
        surface_has_promoted_action=surface_has_promoted_action,
        react_gates_pass=_react_gates_pass(
            preflight_ok, target_bound=target_bound, quality_trusted=quality_trusted
        ),
        operator_arms=arm,
        explicit_recovery=recover,
        inputs_released=True,
        generation_incremented=generation_incremented,
    )
    if state is OperatingState.Halted and recover:
        state = transition_operating_state(
            state, OperatingTransition.recover_from_halt, facts
        )
    if experiment:
        state = transition_operating_state(
            state, OperatingTransition.arm_experiment, facts
        )
    if surface_has_promoted_action and state is OperatingState.Observe:
        state = transition_operating_state(
            state, OperatingTransition.surface_promoted, facts
        )
    if arm:
        state = transition_operating_state(
            state, OperatingTransition.operator_arm_react, facts
        )
    return state


def _load_scouts(cache_dir: Path):
    from loop.adaptive.experiment import make_gateway_scouts

    return make_gateway_scouts(cache_dir)


def _release_experiment_inputs(target) -> None:
    if target is None:
        return
    from loop.adaptive.arm import create_owner

    try:
        create_owner(target).release_all("experiment_rollback_failed")
    except (RuntimeError, ValueError, OSError):
        pass


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
    semantic_mode: str = "off",
    tracker_name: str = "none",
    tracker_status: str = "none",
    game_brief: GameBrief | None = None,
) -> tuple[int, dict]:
    from loop.adaptive.experiment import (
        ExperimentController,
        admit_plan,
        execute_trial,
        observation_fingerprint,
        plan_allows_input,
        run_scouts,
    )
    from loop.adaptive.orchestrate import make_evidence_bundle

    semantic_required = semantic_mode == "required"
    brief_payload, brief_provenance = _brief_payload(game_brief)
    identity = dict(session.target or {})
    config = {
        "semantic_confirmation": semantic_mode,
        "tracker": tracker_name,
        "tracker_status": tracker_status,
    }

    def _row(scene, snapshot) -> dict:
        prov = getattr(observe, "provenance", {}) or {}
        return _stamp_capture(
            _qualification_frame_row(
                scene,
                snapshot,
                semantic_required=semantic_required,
                ledger=session.ledger,
                assembler=getattr(observe, "assembler", None),
                schema_field_ids=tuple(f.field_id for f in session.schema.fields),
            ),
            prov,
            identity,
        )

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

    frames = [_row(graph, snap)]
    graphs = [graph]
    traces = [Trace(dict(snap.fields), dict(snap.fields), recs, recs)]
    _try_promote(session, traces, recs, kinds=_OBSERVE_KINDS)
    for _ in range(max(0, n_steps - 1)):
        recs, graph, snap, holder = observe()
        frames.append(_row(graph, snap))
        graphs.append(graph)
        traces.append(Trace(dict(snap.fields), dict(snap.fields), recs, recs))
        _try_promote(session, traces, recs, kinds=_OBSERVE_KINDS)

    metrics = _shadow_metrics(frames)
    metrics["frames"] = len(frames)
    stability = _stability_gate(metrics, frames)
    if not stability["pass"]:
        err = {
            "mode": "experiment",
            "error": "stability gate failed",
            "gates": {"stability": stability},
            "effective_config": config,
            **metrics,
        }
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

    ctrl.set_state("propose")
    bundle = make_evidence_bundle(
        image_hash=graph.snapshot_id,
        scene=graph,
        schema=session.schema,
        evidence=recs,
        stability=metrics,
        game_brief=brief_payload,
        game_brief_provenance=brief_provenance,
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
        "gates": {"stability": stability},
        "effective_config": config,
    }
    if plan is None or not arm:
        return 0, summary
    if not plan_allows_input(plan):
        err = {**summary, "error": "plan missing expected effect or rollback", "armed": False}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err

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
        _release_experiment_inputs(target)
        err = {
            "mode": "experiment",
            "error": str(exc),
            "recovery_locked": True,
            "effective_config": config,
        }
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
        ok = _synthetic_verify_promoted(session, inv, legal_runner, snap, observe)
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
    session: Session,
    window_title: str,
    n_steps: int,
    hwnd: int | None,
    arm: bool,
    *,
    semantic_mode: str = "off",
    tracker_name: str = "none",
    game_brief: GameBrief | None = None,
) -> tuple[int, dict]:
    from loop.adaptive.tracker import open_tracker

    try:
        target = _prepare_live_target(window_title, hwnd)
        _bind_live_session(session, target, window_title)
        game_brief = _select_bound_game_brief(None if game_brief is None else game_brief.game_id, target)
        _record_game_brief(session, game_brief)
    except (RuntimeError, ValueError) as exc:
        err = {"mode": "experiment", "error": str(exc)}
        print(json.dumps(err), file=sys.stderr, flush=True)
        return 2, err
    tracker = open_tracker(tracker_name)
    assembler = SceneAssembler(tracker)
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
        semantic_mode=semantic_mode,
        tracker_name=tracker_name,
        tracker_status=str(getattr(tracker, "status", "none")),
        game_brief=game_brief,
    )


def _diagnostics_executable() -> str:
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


def cmd_collect(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive collect session")
    parser.add_argument("--window", default=None, help="live window title substring")
    parser.add_argument("--hwnd", type=int, default=None, help="exact live window handle")
    parser.add_argument(
        "--game",
        default=None,
        help="exact game brief id or alias; otherwise use an exact pinned window title match",
    )
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
    parser.add_argument(
        "--direct-experiment",
        action="store_true",
        help="execute current provisional offers without promotion or shadow qualification",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="wall-clock action-loop budget for --direct-experiment (maximum 30)",
    )
    add_decider_args(parser)
    parser.add_argument(
        "--stage",
        choices=("perception", "shadow"),
        default="shadow",
        help="qualification stage: perception (observe/metrics only) or shadow (hypothetical react)",
    )
    parser.add_argument(
        "--semantic-confirmation",
        choices=("off", "required"),
        default="off",
        help="optional cloud semantic confirmation for offer eligibility",
    )
    parser.add_argument(
        "--tracker",
        type=_tracker_arg,
        default="none",
        help="live perception: none (legacy), fused-gpa (experimental GPA+OCR+cards) or student:<concept library dir> (taught concepts); other detector arms are benchmark-only",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="explicit recovery from Halted after release and generation bump",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="open the live read-only diagnostics side panel",
    )
    args = parser.parse_args(argv)

    if args.direct_experiment and (not args.window or args.synthetic):
        print(
            json.dumps({"error": "--direct-experiment requires --window"}),
            file=sys.stderr,
            flush=True,
        )
        return 2
    if args.direct_experiment and not (0 < args.duration_seconds <= 30):
        print(
            json.dumps({"error": "--duration-seconds must be in (0, 30]"}),
            file=sys.stderr,
            flush=True,
        )
        return 2

    if args.arm and not args.window and not args.synthetic:
        print(json.dumps({"error": "--arm requires --window"}), file=sys.stderr, flush=True)
        return 2

    if args.diagnostics:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            [
                _diagnostics_executable(),
                "-m",
                "loop",
                "diagnostics",
                "--workspace",
                str(args.workspace),
            ],
            cwd=Path.cwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )

    session = Session(args.workspace, window_selector=args.window)
    try:
        game_brief = select_game_brief(args.game) if args.game is not None else None
    except GameBriefError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr, flush=True)
        return 2
    semantic_confirmer = _make_semantic_confirmer(
        str(args.semantic_confirmation), game_brief
    )
    if args.synthetic or args.window is None:
        _record_game_brief(session, game_brief)
    preflight_ok = _decider_preflight_ok(args)
    state = _initial_operating_state(session)
    if state is OperatingState.Boot:
        state = after_preflight(True, preflight_ok)
    target_bound = bool(args.window or args.synthetic or session.target)
    surface_has_actions = bool(session.schema.actions)
    generation_inc = bool(args.recover and session.recovery_state() is None)
    if args.recover and session.recovery_state():
        session.recovery_clear()
        generation_inc = True
    state = apply_collect_operating_requests(
        state,
        experiment=bool(args.experiment),
        arm=bool(args.arm),
        recover=bool(args.recover),
        observation_stable=True,
        surface_has_promoted_action=surface_has_actions,
        preflight_ok=preflight_ok,
        target_bound=target_bound,
        generation_incremented=generation_inc,
    )
    operating_state = state

    if args.experiment:
        if args.synthetic or args.window is None:
            rc, summary = _synthetic_experiment(session, max(3, args.steps), args.arm)
        else:
            rc, summary = _live_experiment(
                session,
                args.window,
                max(30, args.steps),
                args.hwnd,
                args.arm,
                semantic_mode=str(args.semantic_confirmation),
                tracker_name=str(args.tracker),
                game_brief=game_brief,
            )
        session.write_step_log(args.workspace / "summary.json", summary)
        if rc == 0:
            summary = {**summary, "operating_state": operating_state.value}
            print(json.dumps(summary, indent=2), flush=True)
        return rc

    if args.synthetic or args.window is None:
        summary = _synthetic_collect(session, max(1, args.steps))
        summary = {**summary, "operating_state": operating_state.value}
        session.write_step_log(args.workspace / "summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    if args.direct_experiment:
        operating_state = OperatingState.ArmedReact
        rc, summary = _shadow_collect(
            session,
            args.window,
            max(1, args.steps),
            args.hwnd,
            decider_timeout=float(args.decider_timeout),
            preflight_ok=preflight_ok,
            decider_model=str(args.decider_model),
            use_graphs=bool(args.decider_graphs),
            semantic_mode=str(args.semantic_confirmation),
            semantic_confirmer=semantic_confirmer,
            tracker_name=str(args.tracker),
            direct_experiment=True,
            duration_s=float(args.duration_seconds),
            game_brief=game_brief,
        )
    elif args.arm:
        if operating_state is not OperatingState.ArmedReact:
            if args.window:
                try:
                    target = _resolve_live_target(args.window, args.hwnd)
                    from loop.adaptive.arm import create_owner

                    owner = create_owner(target)
                    owner.enable_actuation()
                    owner.release_all("arm_rejected_not_shadow_react")
                except (RuntimeError, ValueError):
                    pass
            err = {
                "error": "collect --arm requires ShadowReact with gates passed",
                "operating_state": operating_state.value,
            }
            print(json.dumps(err), file=sys.stderr, flush=True)
            return 2
        rc, summary = _armed_collect(
            session,
            args.window,
            max(1, args.steps),
            args.hwnd,
            game_brief=game_brief,
        )
    elif args.stage == "perception":
        rc, summary = _perception_collect(
            session,
            args.window,
            max(1, args.steps),
            args.hwnd,
            semantic_mode=str(args.semantic_confirmation),
            semantic_confirmer=semantic_confirmer,
            tracker_name=str(args.tracker),
            game_brief=game_brief,
        )
    else:
        rc, summary = _shadow_collect(
            session,
            args.window,
            max(1, args.steps),
            args.hwnd,
            decider_timeout=float(args.decider_timeout),
            preflight_ok=preflight_ok,
            decider_model=str(args.decider_model),
            use_graphs=bool(args.decider_graphs),
            semantic_mode=str(args.semantic_confirmation),
            semantic_confirmer=semantic_confirmer,
            tracker_name=str(args.tracker),
            game_brief=game_brief,
        )

    session.write_step_log(args.workspace / "summary.json", summary)
    if rc == 0:
        summary = {**summary, "operating_state": operating_state.value}
        print(json.dumps(summary, indent=2), flush=True)
    return rc

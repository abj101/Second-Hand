"""Student recognizer: training-free DINOv2 template matching taught from concept exemplars.

Dense DINOv2-small patch features -> exemplar kernels at a few scales -> cosine template
matching -> per-concept NMS -> class-agnostic NMS. A concept has several appearance
prototypes, each with its own calibrated threshold; overlapping same-instance duplicates are
suppressed; small concepts may use a 2x-resolution pass. Boxes are normalized 0..1.
Pixels in, boxes out: nothing here is an action or a click coordinate.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from loop.adaptive import concepts as cl
from loop.adaptive.tracker import TrackHit, TrackMemory

BACKEND = "dinov2-small-template"
PATCH, GW, GH = 14, 64, 37  # 896 x 518 input -> 64 x 37 patch grid
HI = 2  # high-resolution pass multiplier
MIN_SIM, SCALES = 0.3, (0.85, 1.0, 1.2)
SMALL_CELLS = 4  # median exemplar spans fewer coarse cells than this in either axis -> small
NMS_IOU, NMS_CAP, AGNOSTIC_IOU = 0.4, 40, 0.7
CLUSTER_COS, MAX_PROTOTYPES = 0.9, 8
CONTAIN_CHOICES = (0.0, 0.8, 0.7, 0.6, 0.5)  # 0 = off; first strict F1 winner is kept
DEFAULT_CONTAIN, DEFAULT_THRESHOLD, THRESHOLD_MARGIN = 0.6, 0.8, 0.03
CAL_IOU = 0.3  # calibration asks 'same object?', looser than box quality: teacher extents are noisy
MIN_EVIDENCE = 3  # calibrated objects a prototype needs before it gets its own threshold
TRACK_MAX_DISTANCE, TRACK_RETURN_MARGIN = 0.25, 0.02

_MODELS: dict = {}


@dataclass(frozen=True)
class Detection:
    concept_id: str
    bbox: tuple[float, float, float, float]
    score: float
    prototype_id: str
    embedding: np.ndarray = field(compare=False, repr=False, default=None)


# ---------------------------------------------------------------- pure logic (no torch)


def cluster_embeddings(embs, merge: float = CLUSTER_COS, cap: int = MAX_PROTOTYPES) -> list[list[int]]:
    """Greedy cosine clustering: join the first group whose centroid is >= `merge`, else open one;
    then merge the closest pair until at most `cap` groups remain."""

    e = np.asarray(embs, np.float32)
    e = e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)

    def cent(g):
        c = e[g].mean(0)
        return c / max(float(np.linalg.norm(c)), 1e-9)

    groups: list[list[int]] = []
    for i in range(len(e)):
        for g in groups:
            if float(cent(g) @ e[i]) >= merge:
                g.append(i)
                break
        else:
            groups.append([i])
    while len(groups) > cap:
        a, b = max(
            ((a, b) for a in range(len(groups)) for b in range(a + 1, len(groups))),
            key=lambda ab: float(cent(groups[ab[0]]) @ cent(groups[ab[1]])),
        )
        groups[a] += groups.pop(b)
    return groups


def pick_threshold(scores, is_tp, n_gt: int, margin: float = THRESHOLD_MARGIN) -> float | None:
    """Score cut maximizing F1 over detections (is_tp) against n_gt objects; None without evidence.
    The lowest score on the best-F1 plateau, minus a margin: calibration sets are small, so unseen true positives score lower."""

    s, t = np.asarray(scores, np.float64), np.asarray(is_tp, bool)
    if n_gt <= 0 or not len(s) or not t.any():
        return None
    o = np.argsort(-s, kind="stable")
    s, t = s[o], t[o]
    f1 = 2 * np.cumsum(t) / (np.arange(1, len(s) + 1) + n_gt)  # 2TP / (2TP + FP + FN)
    ends = np.r_[s[1:] < s[:-1], True]  # a cut sits between distinct scores
    best = f1[ends].max()
    idx = np.flatnonzero(ends & (f1 >= best - 1e-9))
    return max(MIN_SIM, float(s[idx[-1]] - margin))


def suppress_duplicates(boxes, scores, contain: float) -> np.ndarray:
    """Indices kept, best score first: drop a box whose center lies in a better kept box that
    covers >= `contain` of the smaller area (a shifted duplicate of one instance, not a neighbour)."""

    b, keep = np.asarray(boxes, np.float64), []
    area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    for j in np.argsort(-np.asarray(scores), kind="stable"):
        cx, cy = (b[j, 0] + b[j, 2]) / 2, (b[j, 1] + b[j, 3]) / 2
        for i in keep:
            if not (b[i, 0] <= cx <= b[i, 2] and b[i, 1] <= cy <= b[i, 3]):
                continue
            inter = max(0.0, min(b[i, 2], b[j, 2]) - max(b[i, 0], b[j, 0])) * max(
                0.0, min(b[i, 3], b[j, 3]) - max(b[i, 1], b[j, 1])
            )
            if inter / max(min(area[i], area[j]), 1e-12) >= contain:
                break
        else:
            keep.append(int(j))
    return np.asarray(keep, np.int64)


def _iou(a, b) -> float:
    ix, iy = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def _center_in(p, g) -> bool:
    return g[0] <= (p[0] + p[2]) / 2 <= g[2] and g[1] <= (p[1] + p[3]) / 2 <= g[3]


def match_boxes(preds, gts, small: bool, scores=None, min_iou: float = 0.5) -> list[int]:
    """One-to-one greedy match, pred -> gt index or -1. IoU >= min_iou, or center-in-box when small.
    Pairs rank by IoU, or by detection score when given (the better detection claims the object)."""

    pairs = []
    for j, p in enumerate(preds):
        for i, g in enumerate(gts):
            s = _iou(p, g)
            if (s >= min_iou and not small) or (small and _center_in(p, g)):
                pairs.append(((float(scores[j]) if scores is not None else s + _center_in(p, g)), i, j))
    out, used = [-1] * len(preds), set()
    for _, i, j in sorted(pairs, reverse=True):
        if i not in used and out[j] < 0:
            used.add(i)
            out[j] = i
    return out


def f1_counts(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def prototype_thresholds(frames, n_protos: int) -> list[float | None]:
    """frames: per frame (scores, proto_idx, gt_idx or -1 per detection, n_gt). A GT belongs to the
    prototype that matched it; a detection matching another prototype's GT is ignored, an unmatched
    one is a false positive. Returns per-prototype thresholds, None where there is no evidence."""

    rows: list[list[tuple[float, bool]]] = [[] for _ in range(n_protos)]
    n_gt = [0] * n_protos
    for scores, pidx, gt, _n in frames:
        owner = {g: p for g, p in zip(gt, pidx) if g >= 0}
        for p in owner.values():
            n_gt[p] += 1
        for s, p, g in zip(scores, pidx, gt):
            if g < 0:
                rows[p].append((float(s), False))
            elif owner[g] == p:
                rows[p].append((float(s), True))
    return [
        pick_threshold([s for s, _ in r], [t for _, t in r], n_gt[p]) if n_gt[p] >= MIN_EVIDENCE else None
        for p, r in enumerate(rows)
    ]


def concept_threshold(frames) -> float | None:
    """Best single threshold for a whole concept (fallback for prototypes without evidence)."""

    rows = [(float(s), g >= 0) for sc, _p, gt, _n in frames for s, g in zip(sc, gt)]
    return pick_threshold([s for s, _ in rows], [t for _, t in rows], sum(n for *_, n in frames))


def cosine_distance(a: bytes, b: bytes) -> float:
    if not a or len(a) != len(b):
        return float("inf")
    x, y = np.frombuffer(a, np.float16).astype(np.float32), np.frombuffer(b, np.float16).astype(np.float32)
    return float(1.0 - x @ y / max(float(np.linalg.norm(x) * np.linalg.norm(y)), 1e-9))


# ---------------------------------------------------------------- DINO features


def _torch():
    import torch
    import torch.nn.functional as F

    return torch, F


def _model(device: str):
    if device not in _MODELS:
        torch, _ = _torch()
        from transformers import AutoModel

        try:
            m = AutoModel.from_pretrained("facebook/dinov2-small", local_files_only=True)
        except OSError:
            m = AutoModel.from_pretrained("facebook/dinov2-small")
        mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225], device=device).view(3, 1, 1)
        _MODELS[device] = (m.to(device).eval().half(), mean, std)
    return _MODELS[device]


def features(image: Image.Image, device: str, mult: int = 1):
    """L2-normalized patch features (C, gh, gw) of the frame resized to the (GW*mult, GH*mult) grid."""

    torch, F = _torch()
    gw, gh = GW * mult, GH * mult
    model, mean, std = _model(device)
    with torch.no_grad():
        x = torch.from_numpy(np.array(image.convert("RGB").resize((gw * PATCH, gh * PATCH), Image.BICUBIC)))
        x = ((x.to(device).permute(2, 0, 1).float() / 255 - mean) / std).half()[None]
        tok = model(pixel_values=x).last_hidden_state[0, 1:].float()
        return F.normalize(tok, dim=-1).view(gh, gw, -1).permute(2, 0, 1)


def _grid_box(b, gw: int, gh: int) -> list[int]:
    x0, y0 = int(round(b[0] * gw)), int(round(b[1] * gh))
    x1, y1 = max(x0 + 1, int(round(b[2] * gw))), max(y0 + 1, int(round(b[3] * gh)))
    return [min(x0, gw - 1), min(y0, gh - 1), min(x1, gw), min(y1, gh)]


def _integral(fm):
    torch, F = _torch()
    return F.pad(fm.permute(1, 2, 0).cumsum(0).cumsum(1), (0, 0, 1, 0, 1, 0))


def _pool(ii, boxes):  # boxes: normalized (N,4) tensor -> unit mean features (N, C)
    torch, F = _torch()
    gh, gw = ii.shape[0] - 1, ii.shape[1] - 1
    g = torch.tensor([_grid_box(b, gw, gh) for b in boxes.tolist()], device=ii.device).view(-1, 4)
    x0, y0, x1, y1 = g.unbind(1)
    return F.normalize(ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0], dim=-1)


def _kernels(fm, box) -> list:
    """Exemplar feature grid cropped at `box`, resampled at each scale."""

    torch, F = _torch()
    gh, gw = fm.shape[1:]
    x0, y0, x1, y1 = _grid_box(box, gw, gh)
    k, out = fm[:, y0:y1, x0:x1], []
    for s in SCALES:
        hh, ww = max(1, round(k.shape[1] * s)), max(1, round(k.shape[2] * s))
        if hh <= gh and ww <= gw:
            out.append(F.normalize(F.interpolate(k[None], size=(hh, ww), mode="bilinear", align_corners=False)[0], dim=0))
    return out


# ---------------------------------------------------------------- matching core


@dataclass
class _Proto:
    prototype_id: str
    threshold: float
    coarse: list  # (C,h,w) kernels on device
    hi: list = field(default_factory=list)
    exemplar_indices: tuple = ()


@dataclass
class _Concept:
    concept_id: str
    kind: str
    small: bool = False  # median exemplar is under SMALL_CELLS: eligible for the hi-res pass
    hires: bool = False  # this concept matches on the hi-res grid
    contain: float = 0.0  # duplicate-suppression containment; 0 = off
    protos: list[_Proto] = field(default_factory=list)


def _match_grid(fm, kernels_by_proto: list[list], floor: float, size: tuple[int, int]):
    """Template matching on one feature grid -> pixel boxes, scores, prototype index.
    Pixel units keep NMS tie-breaking identical to the Phase 0 script."""

    torch, F = _torch()
    gh, gw = fm.shape[1:]
    B, S, P = [], [], []
    for pi, ks in enumerate(kernels_by_proto):
        for k in ks:
            hh, ww = k.shape[1:]
            sim = F.conv2d(fm[None], k[None])[0, 0] / (hh * ww)
            ys, xs = torch.nonzero(sim >= floor, as_tuple=True)
            if not len(ys):
                continue
            S.append(sim[ys, xs])
            P.append(torch.full_like(ys, pi))
            B.append(torch.stack([xs, ys, xs + ww, ys + hh], 1).float())
    if not B:
        return None
    scale = torch.tensor([size[0] / gw, size[1] / gh, size[0] / gw, size[1] / gh], device=fm.device)
    return torch.cat(B) * scale, torch.cat(S), torch.cat(P)


class _Core:
    def __init__(self, concepts: list[_Concept], device: str) -> None:
        self.device, self.concepts = device, concepts

    def candidates(self, image: Image.Image, hires: bool | None = None):
        """Every window >= MIN_SIM per concept (before thresholds/NMS), plus coarse features.
        `hires` forces the small concepts onto one grid (calibration); None uses each concept's choice."""

        use = lambda c: c.small and hires if hires is not None else c.hires  # noqa: E731
        fm = features(image, self.device)
        hi = features(image, self.device, HI) if any(use(c) for c in self.concepts) else None
        out = {}
        for c in self.concepts:
            grid, kernels = (hi, [p.hi for p in c.protos]) if use(c) else (fm, [p.coarse for p in c.protos])
            r = _match_grid(grid, kernels, MIN_SIM, image.size)
            if r is not None:
                out[c.concept_id] = r
        return out, fm

    def select(self, c: _Concept, cand, apply_thresholds: bool, contain: float):
        """Thresholds -> NMS -> duplicate suppression for one concept. Tensors on device."""

        torch, _ = _torch()
        from torchvision.ops import nms

        boxes, scores, pidx = cand
        if apply_thresholds:
            keep = scores >= torch.tensor([p.threshold for p in c.protos], device=scores.device)[pidx]
            boxes, scores, pidx = boxes[keep], scores[keep], pidx[keep]
        if not len(scores):
            return None
        k = nms(boxes, scores, NMS_IOU)[:NMS_CAP]
        boxes, scores, pidx = boxes[k], scores[k], pidx[k]
        if contain > 0 and len(k) > 1:
            k = torch.from_numpy(suppress_duplicates(boxes.cpu().numpy(), scores.cpu().numpy(), contain)).to(boxes.device)
            boxes, scores, pidx = boxes[k], scores[k], pidx[k]
        return boxes, scores, pidx

    def detect(self, image: Image.Image, *, apply_thresholds: bool = True) -> list[Detection]:
        torch, _ = _torch()
        from torchvision.ops import nms

        cands, fm = self.candidates(image)
        by_id = {c.concept_id: c for c in self.concepts}
        B, S, P, C = [], [], [], []
        for cid, cand in cands.items():
            r = self.select(by_id[cid], cand, apply_thresholds, by_id[cid].contain)
            if r is not None:
                B.append(r[0]); S.append(r[1]); P.append(r[2]); C += [cid] * len(r[1])
        if not B:
            return []
        B, S, P = torch.cat(B), torch.cat(S), torch.cat(P)
        keep = nms(B, S, AGNOSTIC_IOU)
        B = B[keep] / torch.tensor([*image.size, *image.size], device=B.device)
        emb = _pool(_integral(fm), B).half().cpu().numpy()
        B, S, P = B.cpu().numpy(), S[keep].cpu().numpy(), P[keep].cpu().numpy()
        out = []
        for n, i in enumerate(keep.tolist()):
            c = by_id[C[i]]
            out.append(
                Detection(C[i], tuple(float(v) for v in B[n]), float(S[n]), c.protos[int(P[n])].prototype_id, emb[n])
            )
        return out


# ---------------------------------------------------------------- compile


def _small(exemplars) -> bool:
    w = np.median([(e.bbox[2] - e.bbox[0]) * GW for e in exemplars])
    h = np.median([(e.bbox[3] - e.bbox[1]) * GH for e in exemplars])
    return bool(min(w, h) < SMALL_CELLS)  # thin objects (bars, buttons) need the finer grid too


def _build(lib, library_dir, device: str, mode: str) -> _Core:
    torch, _ = _torch()
    frames: dict[str, tuple] = {}

    def feats(digest, hi):
        if digest not in frames:
            frames[digest] = [None, None]
        slot = frames[digest]
        if slot[hi] is None:
            slot[hi] = features(cl.load_frame(library_dir, digest), device, HI if hi else 1)
        return slot[hi]

    out = []
    for c in lib.concepts:
        pos = [i for i, e in enumerate(c.exemplars) if e.positive]
        if not pos:
            continue
        full = mode == "full"
        small = full and _small([c.exemplars[i] for i in pos])
        emb = _pool_each([(feats(c.exemplars[i].frame_digest, 0), c.exemplars[i].bbox) for i in pos])
        groups = [[pos[j] for j in g] for g in cluster_embeddings(emb)] if full else [pos]
        con = _Concept(c.concept_id, c.kind, small, small, DEFAULT_CONTAIN if full else 0.0)
        for n, g in enumerate(groups):
            coarse = [k for i in g for k in _kernels(feats(c.exemplars[i].frame_digest, 0), c.exemplars[i].bbox)]
            hi = [k for i in g for k in _kernels(feats(c.exemplars[i].frame_digest, 1), c.exemplars[i].bbox)] if small else []
            thr = DEFAULT_THRESHOLD if full else MIN_SIM
            con.protos.append(_Proto(f"{c.concept_id}.p{n}", thr, coarse, hi, tuple(g)))
        out.append(con)
    return _Core(out, device)


def _pool_each(items) -> np.ndarray:
    return np.stack([_pool(_integral(fm), _t(b))[0].cpu().numpy() for fm, b in items])


def _t(b):
    torch, _ = _torch()
    return torch.tensor([b], dtype=torch.float32)


def _fit(core: _Core, c: _Concept, data) -> float | None:
    """Set c's prototype thresholds and duplicate containment from calibration data; returns the
    resulting F1, or None when the concept never appears in the calibration frames."""

    per = []  # raw (scores, proto idx, gt idx, n_gt) per frame
    for cands, gts, norm in data:
        gt = gts.get(c.concept_id, [])
        r = core.select(c, cands[c.concept_id], False, 0.0) if c.concept_id in cands else None
        if r is None:
            per.append((np.zeros(0), np.zeros(0, np.int64), [], len(gt)))
            continue
        b, s, p = (t.cpu().numpy() for t in r)
        per.append((s, p, match_boxes(b / norm, gt, c.small, s, CAL_IOU), len(gt)))
    if not any(n for *_, n in per):
        return None
    fallback = concept_threshold(per) or DEFAULT_THRESHOLD
    for proto, t in zip(c.protos, prototype_thresholds(per, len(c.protos))):
        proto.threshold = fallback if t is None else t
    best, c.contain = -1.0, 0.0
    for contain in CONTAIN_CHOICES:
        tp = fp = fn = 0
        for cands, gts, norm in data:
            gt = gts.get(c.concept_id, [])
            r = core.select(c, cands[c.concept_id], True, contain) if c.concept_id in cands else None
            m = match_boxes(r[0].cpu().numpy() / norm, gt, c.small, r[1].cpu().numpy(), CAL_IOU) if r is not None else []
            hit = sum(1 for g in m if g >= 0)
            tp += hit; fp += len(m) - hit; fn += len(gt) - hit
        f = f1_counts(tp, fp, fn)
        if f > best + 1e-9:
            best, keep = f, contain
    c.contain = keep
    return best


def _calibrate(core: _Core, calibration) -> None:
    """Per-prototype thresholds, duplicate containment and (for small concepts) the grid, all from
    teacher-labelled frames. Hi-res is used unless coarse is strictly better on F1."""

    frames = list(calibration)
    meta = []
    for img, objs in frames:
        gts: dict[str, list] = {}
        for o in objs:
            gts.setdefault(o["category"], []).append(tuple(o["bbox"]))
        meta.append((gts, np.array([*img.size, *img.size], np.float64)))
    coarse = [(core.candidates(img, False)[0], *m) for (img, _), m in zip(frames, meta)]
    hi = [(core.candidates(img, True)[0], *m) for (img, _), m in zip(frames, meta)] if any(c.small for c in core.concepts) else []
    for c in core.concepts:
        c.hires = False
        f_coarse = _fit(core, c, coarse)
        if not c.small or f_coarse is None:
            continue
        kept = ([p.threshold for p in c.protos], c.contain)
        c.hires = True
        if (_fit(core, c, hi) or 0.0) < f_coarse:
            c.hires = False
            for p, t in zip(c.protos, kept[0]):
                p.threshold = t
            c.contain = kept[1]


def compile_library(lib, library_dir, *, calibration=(), mode="full"):
    """Exemplars -> DINO kernel artifacts (one content-hashed file per concept) -> new revision."""

    torch, _ = _torch()
    if mode not in ("full", "phase0"):
        raise ValueError(f"unknown mode: {mode}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    core = _build(lib, library_dir, device, mode)
    if mode == "full" and calibration:
        _calibrate(core, list(calibration))
    students = {}
    for con in core.concepts:
        buf = io.BytesIO()
        cpu = lambda ks: [k.cpu() for k in ks]  # noqa: E731
        torch.save({p.prototype_id: {"coarse": cpu(p.coarse), "hi": cpu(p.hi)} for p in con.protos}, buf)
        sha = cl.store_artifact(library_dir, buf.getvalue(), ".pt")
        protos = tuple(cl.Prototype(p.prototype_id, p.exemplar_indices, round(p.threshold, 4)) for p in con.protos)
        cfg = {"mode": mode, "small": con.small, "hires": con.hires, "contain": con.contain, "kind": con.kind}
        students[con.concept_id] = cl.StudentArtifact(BACKEND, sha, protos, cfg)
    return cl.with_students(lib, students)


# ---------------------------------------------------------------- runtime


def _load_core(library_dir, revision_id, device: str) -> _Core:
    torch, _ = _torch()
    lib = cl.load(Path(library_dir), revision_id)
    out = []
    for c in lib.concepts:
        st = c.student
        if st is None or st.backend != BACKEND:
            continue
        data = torch.load(cl.artifact_path(Path(library_dir), st.artifact_sha256), map_location=device, weights_only=True)
        cfg = st.config
        con = _Concept(c.concept_id, c.kind, bool(cfg.get("small")), bool(cfg.get("hires")), float(cfg.get("contain", 0.0)))
        for p in st.prototypes:
            d = data[p.prototype_id]
            con.protos.append(_Proto(p.prototype_id, p.threshold, [k.float() for k in d["coarse"]], [k.float() for k in d["hi"]]))
        out.append(con)
    return _Core(out, device)


class StudentRecognizer(_Core):
    def __init__(self, library_dir, revision_id=None, device: str = "cuda") -> None:
        core = _load_core(library_dir, revision_id, device)
        super().__init__(core.concepts, device)


class StudentTracker:
    """SceneAssembler tracker arm: student detections -> TrackMemory with embedding identity."""

    associator = "student"

    def __init__(self, library_dir, revision_id=None, device: str = "cuda") -> None:
        t0 = time.perf_counter()
        self.diagnostics: dict = {"available": False}
        self.recognizer = None
        try:
            self.recognizer = StudentRecognizer(library_dir, revision_id, device)
            # First CUDA pass costs ~5 s (kernel autotune); pay it at load, not on the first See.
            self.recognizer.detect(Image.new("RGB", (1280, 720)))
            self.status = "ok"
        except Exception as exc:  # status is the contract; the loop never sees a raise
            self.status = f"student unavailable: {type(exc).__name__}: {exc}"
        self.load_ms = (time.perf_counter() - t0) * 1000.0
        self._kinds = {c.concept_id: c.kind for c in self.recognizer.concepts} if self.recognizer else {}
        self._mem = TrackMemory(distance=cosine_distance, max_distance=TRACK_MAX_DISTANCE, return_margin=TRACK_RETURN_MARGIN)

    def clear_live(self) -> None:
        self._mem.clear_live()

    def reset(self) -> None:
        self._mem.reset()

    def update(self, image: Image.Image) -> tuple[TrackHit, ...]:
        if self.status != "ok":
            return ()
        t0 = time.perf_counter()
        try:
            dets = self.recognizer.detect(image)
        except Exception as exc:
            self.diagnostics = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            return ()
        raw = [
            (d.bbox, d.concept_id, d.score, d.embedding.tobytes(), None, self._kinds.get(d.concept_id, "unknown"))
            for d in dets
        ]
        per: dict[str, int] = {}
        for d in dets:
            per[d.concept_id] = per.get(d.concept_id, 0) + 1
        self.diagnostics = {
            "available": True,
            "frame_ms": (time.perf_counter() - t0) * 1000.0,
            "detections": len(dets),
            "per_concept": per,
        }
        return self._mem.reconcile(raw)

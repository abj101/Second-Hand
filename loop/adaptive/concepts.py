"""Concept library (schema v2): taught pixel evidence plus compiled student artifacts.

A library is one immutable, content-hashed revision per game. Teaching or
compiling never edits a revision in place; it stamps a child revision. Teacher
boxes are evidence only: nothing here is an action, a click or a coordinate the
control plane may actuate.

Layout of a library directory:
    <dir>/<revision_id>.json   one file per revision, HEAD.json names the current one
    <dir>/frames/<digest>.png   exemplar frames, keyed by pixel digest
    <dir>/artifacts/<sha>.*     student artifacts, keyed by content hash
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from loop.adaptive.schema import SchemaRevision, _atomic_replace, _to_plain, hash_payload, write_head

BBox = tuple[float, float, float, float]  # normalized 0..1 (x0, y0, x1, y1)
KINDS = ("entity", "control", "field", "screen_marker")
STATUSES = ("hypothesis", "provisional", "promoted", "retired")


@dataclass(frozen=True)
class Exemplar:
    frame_digest: str
    bbox: BBox
    positive: bool = True
    label: str = ""  # teacher's instance text (e.g. "Defend"); advisory
    source: str = "teacher"


@dataclass(frozen=True)
class Prototype:
    """One appearance mode of a concept with its own calibrated threshold."""

    prototype_id: str
    exemplar_indices: tuple[int, ...]  # into Concept.exemplars
    threshold: float


@dataclass(frozen=True)
class StudentArtifact:
    backend: str  # e.g. "dinov2-small-template"
    artifact_sha256: str  # file under <dir>/artifacts/
    prototypes: tuple[Prototype, ...]
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Concept:
    concept_id: str
    kind: str
    name: str  # teacher hypothesis; advisory text only
    exemplars: tuple[Exemplar, ...] = ()
    student: StudentArtifact | None = None
    validation: dict = field(default_factory=dict)
    status: str = "hypothesis"


@dataclass(frozen=True)
class ConceptLibrary:
    revision: SchemaRevision
    game_id: str  # rule 8: a library never spans games
    concepts: tuple[Concept, ...] = ()

    def concept(self, concept_id: str) -> Concept | None:
        return next((c for c in self.concepts if c.concept_id == concept_id), None)


def frame_digest(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    return hashlib.sha256(f"{rgb.size}".encode() + rgb.tobytes()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp(lib: ConceptLibrary, parent_id: str | None) -> ConceptLibrary:
    rid, ts = str(uuid.uuid4()), _now()
    draft = replace(lib, revision=SchemaRevision(rid, parent_id, ts, ""))
    return replace(draft, revision=SchemaRevision(rid, parent_id, ts, hash_payload(_to_plain(draft))))


def empty_library(game_id: str) -> ConceptLibrary:
    if not game_id:
        raise ValueError("game_id required")
    return _stamp(ConceptLibrary(SchemaRevision("", None, "", ""), game_id), None)


def _child(lib: ConceptLibrary, concepts: tuple[Concept, ...]) -> ConceptLibrary:
    return _stamp(replace(lib, concepts=concepts), lib.revision.revision_id)


def teach(
    lib: ConceptLibrary,
    image: Image.Image,
    objects: list[dict],
    *,
    library_dir: Path,
    kinds: dict[str, str] | None = None,
    source: str = "teacher",
) -> ConceptLibrary:
    """Teacher output on one frame -> exemplars in a new revision.

    `objects` are {"category", "bbox" (pixel xyxy), "label"?, "positive"?}.
    The frame is stored by digest so the student can recompile from evidence.
    """

    w, h = image.size
    digest = frame_digest(image)
    frames = Path(library_dir) / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    if not (frames / f"{digest}.png").exists():
        image.convert("RGB").save(frames / f"{digest}.png")
    by_id = {c.concept_id: c for c in lib.concepts}
    order = [c.concept_id for c in lib.concepts]
    for obj in objects:
        cid = str(obj["category"])
        x0, y0, x1, y1 = (float(v) for v in obj["bbox"])
        box = (max(0.0, x0 / w), max(0.0, y0 / h), min(1.0, x1 / w), min(1.0, y1 / h))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        ex = Exemplar(digest, box, bool(obj.get("positive", True)), str(obj.get("label") or ""), source)
        old = by_id.get(cid)
        if old is None:
            kind = (kinds or {}).get(cid, "entity")
            if kind not in KINDS:
                raise ValueError(f"unknown concept kind: {kind}")
            old = Concept(cid, kind, cid)
            order.append(cid)
        if ex in old.exemplars:
            continue
        # New evidence invalidates the compiled student until it is recompiled.
        by_id[cid] = replace(old, exemplars=old.exemplars + (ex,), student=None)
    return _child(lib, tuple(by_id[c] for c in order))


def with_students(lib: ConceptLibrary, students: dict[str, StudentArtifact]) -> ConceptLibrary:
    concepts = tuple(replace(c, student=students.get(c.concept_id, c.student)) for c in lib.concepts)
    return _child(lib, concepts)


def store_artifact(library_dir: Path, data: bytes, suffix: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    dest = Path(library_dir) / "artifacts" / f"{sha}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return sha


def artifact_path(library_dir: Path, sha: str) -> Path:
    hits = list((Path(library_dir) / "artifacts").glob(f"{sha}.*"))
    if len(hits) != 1:
        raise FileNotFoundError(f"artifact {sha}")
    if hashlib.sha256(hits[0].read_bytes()).hexdigest() != sha:
        raise ValueError(f"artifact {sha} content mismatch")
    return hits[0]


def load_frame(library_dir: Path, digest: str) -> Image.Image:
    with Image.open(Path(library_dir) / "frames" / f"{digest}.png") as im:
        return im.convert("RGB")


def save(lib: ConceptLibrary, library_dir: Path) -> Path:
    d = Path(library_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{lib.revision.revision_id}.json"
    if dest.exists():
        raise FileExistsError(dest)
    _atomic_replace(dest, json.dumps(_to_plain(lib), sort_keys=True, indent=1))
    write_head(d, lib.revision.revision_id)
    return dest


def load(library_dir: Path, revision_id: str | None = None) -> ConceptLibrary:
    d = Path(library_dir)
    if revision_id is None:
        revision_id = json.loads((d / "HEAD.json").read_text(encoding="utf-8"))["revision_id"]
    raw = json.loads((d / f"{revision_id}.json").read_text(encoding="utf-8"))
    concepts = []
    for c in raw["concepts"]:
        st = c.get("student")
        student = None
        if st:
            protos = tuple(
                Prototype(p["prototype_id"], tuple(p["exemplar_indices"]), float(p["threshold"]))
                for p in st["prototypes"]
            )
            student = StudentArtifact(st["backend"], st["artifact_sha256"], protos, dict(st.get("config") or {}))
        exemplars = tuple(
            Exemplar(e["frame_digest"], tuple(e["bbox"]), e["positive"], e["label"], e["source"]) for e in c["exemplars"]
        )
        concepts.append(
            Concept(c["concept_id"], c["kind"], c["name"], exemplars, student, dict(c["validation"]), c["status"])
        )
    lib = ConceptLibrary(SchemaRevision(**raw["revision"]), raw["game_id"], tuple(concepts))
    blank = replace(lib, revision=replace(lib.revision, payload_hash=""))
    if hash_payload(_to_plain(blank)) != lib.revision.payload_hash:
        raise ValueError("concept library payload_hash mismatch")
    return lib

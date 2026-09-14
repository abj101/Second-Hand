"""Small, game-scoped semantic memory backed by flat LanceDB hybrid search."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Literal, Protocol, Sequence


Relation = Literal["support", "contradiction"]
KnowledgeStatus = Literal["hypothesis", "confirmed", "disputed", "rejected"]
MemoryType = Literal["mechanic", "strategy", "procedural_skill", "fact", "failure_lesson"]
FailureSource = Literal["policy", "perception", "grounding", "execution", "environment", "unknown"]
_WORDS = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class EvidenceRef:
    run_ref: str
    session_id: str
    episode_id: str
    step_id: int
    evidence_kind: str
    relation: Relation = "support"


@dataclass
class MemoryCandidate:
    type: MemoryType
    canonical_text: str
    retrieval_text: str
    entities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float = 0.5
    evidence_refs: tuple[EvidenceRef, ...] = ()
    failure_source: FailureSource | None = None
    related_memory_id: str | None = None
    relation: Relation = "support"


@dataclass(frozen=True)
class KnowledgeHit:
    memory_id: str
    text: str
    type: MemoryType
    status: KnowledgeStatus
    confidence: float
    entities: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    score: float


@dataclass(frozen=True)
class RememberReport:
    inserts: int = 0
    merges: int = 0
    contradictions: int = 0
    embeddings_generated: int = 0
    embeddings_avoided: int = 0
    memory_ids: tuple[str, ...] = ()


class Embedder(Protocol):
    def __call__(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class QwenEmbedder:
    """Lazy CPU Qwen embedder; imports/downloads happen only on first call."""

    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-0.6B") -> None:
        self.model_name = model_name
        self._tokenizer = None
        self._model = None

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._embed(texts)

    def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._embed([f"Instruct: Retrieve relevant game memory\nQuery: {text}" for text in texts])

    def __call__(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self.embed_documents(texts)

    def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if self._model is None:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModel.from_pretrained(self.model_name).to("cpu").eval()
        assert self._tokenizer is not None and self._model is not None
        import torch

        with torch.inference_mode():
            encoded = self._tokenizer(list(texts), padding=True, truncation=True, return_tensors="pt")
            hidden = self._model(**encoded).last_hidden_state
            if int(encoded["attention_mask"][:, -1].sum()) == hidden.shape[0]:
                pooled = hidden[:, -1]
            else:
                last = encoded["attention_mask"].sum(1).sub(1)
                pooled = hidden[torch.arange(hidden.shape[0]), last]
            if pooled.shape[1] != 1024:
                raise ValueError(f"Qwen3-Embedding-0.6B returned {pooled.shape[1]} dimensions")
            return torch.nn.functional.normalize(pooled, p=2, dim=1).tolist()


class KnowledgeStore:
    """Game-isolated fact memory with exact-canonical duplicate merging."""

    def __init__(
        self,
        path: Path,
        game_id: str,
        *,
        embedder: Embedder | None = None,
        evidence_resolver: Callable[[EvidenceRef], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        self.game_id = str(game_id).strip()
        if not self.game_id:
            raise ValueError("game_id is required")
        self._embedder = embedder or QwenEmbedder()
        self._evidence_resolver = evidence_resolver or _resolve_evidence_ref
        self._db = None
        self._table = None
        self.last_recall_metrics: dict[str, float | str] = {}

    def close(self) -> None:
        self._table = None
        self._db = None

    def clear(self) -> None:
        if self._table is not None or self._exists():
            self._database().drop_table("memories")
        self._table = None

    def size(self) -> int:
        return sum(1 for row in self._rows() if row["game_id"] == self.game_id)

    def set_status(self, memory_id: str, status: KnowledgeStatus) -> bool:
        if status not in {"hypothesis", "confirmed", "disputed", "rejected"}:
            raise ValueError(f"invalid knowledge status: {status}")
        row = next((row for row in self._rows() if row["memory_id"] == memory_id and row["game_id"] == self.game_id), None)
        if row is None:
            return False
        row["status"] = status
        row["updated_at"] = datetime.now(UTC).isoformat()
        self._replace(row)
        self._refresh_fts()
        return True

    def remember(self, candidate: MemoryCandidate) -> RememberReport:
        return self.remember_candidates((candidate,))

    def remember_candidates(self, candidates: Sequence[MemoryCandidate]) -> RememberReport:
        """Validate evidence then batch-embed only new canonical rows."""
        valid = [self._candidate(candidate) for candidate in candidates]
        collapsed: dict[str, MemoryCandidate] = {}
        batch_merges = 0
        for candidate in valid:
            prior = collapsed.get(candidate.canonical_text)
            if prior is None:
                collapsed[candidate.canonical_text] = candidate
                continue
            collapsed[candidate.canonical_text] = MemoryCandidate(
                prior.type, prior.canonical_text, prior.retrieval_text,
                tuple(dict.fromkeys((*prior.entities, *candidate.entities))),
                tuple(dict.fromkeys((*prior.tags, *candidate.tags))),
                max(prior.confidence, candidate.confidence),
                (*prior.evidence_refs, *candidate.evidence_refs), prior.failure_source,
                prior.related_memory_id, prior.relation,
            )
            batch_merges += 1
        valid = list(collapsed.values())
        rows = {row["canonical_key"]: row for row in self._rows() if row["game_id"] == self.game_id}
        related = {id(candidate): self._related(candidate) for candidate in valid if candidate.related_memory_id}
        new = [candidate for candidate in valid if _canonical_key(candidate.canonical_text) not in rows and id(candidate) not in related]
        vectors = list(self._documents([candidate.retrieval_text for candidate in new])) if new else []
        if len(vectors) != len(new):
            raise ValueError("embedder returned a vector count different from its input")
        inserts = merges = contradictions = avoided = 0
        ids: list[str] = []
        for candidate, vector in zip(new, vectors):
            row = self._row(candidate, vector)
            self._add(row)
            rows[row["canonical_key"]] = row
            inserts += 1
            ids.append(row["memory_id"])
        for candidate in valid:
            related_row = related.get(id(candidate))
            if related_row is None:
                continue
            self._merge(related_row, candidate)
            merges += 1
            avoided += 1
            contradictions += sum(ref.relation == "contradiction" for ref in candidate.evidence_refs)
            ids.append(related_row["memory_id"])
        for candidate in valid:
            if id(candidate) in related:
                continue
            old = rows.get(_canonical_key(candidate.canonical_text))
            if old is None or old["memory_id"] in ids:
                continue
            incoming = list(candidate.evidence_refs)
            contradictions += sum(ref.relation == "contradiction" for ref in incoming)
            self._merge(old, candidate)
            merges += 1
            avoided += 1
            ids.append(old["memory_id"])
        contradictions += sum(ref.relation == "contradiction" for candidate in new for ref in candidate.evidence_refs)
        if inserts or merges:
            self._refresh_fts()
        return RememberReport(inserts, merges + batch_merges, contradictions, len(new), avoided + batch_merges, tuple(ids))

    def recall(
        self,
        query: str,
        *,
        entities: Sequence[str] = (),
        types: Sequence[MemoryType] | None = None,
        limit: int = 3,
    ) -> list[KnowledgeHit]:
        """Flat hybrid recall, bounded to the prompt's three-item budget."""
        limit = min(3, max(0, limit))
        if not limit or not self._rows():
            self.last_recall_metrics = {"embedding_ms": 0.0, "hybrid_search_ms": 0.0}
            return []
        started = time.perf_counter()
        vector = list(self._queries((query,))[0])
        embedded = time.perf_counter()
        native = self._native_recall(query, vector, types, max(limit * 8, 24))
        query_words = set(_WORDS.findall(query.lower()))
        wanted_entities = {_norm(value) for value in entities}
        hits: list[KnowledgeHit] = []
        for row in native if native is not None else self._rows():
            if row["game_id"] != self.game_id:
                continue
            if row["status"] in {"disputed", "rejected"}:
                continue
            if types is not None and row["type"] not in types:
                continue
            row_entities = {_norm(value) for value in row["entities"]}
            entity_overlap = (
                len(wanted_entities & row_entities) / len(wanted_entities | row_entities)
                if wanted_entities and row_entities else 0.0
            )
            if native is None:
                semantic = _cosine(vector, row["vector"])
                words = set(_WORDS.findall(row["retrieval_text"].lower()))
                lexical = len(query_words & words) / len(query_words | words) if query_words or words else 0.0
                score = 0.75 * semantic + 0.25 * lexical + 0.1 * entity_overlap
            else:
                score = float(row.get("_relevance_score", row.get("_score", 0.0))) + 0.1 * entity_overlap
            hits.append(KnowledgeHit(row["memory_id"], row["memory_text"], row["type"], row["status"], float(row["confidence"]), tuple(row["entities"]), self._refs(row["evidence_refs"]), score))
        self.last_recall_metrics = {
            "embedding_ms": (embedded - started) * 1000,
            "hybrid_search_ms": (time.perf_counter() - embedded) * 1000,
            "retrieval_backend": "lancedb_rrf" if native is not None else "python_flat",
        }
        return sorted(hits, key=lambda hit: (-hit.score, hit.memory_id))[:limit]

    def _candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        if candidate.type not in {"mechanic", "strategy", "procedural_skill", "fact", "failure_lesson"}:
            raise ValueError(f"invalid memory type: {candidate.type}")
        if candidate.failure_source not in {None, "policy", "perception", "grounding", "execution", "environment", "unknown"}:
            raise ValueError(f"invalid failure source: {candidate.failure_source}")
        if candidate.relation not in {"support", "contradiction"}:
            raise ValueError(f"invalid relation: {candidate.relation}")
        if candidate.type == "failure_lesson" and candidate.failure_source is None:
            raise ValueError("failure lessons require a failure source")
        canonical = " ".join(candidate.canonical_text.split())
        retrieval = " ".join(candidate.retrieval_text.split())
        if not canonical or not retrieval:
            raise ValueError("canonical_text and retrieval_text are required")
        if any(not isinstance(entity, str) or not entity.strip() for entity in candidate.entities):
            raise ValueError("entities must be non-empty strings")
        if not candidate.evidence_refs:
            raise ValueError("candidate requires evidence")
        invalid = [ref for ref in candidate.evidence_refs if not self._valid(ref)]
        if invalid:
            raise ValueError("candidate contains invalid evidence reference")
        refs = candidate.evidence_refs
        return MemoryCandidate(candidate.type, canonical, retrieval, tuple(candidate.entities), tuple(candidate.tags), _confidence(candidate.confidence), refs, candidate.failure_source, candidate.related_memory_id, candidate.relation)

    def _valid(self, ref: EvidenceRef) -> bool:
        structurally_valid = (
            bool(ref.run_ref.strip() and ref.session_id.strip() and ref.episode_id.strip())
            and isinstance(ref.step_id, int) and not isinstance(ref.step_id, bool) and ref.step_id >= 0
            and bool(ref.evidence_kind.strip()) and ref.relation in {"support", "contradiction"}
        )
        return structurally_valid and self._evidence_resolver(ref)

    def _row(self, candidate: MemoryCandidate, vector: Sequence[float]) -> dict:
        refs = candidate.evidence_refs
        now = datetime.now(UTC).isoformat()
        key = _canonical_key(candidate.canonical_text)
        return {"memory_id": hashlib.sha256(f"{self.game_id}\0{key}".encode()).hexdigest()[:24], "canonical_key": key, "game_id": self.game_id, "type": candidate.type, "memory_text": candidate.canonical_text, "retrieval_text": candidate.retrieval_text, "entities": list(candidate.entities), "tags": list(candidate.tags), "confidence": candidate.confidence, "evidence_refs": self._refs_json(refs), "contradiction_texts": [candidate.canonical_text] if candidate.relation == "contradiction" else [], "failure_source": candidate.failure_source or "", "related_memory_id": candidate.related_memory_id or "", "relation": candidate.relation, "status": self._status(refs), "support_count": sum(ref.relation == "support" for ref in refs), "contradiction_count": sum(ref.relation == "contradiction" for ref in refs), "episode_ids": sorted({ref.episode_id for ref in refs}), "embedding_model": getattr(self._embedder, "model_name", type(self._embedder).__name__), "embedding_version": "qwen-last-token-v1", "embedding_dimension": len(vector), "created_at": now, "updated_at": now, "vector": [float(value) for value in vector]}

    @staticmethod
    def _refs_json(refs: Iterable[EvidenceRef]) -> str:
        unique = {(ref.run_ref, ref.session_id, ref.episode_id, ref.step_id, ref.evidence_kind, ref.relation): ref for ref in refs}
        return json.dumps([asdict(ref) for _, ref in sorted(unique.items())], sort_keys=True)

    @staticmethod
    def _refs(raw: str) -> tuple[EvidenceRef, ...]:
        return tuple(EvidenceRef(**item) for item in json.loads(raw))

    @staticmethod
    def _status(refs: Iterable[EvidenceRef]) -> KnowledgeStatus:
        refs = tuple(refs)
        support_episodes = {ref.episode_id for ref in refs if ref.relation == "support"}
        contradictions = any(ref.relation == "contradiction" for ref in refs)
        if contradictions and support_episodes:
            return "disputed"
        if contradictions:
            return "rejected"
        if any(ref.relation == "support" and ref.evidence_kind == "sensor_verified_tutorial" for ref in refs):
            return "confirmed"
        return "confirmed" if len(support_episodes) >= 2 else "hypothesis"

    def _database(self):
        if self._db is None:
            import lancedb
            self.path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.path))
        return self._db

    def _exists(self) -> bool:
        database = self._database()
        if hasattr(database, "list_tables"):
            return "memories" in database.list_tables().tables
        return "memories" in database.table_names()

    def _get_table(self):
        if self._table is None and self._exists():
            self._table = self._database().open_table("memories")
        return self._table

    def _rows(self) -> list[dict]:
        table = self._get_table()
        if table is None:
            return []
        if hasattr(table, "to_list"):  # Tiny fake used by focused tests.
            return table.to_list()
        return table.to_arrow().to_pylist()

    def _add(self, row: dict) -> None:
        table = self._get_table()
        if table is None:
            database = self._database()
            if hasattr(database, "list_tables"):
                self._table = database.create_table(
                    "memories", schema=_arrow_schema(len(row["vector"])),
                )
                self._table.add([row])
            else:
                self._table = database.create_table("memories", data=[row])
        else:
            table.add([row])

    def _replace(self, row: dict) -> None:
        table = self._get_table()
        assert table is not None
        table.delete(f"memory_id = '{row['memory_id']}'")
        table.add([row])

    def _refresh_fts(self) -> None:
        table = self._get_table()
        assert table is not None
        if hasattr(table, "create_index"):
            from lancedb.index import FTS

            table.create_index("retrieval_text", config=FTS(), replace=True)
        else:
            table.create_fts_index("retrieval_text", replace=True)

    def _documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        method = getattr(self._embedder, "embed_documents", self._embedder)
        return method(texts)

    def _queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        method = getattr(self._embedder, "embed_queries", self._embedder)
        return method(texts)

    def _related(self, candidate: MemoryCandidate) -> dict | None:
        if not candidate.related_memory_id:
            return None
        row = next((row for row in self._rows() if row["memory_id"] == candidate.related_memory_id and row["game_id"] == self.game_id), None)
        if row is None:
            raise ValueError("related_memory_id is not in this game's memory")
        if row["type"] != candidate.type:
            raise ValueError("related memory must have the same memory type")
        incoming = {_norm(entity) for entity in candidate.entities}
        incumbent = {_norm(entity) for entity in row["entities"]}
        if incoming and incumbent and not incoming & incumbent:
            raise ValueError("related memory must share an entity")
        return row

    def _merge(self, row: dict, candidate: MemoryCandidate) -> None:
        refs = self._refs(row["evidence_refs"])
        incoming = tuple(
            ref if candidate.relation == "support" else EvidenceRef(ref.run_ref, ref.session_id, ref.episode_id, ref.step_id, ref.evidence_kind, "contradiction")
            for ref in candidate.evidence_refs
        )
        refs = self._refs_json((*refs, *incoming))
        all_refs = self._refs(refs)
        row["evidence_refs"] = refs
        if candidate.relation == "contradiction":
            row["contradiction_texts"] = sorted(set(row.get("contradiction_texts") or ()) | {candidate.canonical_text})
        row["entities"] = sorted(set(row["entities"]) | set(candidate.entities))
        row["tags"] = sorted(set(row["tags"]) | set(candidate.tags))
        row["confidence"] = max(float(row["confidence"]), candidate.confidence)
        row["support_count"] = sum(ref.relation == "support" for ref in all_refs)
        row["contradiction_count"] = sum(ref.relation == "contradiction" for ref in all_refs)
        row["episode_ids"] = sorted({ref.episode_id for ref in all_refs})
        row["status"] = self._status(all_refs)
        row["updated_at"] = datetime.now(UTC).isoformat()
        self._replace(row)

    def _native_recall(
        self, query: str, vector: Sequence[float], types: Sequence[MemoryType] | None,
        limit: int,
    ) -> list[dict] | None:
        """Use Lance's flat vector + FTS search and default RRF when available."""
        table = self._get_table()
        if table is None or not hasattr(table, "search"):
            return None
        quoted_game = self.game_id.replace("'", "''")
        where = f"game_id = '{quoted_game}' AND status NOT IN ('disputed', 'rejected')"
        if types:
            quoted = ", ".join(f"'{str(value).replace(chr(39), chr(39) * 2)}'" for value in types)
            where += f" AND type IN ({quoted})"
        builder = (
            table.search(
                query_type="hybrid", vector_column_name="vector", fts_columns="retrieval_text",
            )
            .vector(list(vector))
            .text(query)
            .distance_type("cosine")
            .bypass_vector_index()
            .where(where, prefilter=True)
            .rerank()
            .limit(limit)
        )
        return builder.to_list() if hasattr(builder, "to_list") else builder.to_arrow().to_pylist()


def _confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _canonical_key(text: str) -> str:
    return hashlib.sha256(_norm(text).encode()).hexdigest()


def _arrow_schema(vector_size: int):
    import pyarrow as pa

    return pa.schema([
        ("memory_id", pa.string()), ("canonical_key", pa.string()), ("game_id", pa.string()),
        ("type", pa.string()), ("memory_text", pa.string()), ("retrieval_text", pa.string()),
        ("entities", pa.list_(pa.string())), ("tags", pa.list_(pa.string())),
        ("confidence", pa.float64()), ("evidence_refs", pa.string()),
        ("contradiction_texts", pa.list_(pa.string())),
        ("failure_source", pa.string()), ("related_memory_id", pa.string()),
        ("relation", pa.string()), ("status", pa.string()),
        ("support_count", pa.int64()), ("contradiction_count", pa.int64()),
        ("episode_ids", pa.list_(pa.string())), ("embedding_model", pa.string()),
        ("embedding_version", pa.string()), ("embedding_dimension", pa.int64()),
        ("created_at", pa.string()), ("updated_at", pa.string()),
        ("vector", pa.list_(pa.float32(), vector_size)),
    ])


def _resolve_evidence_ref(ref: EvidenceRef) -> bool:
    """Require the referenced run, episode, and step to exist durably on disk."""
    run_dir = Path(ref.run_ref)
    turns = run_dir / "turns.jsonl"
    episodes = run_dir / "episodes.jsonl"
    if not run_dir.is_dir() or not turns.is_file() or not episodes.is_file():
        return False

    def _contains(path: Path, *, step: bool) -> bool:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (
                    str(row.get("session_id") or "") == ref.session_id
                    and str(row.get("episode_id") or "") == ref.episode_id
                    and (not step or row.get("step_id") == ref.step_id)
                ):
                    return True
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return False

    return _contains(episodes, step=False) and _contains(turns, step=True)

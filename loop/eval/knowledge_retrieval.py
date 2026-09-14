"""Deterministic retrieval benchmark for the compact semantic-memory prompt."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from loop.memory.knowledge import EvidenceRef, KnowledgeStore, MemoryCandidate, QwenEmbedder


def run_knowledge_retrieval_benchmark(
    fixture_path: Path,
    *,
    recall: Callable[[str], Sequence[Any]],
    embed_query: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Score an injected recall path; the benchmark never loads a model itself."""
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    embed_ms: list[float] = []
    search_ms: list[float] = []
    reciprocal_ranks: list[float] = []
    recalls = {1: 0, 3: 0, 5: 0}
    false_positives = 0
    positive_queries = 0
    for item in fixture.get("queries", []):
        query = str(item["query"])
        relevant = {str(value) for value in item.get("relevant_memory_ids") or ()}
        if embed_query is not None:
            started = time.perf_counter()
            embed_query(query)
            embed_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        hits = list(recall(query))
        search_ms.append((time.perf_counter() - started) * 1000)
        ids = [_memory_id(hit) for hit in hits]
        rank = next((index + 1 for index, value in enumerate(ids) if value in relevant), None)
        if relevant:
            positive_queries += 1
            reciprocal_ranks.append(1 / rank if rank else 0.0)
            for k in recalls:
                recalls[k] += int(bool(relevant & set(ids[:k])))
        # Empty relevance is intentional: every returned hit is a false positive.
        false_positives += sum(value not in relevant for value in ids[:5])
        rows.append({"query": query, "relevant_memory_ids": sorted(relevant), "returned_memory_ids": ids[:5], "rank": rank})
    count = len(rows)
    return {
        "queries": count,
        "memories": len(fixture.get("memories") or ()),
        "positive_queries": positive_queries,
        "recall_at_1": recalls[1] / positive_queries if positive_queries else 0.0,
        "recall_at_3": recalls[3] / positive_queries if positive_queries else 0.0,
        "recall_at_5": recalls[5] / positive_queries if positive_queries else 0.0,
        "mrr": sum(reciprocal_ranks) / positive_queries if positive_queries else 0.0,
        "false_positives": false_positives,
        "query_embedding_latency_ms": _mean(embed_ms),
        "hybrid_search_latency_ms": _mean(search_ms),
        "process_rss_bytes": _rss_bytes(),
        "rows": rows,
    }


def run_model_benchmark(
    fixture_path: Path,
    *,
    model_name: str = "Qwen/Qwen3-Embedding-0.6B",
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Seed the checked-in corpus and benchmark one configurable embedder."""
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    document_embedder = embedder or QwenEmbedder(model_name)
    embedding_ms: list[float] = []
    search_ms: list[float] = []
    with tempfile.TemporaryDirectory(prefix="avatar-knowledge-bench-") as temp:
        store = KnowledgeStore(
            Path(temp) / "knowledge.lancedb", "benchmark",
            embedder=document_embedder, evidence_resolver=lambda _: True,
        )
        ref = EvidenceRef("benchmark", "benchmark", "fixture", 1, "benchmark")
        memories = list(fixture.get("memories") or ())
        report = store.remember_candidates(tuple(
            MemoryCandidate(
                type=str(row["type"]), canonical_text=str(row["text"]),
                retrieval_text=str(row["text"]), entities=tuple(row.get("entities") or ()),
                evidence_refs=(ref,),
                failure_source="policy" if row["type"] == "failure_lesson" else None,
            )
            for row in memories
        ))
        external_ids = {
            memory_id: str(row["memory_id"])
            for memory_id, row in zip(report.memory_ids, memories)
        }

        def _recall(query: str) -> list[dict[str, str]]:
            hits = store.recall(query, limit=3)
            embedding_ms.append(float(store.last_recall_metrics.get("embedding_ms", 0.0)))
            search_ms.append(float(store.last_recall_metrics.get("hybrid_search_ms", 0.0)))
            return [{"memory_id": external_ids[hit.memory_id]} for hit in hits]

        result = run_knowledge_retrieval_benchmark(fixture_path, recall=_recall)
        result["model"] = getattr(document_embedder, "model_name", model_name)
        result["query_embedding_latency_ms"] = _mean(embedding_ms)
        result["hybrid_search_latency_ms"] = _mean(search_ms)
        store.close()
        return result


def _memory_id(hit: Any) -> str:
    if isinstance(hit, dict):
        return str(hit.get("memory_id") or hit.get("id") or "")
    return str(getattr(hit, "memory_id", getattr(hit, "id", "")))


def _mean(values: Iterable[float]) -> float:
    values = tuple(values)
    return sum(values) / len(values) if values else 0.0


def _rss_bytes() -> int:
    if os.name != "nt":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * 1024)  # Linux reports KiB; macOS already reports bytes.
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD),
            ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
            ("quota_peak_paged", ctypes.c_size_t), ("quota_paged", ctypes.c_size_t),
            ("quota_peak_nonpaged", ctypes.c_size_t), ("quota_nonpaged", ctypes.c_size_t),
            ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
        ]
    counters = Counters(cb=ctypes.sizeof(Counters))
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    process = ctypes.windll.kernel32.GetCurrentProcess()
    if not query(process, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.working_set)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Avatar semantic-memory retrieval")
    parser.add_argument(
        "--fixture", type=Path,
        default=Path(__file__).resolve().parents[1] / "fixtures" / "knowledge_retrieval.json",
    )
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    args = parser.parse_args(argv)
    print(json.dumps(run_model_benchmark(args.fixture, model_name=args.model), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

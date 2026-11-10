"""Receipt retrieval benchmark; never invokes models or changes task authority."""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .agent import memory
from .agent.pilot import usage


def benchmark(workspace, store, receipts_dir, cases, *, account_id=None, rounds=10):
    if not cases or not 1 <= rounds <= 100:
        raise ValueError("benchmark requires cases and 1..100 rounds")
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("goal"), str)
                or not case["goal"].strip()
                or ("relevant_task_ids" in case and (
                    not isinstance(case["relevant_task_ids"], list)
                    or any(not isinstance(i, str) for i in case["relevant_task_ids"])))):
            raise ValueError("invalid labeled retrieval case")
    source_sha = memory.current_base_commit(workspace)
    measurements = {}
    for ranking in ("baseline", "qc"):
        latencies, relevance, results = [], [], []
        for case in cases:
            found = []
            for _ in range(rounds):
                started = time.perf_counter()
                found = memory.retrieve_work(
                    case["goal"], workspace, source_sha, store, receipts_dir,
                    account_id=account_id, ranking=ranking)
                latencies.append((time.perf_counter() - started) * 1000)
            ids = [row["task_id"] for row in found]
            expected = case.get("relevant_task_ids")
            hit = bool(set(ids) & set(expected)) if expected is not None else None
            if hit is not None:
                relevance.append(hit)
            results.append({"returned_task_ids": ids, "hit_at_5": hit,
                            "receipt_refs": [r["provenance"] for r in found]})
        ordered = sorted(latencies)
        measurements[ranking] = {
            "queries": len(latencies), "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)], 3),
            "labeled_cases": len(relevance),
            "hit_at_5": sum(relevance) / len(relevance) if relevance else None,
            "results": results}
    buckets = {"QC_PASSED": [], "NOT_QC_PASSED": []}
    for _, receipt in memory._iter_receipts(receipts_dir):
        if not receipt or memory.canonical_repo(receipt["input"]) != workspace:
            continue
        if account_id is not None and receipt["input"].get("hints", {}).get("account_id") != account_id:
            continue
        observed = usage(receipt)
        if memory.terminal_conflicts(store, receipt):
            observed["outcome_bucket"] = "NOT_QC_PASSED"
        buckets[observed["outcome_bucket"]].append({"task_id": receipt["task_id"], **observed})
    return {
        "schema": "KMCW_RECEIPT_BENCHMARK_V1", "source_sha": source_sha,
        "backend": "ORIGINAL_RECEIPTS", "pgvector": "BLOCKED_NO_AUTHORIZED_MEMORY_ADAPTER",
        "measurements": measurements, "historical_usage_by_outcome": buckets,
        "benchmark_inference_calls": 0, "benchmark_model_tokens": 0,
        "tokens_per_live_qc_pass": None, "copilot_standalone_comparison": "NOT_MEASURED",
        "limitation": "Retrieval timings/hits are not coding quality or a Copilot superiority claim."}


def run(args):
    from .queue_store import QueueStore
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("expected retrieval case array")
    report = benchmark(
        str(Path(args.workspace).resolve()), QueueStore(Path(args.runtime_base) / "queue"),
        Path(args.runtime_base) / "receipts", data,
        account_id=args.account_id or None, rounds=args.rounds)
    report["corpus_kind"] = args.corpus_kind
    return report

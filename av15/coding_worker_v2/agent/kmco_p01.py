"""KMCO P01 evidence circuit: five explicit deterministic steps.

This circuit accepts verbatim file excerpts only. It does not prove semantic
truth, entailment, or canonical Dhamma authority. No arbitrary path or network.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class P01Blocked(ValueError):
    pass


def source_locator(root: Path, relative_path: str) -> dict:
    """P01-M01: pin a real regular file inside the authorized workspace."""
    base = Path(root).resolve(strict=True)
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise P01Blocked("P01_SOURCE_SCOPE_DENIED")
    rel = Path(relative_path)
    if any(p in ("..", ".git") for p in rel.parts):
        raise P01Blocked("P01_SOURCE_SCOPE_DENIED")
    path = base / rel
    if path.is_symlink():
        raise P01Blocked("P01_SOURCE_SYMLINK_DENIED")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise P01Blocked("P01_SOURCE_SCOPE_DENIED")
    raw = resolved.read_bytes()
    if len(raw) > 1024 * 1024:
        raise P01Blocked("P01_SOURCE_TOO_LARGE")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise P01Blocked("P01_SOURCE_NOT_UTF8")
    return {"relative_path": str(resolved.relative_to(base)),
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def evidence_lookup(root: Path, source: dict, quote: str) -> dict:
    """P01-M02: require an exact, single-line source quotation."""
    if not isinstance(quote, str) or not quote.strip() or "\n" in quote or len(quote) > 2048:
        raise P01Blocked("P01_QUOTE_INVALID")
    pinned = source_locator(root, source["relative_path"])
    if pinned["sha256"] != source["sha256"]:
        raise P01Blocked("P01_SOURCE_CHANGED")
    raw = (Path(root).resolve() / pinned["relative_path"]).read_text("utf-8")
    for index, line in enumerate(raw.splitlines(), 1):
        if quote in line:
            return {"source": pinned, "line": index, "quote": quote}
    raise P01Blocked("P01_QUOTE_NOT_FOUND")


def provenance_checker(root: Path, evidence: dict) -> dict:
    """P01-M03: repeat SHA and line check immediately before release."""
    pinned = source_locator(root, evidence["source"]["relative_path"])
    if pinned["sha256"] != evidence["source"]["sha256"]:
        raise P01Blocked("P01_SOURCE_CHANGED")
    lines = (Path(root).resolve() / pinned["relative_path"]).read_text("utf-8").splitlines()
    i = evidence["line"]
    if not isinstance(i, int) or not (1 <= i <= len(lines)) or evidence["quote"] not in lines[i - 1]:
        raise P01Blocked("P01_PROVENANCE_MISMATCH")
    return {"verified": True, "source": pinned, "line": i,
            "quote": evidence["quote"]}


def contradiction_detector(assertions: list[dict]) -> dict:
    """P01-M04: detect conflicting *structured* values; not natural-language inference."""
    seen = {}
    for a in assertions:
        if not isinstance(a, dict) or set(a) != {"subject", "predicate", "value"}:
            raise P01Blocked("P01_ASSERTION_INVALID")
        key = (a["subject"], a["predicate"])
        if any(not isinstance(x, str) or not x.strip() for x in (*key, a["value"])):
            raise P01Blocked("P01_ASSERTION_INVALID")
        if key in seen and seen[key] != a["value"]:
            raise P01Blocked("P01_STRUCTURED_CONTRADICTION")
        seen[key] = a["value"]
    return {"structured_conflicts": 0, "semantic_check": "NOT_PERFORMED"}


def answer_gate(root: Path, evidence: dict, assertions: list[dict] | None = None) -> dict:
    """P01-M05: emit only a reverified verbatim quote; never a model truth claim."""
    verified = provenance_checker(root, evidence)
    contradiction_detector(assertions or [])
    return {
        "state": "EXACT_QUOTE_VERIFIED",
        "text": verified["quote"],
        "citation": {"file": verified["source"]["relative_path"],
                     "line": verified["line"], "sha256": verified["source"]["sha256"]},
        "scope": "VERBATIM_FILE_MATCH_ONLY",
        "semantic_truth_verified": False,
    }


def run_p01(root: Path, relative_path: str, quote: str, assertions=None) -> dict:
    """P01-M01..M05 ordered circuit; no release until all five succeed."""
    source = source_locator(root, relative_path)
    evidence = evidence_lookup(root, source, quote)
    provenance_checker(root, evidence)
    contradiction_detector(assertions or [])
    result = answer_gate(root, evidence, assertions)
    return {"circuit": "P01", "steps_completed": [
        "P01-M01", "P01-M02", "P01-M03", "P01-M04", "P01-M05"
    ], "output": result}

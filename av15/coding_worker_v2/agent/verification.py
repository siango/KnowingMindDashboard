"""Signed external-verifier handoff and readback."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "KM_INDEPENDENT_VERIFICATION_V1"
MAX_BYTES = 1_000_000
MAX_AGE_S = 900


def wait_signed_proof(request_path: Path, proof_path: Path,
                      public_key_path: Path, lease_valid) -> dict:
    """Allow the existing external file handoff time to return a proof.

    Only an absent file is retryable. A published invalid proof fails closed.
    The executor maintains its existing keepalive throughout this bounded wait.
    """
    if not public_key_path.is_file() or not shutil.which("openssl"):
        raise VerificationError("VERIFIER_AUTHORITY_UNAVAILABLE")
    try:
        timeout = float(os.environ.get("KMW2_VERIFIER_WAIT_S", "30"))
    except ValueError:
        raise VerificationError("VERIFIER_WAIT_INVALID") from None
    if not math.isfinite(timeout) or not 0 <= timeout <= 300:
        raise VerificationError("VERIFIER_WAIT_INVALID")
    deadline = time.monotonic() + timeout
    while True:
        if not lease_valid():
            raise VerificationError("VERIFIER_LEASE_LOST")
        if proof_path.exists():
            return read_signed_proof(request_path, proof_path, public_key_path)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("VERIFIER_PROOF_UNAVAILABLE")
        time.sleep(min(0.25, remaining))


class VerificationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _read_json(path: Path) -> dict:
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    except OSError:
        raise VerificationError("VERIFIER_PROOF_UNAVAILABLE") from None
    if len(raw) > MAX_BYTES:
        raise VerificationError("VERIFIER_PAYLOAD_TOO_LARGE")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise VerificationError("VERIFIER_PROOF_UNAVAILABLE") from None


def create_handoff(request_dir: Path, task, receipt_path: Path,
                   plan_scope: dict, executor_id: str,
                   executor_runner_id: str) -> tuple[Path, dict]:
    try:
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
    except (OSError, ValueError):
        raise VerificationError("VERIFIER_RECEIPT_UNAVAILABLE") from None
    hints = task.task_input.hints or {}
    lease_token = task.lease_token
    payload = {
        "schema": SCHEMA,
        "task_id": task.task_id,
        "mission_id": task.mission_id,
        "plan_id": plan_scope["plan_id"],
        "plan_digest": plan_scope["plan_digest"],
        "scope_digest": plan_scope["scope_digest"],
        "allowed_paths": list(
            (hints.get("approved_scope") or {}).get("allowed_paths", [])),
        "source_sha": hints.get("base_commit", ""),
        "patch_digest": (task.output or {}).get("patch_digest", ""),
        "receipt_version": receipt.get("receipt_version"),
        "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "lease_token_sha256": hashlib.sha256(
            lease_token.encode("utf-8")).hexdigest(),
        "executor_id": executor_id,
        "executor_runner_id": executor_runner_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    if (not re.fullmatch(r"[0-9a-fA-F]{40}", str(payload["source_sha"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload["plan_digest"]))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}",
                                str(payload["scope_digest"]))
            or not payload["allowed_paths"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload["patch_digest"]))
            or type(payload["receipt_version"]) is not int
            or payload["receipt_version"] < 1 or not lease_token):
        raise VerificationError("VERIFIER_REQUEST_CONTEXT_INVALID")
    request_digest = hashlib.sha256(_canonical(payload)).hexdigest()
    request = {"request": payload, "request_digest": request_digest}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", task.task_id):
        raise VerificationError("VERIFIER_TASK_ID_INVALID")
    path = request_dir / (
        f"{task.task_id}.v{int(payload['receipt_version']):06d}.request.json")
    request_dir.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(request, ensure_ascii=False, sort_keys=True,
                                    indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise VerificationError("VERIFIER_REQUEST_COLLISION") from None
    return path, request


def read_signed_proof(request_path: Path, proof_path: Path,
                      public_key_path: Path) -> dict:
    request = _read_json(request_path)
    proof = _read_json(proof_path)
    if not isinstance(request, dict) or not isinstance(proof, dict):
        raise VerificationError("VERIFIER_PROOF_INVALID")
    payload = request.get("request")
    attestation = proof.get("payload")
    signature = proof.get("signature")
    if (not isinstance(payload, dict) or not isinstance(attestation, dict)
            or not isinstance(signature, str)
            or request.get("request_digest")
            != hashlib.sha256(_canonical(payload)).hexdigest()):
        raise VerificationError("VERIFIER_PROOF_INVALID")
    for key in ("task_id", "mission_id", "plan_id", "plan_digest", "scope_digest",
                "allowed_paths", "source_sha", "patch_digest", "receipt_version",
                "receipt_sha256",
                "lease_token_sha256", "executor_id", "executor_runner_id"):
        if attestation.get(key) != payload.get(key):
            raise VerificationError("VERIFIER_PROOF_MISMATCH")
    if (attestation.get("schema") != SCHEMA
            or attestation.get("request_digest") != request["request_digest"]
            or attestation.get("status") != "PASS"
            or not isinstance(attestation.get("verifier_id"), str)
            or not attestation["verifier_id"]
            or not isinstance(attestation.get("runner_id"), str)
            or not attestation["runner_id"]
            or attestation["verifier_id"] == payload["executor_id"]
            or attestation["runner_id"] == payload["executor_runner_id"]
            or not isinstance(attestation.get("evidence_refs"), list)
            or not attestation["evidence_refs"]
            or any(not isinstance(ref, str) or not ref.strip()
                   for ref in attestation["evidence_refs"])):
        raise VerificationError("VERIFIER_PROOF_INVALID")
    try:
        requested = datetime.fromisoformat(
            str(payload["requested_at"]).replace("Z", "+00:00"))
        issued = datetime.fromisoformat(
            str(attestation["issued_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(
            str(attestation["expires_at"]).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if (requested.tzinfo is None or issued.tzinfo is None or expires.tzinfo is None
                or (now - requested).total_seconds() > MAX_AGE_S
                or issued < requested
                or (now - issued).total_seconds() > MAX_AGE_S
                or (issued - now).total_seconds() > 60
                or expires <= now or (expires - issued).total_seconds() > MAX_AGE_S):
            raise VerificationError("VERIFIER_PROOF_STALE")
    except (KeyError, TypeError, ValueError):
        raise VerificationError("VERIFIER_PROOF_INVALID") from None
    binary = shutil.which("openssl")
    if not binary or not public_key_path.is_file():
        raise VerificationError("VERIFIER_AUTHORITY_UNAVAILABLE")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, base64.binascii.Error):
        raise VerificationError("VERIFIER_SIGNATURE_INVALID") from None
    with tempfile.TemporaryDirectory(prefix="kmcw-verifier-") as tmp:
        root = Path(tmp)
        payload_path = root / "payload.json"
        signature_path = root / "signature.bin"
        payload_path.write_bytes(_canonical(attestation))
        signature_path.write_bytes(signature_bytes)
        try:
            result = subprocess.run(
                [binary, "dgst", "-sha256", "-verify", str(public_key_path),
                 "-signature", str(signature_path), str(payload_path)],
                capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise VerificationError("VERIFIER_SIGNATURE_CHECK_FAILED") from None
    if result.returncode != 0:
        raise VerificationError("VERIFIER_SIGNATURE_INVALID")
    return {**attestation, "authenticated": True,
            "signature_sha256": hashlib.sha256(signature_bytes).hexdigest()}

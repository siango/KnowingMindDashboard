"""Phase 2: task envelope, admission, and validation.

Every task carries: mission_id, task_id, owner_node, input, output,
status, evidence, next_action.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA = "KM_CODING_TASK_V1"
STATUSES = {"QUEUED", "ADMITTED", "RUNNING", "REPAIRING", "DONE", "FAILED", "BLOCKED"}
FAILURE_CLASSES = {"PROVIDER_FAIL", "SYNTAX_FAIL", "TEST_FAIL", "TIMEOUT",
                   "POLICY_DENY", "BUDGET_EXCEEDED", "REPO_FAIL", "PATCH_FAIL",
                   "PARSE_FAIL", "LEASE_LOST", "UNKNOWN"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class TaskValidationError(ValueError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskInput:
    repo: str  # absolute path of target repo checkout
    goal: str  # natural-language coding goal
    test_command: str = ""  # allowlisted test command, "" = syntax only
    preferred_provider: str = ""  # per-task model selection
    max_repairs: int = 2
    hints: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.repo or len(self.repo) > 500:
            raise TaskValidationError("input.repo missing/too long")
        if not self.goal or len(self.goal) > 12000:
            raise TaskValidationError("input.goal missing/too long")
        if len(self.test_command) > 500:
            raise TaskValidationError("input.test_command too long")
        if not 0 <= self.max_repairs <= 5:
            raise TaskValidationError("input.max_repairs out of range 0..5")


@dataclass
class Task:
    mission_id: str
    task_id: str
    owner_node: str
    task_input: TaskInput
    status: str = "QUEUED"
    output: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    next_action: str = "ADMIT"
    idempotency_key: str = ""
    failure_class: str = ""
    attempts: int = 0
    max_attempts: int = 3
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = f"{self.mission_id}:{self.task_id}"
        if not self.created_at:
            self.created_at = utcnow()
        self.updated_at = utcnow()

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "owner_node": self.owner_node,
            "input": {"repo": self.task_input.repo,
                      "goal": self.task_input.goal,
                      "test_command": self.task_input.test_command,
                      "preferred_provider": self.task_input.preferred_provider,
                      "max_repairs": self.task_input.max_repairs,
                      "hints": self.task_input.hints},
            "output": self.output,
            "status": self.status,
            "evidence": self.evidence,
            "next_action": self.next_action,
            "idempotency_key": self.idempotency_key,
            "failure_class": self.failure_class,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        if not isinstance(data, dict):
            raise TaskValidationError("task not an object")
        for key in ("mission_id", "task_id", "owner_node", "input"):
            if key not in data:
                raise TaskValidationError(f"task.{key} missing")
        raw = data["input"]
        if not isinstance(raw, dict):
            raise TaskValidationError("task.input not an object")
        task = cls(
            mission_id=str(data["mission_id"]),
            task_id=str(data["task_id"]),
            owner_node=str(data["owner_node"]),
            task_input=TaskInput(
                repo=str(raw.get("repo", "")),
                goal=str(raw.get("goal", "")),
                test_command=str(raw.get("test_command", "")),
                preferred_provider=str(raw.get("preferred_provider", "")),
                max_repairs=int(raw.get("max_repairs", 2)),
                hints=raw.get("hints", {}) if isinstance(raw.get("hints"), dict) else {},
            ),
            status=str(data.get("status", "QUEUED")),
            output=data.get("output", {}) if isinstance(data.get("output"), dict) else {},
            evidence=data.get("evidence", []) if isinstance(data.get("evidence"), list) else [],
            next_action=str(data.get("next_action", "ADMIT")),
            idempotency_key=str(data.get("idempotency_key", "")),
            failure_class=str(data.get("failure_class", "")),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 3)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
        admit(task)
        return task


def admit(task: Task) -> Task:
    """Admission + validation. Raises TaskValidationError on rejection."""
    for attr in ("mission_id", "task_id", "owner_node"):
        value = getattr(task, attr)
        if not isinstance(value, str) or not ID_RE.match(value):
            raise TaskValidationError(f"task.{attr} invalid")
    task.task_input.validate()
    if task.status not in STATUSES:
        raise TaskValidationError("task.status invalid")
    if task.failure_class and task.failure_class not in FAILURE_CLASSES:
        raise TaskValidationError("task.failure_class invalid")
    if not 1 <= task.max_attempts <= 10:
        raise TaskValidationError("task.max_attempts out of range")
    if task.status == "QUEUED":
        task.status = "ADMITTED"
        task.next_action = "RUN"
    task.updated_at = utcnow()
    return task


def new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:6]}"

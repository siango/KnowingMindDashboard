"""V3.1 Phase 2: one isolated git worktree per active coding task.

Registry lives in agent state (runtime, never committed). Cleanup never
deletes uncommitted work without verified ownership plus a retained patch.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class WorktreeError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail[:300]}" if detail else reason)
        self.reason = reason
        self.detail = detail[:2000]


def _run(argv: list[str], cwd: str, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise WorktreeError("TIMEOUT", " ".join(argv[:3])) from None
    except OSError as exc:
        raise WorktreeError("GIT_MISSING", type(exc).__name__) from None
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-4000:]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorktreeRegistry:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.roots = state_dir / "worktrees"
        self.trash = state_dir / "worktrees_trash"
        self.file = state_dir / "worktrees.json"
        self.roots.mkdir(parents=True, exist_ok=True)
        self.trash.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def list(self) -> list[dict]:
        return sorted(self._read().values(), key=lambda e: e.get("task_id", ""))

    def get(self, task_id: str) -> dict | None:
        return self._read().get(task_id)

    def create(self, repo: str, task_id: str, base: str = "HEAD") -> dict:
        """Create worktree on branch km/<task_id> at base. Idempotent."""
        if not Path(repo).is_dir():
            raise WorktreeError("REPO_MISSING", repo[:120])
        existing = self.get(task_id)
        if existing and existing.get("status") == "active" \
                and Path(existing["path"]).is_dir():
            return existing
        rc, base_commit = _run(["git", "-C", repo, "rev-parse", base], repo)
        if rc != 0:
            raise WorktreeError("BASE_UNKNOWN", base[:60])
        base_commit = base_commit.strip().splitlines()[0][:64]
        branch = f"km/{task_id}"
        path = self.roots / task_id
        if path.exists():
            raise WorktreeError("PATH_EXISTS", str(path)[:160])
        rc, out = _run(["git", "-C", repo, "worktree", "add", "-b", branch,
                        str(path), base_commit], repo, timeout=120)
        if rc != 0:
            raise WorktreeError("WORKTREE_ADD_FAIL", out)
        entry = {"task_id": task_id, "repo": repo, "path": str(path),
                 "branch": branch, "base_commit": base_commit,
                 "status": "active", "created_at": _utcnow()}
        data = self._read()
        data[task_id] = entry
        self._write(data)
        return entry

    def status(self, task_id: str) -> dict:
        entry = self.get(task_id)
        if entry is None:
            raise WorktreeError("NOT_REGISTERED", task_id[:80])
        path = entry["path"]
        if not Path(path).is_dir():
            return {**entry, "on_disk": False, "dirty": None, "files": []}
        rc, out = _run(["git", "-C", path, "status", "--short"], path)
        files = out.strip().splitlines() if out.strip() else []
        return {**entry, "on_disk": True, "dirty": bool(files),
                "files": files[:50]}

    def cleanup(self, task_id: str, allow_dirty: bool = False) -> dict:
        """Remove worktree. Dirty trees need allow_dirty + retained patch."""
        entry = self.get(task_id)
        if entry is None:
            raise WorktreeError("NOT_REGISTERED", task_id[:80])
        if entry.get("status") != "active":
            return {**entry, "removed": False}
        path = Path(entry["path"])
        if self.roots.resolve() not in path.resolve().parents \
                and path.resolve() != self.roots.resolve():
            raise WorktreeError("OWNERSHIP_REFUSED", str(path)[:160])
        state = self.status(task_id)
        retained = ""
        if state.get("dirty") and not allow_dirty:
            raise WorktreeError("DIRTY_REFUSED",
                                f"{len(state.get('files', []))} uncommitted files")
        if state.get("dirty"):
            rc, diff = _run(["git", "-C", str(path), "diff", "HEAD"], str(path))
            retained = str(self.trash / f"{task_id}.patch")
            Path(retained).write_text(diff[:500000], encoding="utf-8")
        if path.is_dir():
            rc, out = _run(["git", "-C", entry["repo"], "worktree", "remove",
                            str(path), "--force"], entry["repo"], timeout=120)
            if rc != 0:
                raise WorktreeError("WORKTREE_REMOVE_FAIL", out)
        _run(["git", "-C", entry["repo"], "worktree", "prune"], entry["repo"])
        entry = {**entry, "status": "removed", "removed_at": _utcnow(),
                 "retained_patch": retained}
        data = self._read()
        data[task_id] = entry
        self._write(data)
        return {**entry, "removed": True}

    def recover(self) -> dict:
        """Restart recovery: reconcile registry against git worktree list."""
        data = self._read()
        active, orphaned = [], []
        for task_id, entry in sorted(data.items()):
            if entry.get("status") != "active":
                continue
            if Path(entry.get("path", "")).is_dir():
                active.append(task_id)
            else:
                entry["status"] = "orphaned"
                orphaned.append(task_id)
        self._write(data)
        return {"active": active, "orphaned": orphaned}

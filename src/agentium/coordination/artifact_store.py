"""Content-addressable artifact store with optional on-disk persistence.

Per PRD §3.2 / §3.6 the store is the *single writer* surface for workflow
intermediate outputs.  It must:

- assign deterministic, content-addressable ids (sha256 of canonical JSON or
  raw bytes), so re-runs that produce identical content do not multiply
  artifacts;
- expose immutable lineage: ``parent_ids`` link forward stages to their
  predecessors so :class:`WorkflowOrchestrator` resumes can reconstruct the
  DAG;
- persist optionally to a JSONL ledger (``persist_path``) so recovery tests
  can replay state after a crash.

The store is *not* a long-term object store; it is a coordination surface.
Large blobs should be stored externally and referenced by uri.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _canonical_json(value: Any) -> bytes:
    """Stable JSON serialisation used for content addressing."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def compute_artifact_id(content: Mapping[str, Any]) -> str:
    """Return ``sha256:<hex>`` over the canonical content."""

    return "sha256:" + hashlib.sha256(_canonical_json(content)).hexdigest()


@dataclass(frozen=True)
class Artifact:
    """Immutable artifact record."""

    artifact_id: str
    workflow: str
    node: str
    tenant_id: str
    run_id: str
    content: Mapping[str, Any]
    parent_ids: tuple = ()
    created_at: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "workflow": self.workflow,
            "node": self.node,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "content": dict(self.content),
            "parent_ids": list(self.parent_ids),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Artifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            workflow=str(data["workflow"]),
            node=str(data["node"]),
            tenant_id=str(data["tenant_id"]),
            run_id=str(data["run_id"]),
            content=dict(data.get("content") or {}),
            parent_ids=tuple(data.get("parent_ids") or ()),
            created_at=float(data.get("created_at") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )


class ArtifactStore:
    """Thread-safe in-memory store with optional JSONL persistence.

    Args:
        persist_path: optional file used as an append-only ledger; on
            construction the file is replayed so the store survives restarts.
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._artifacts: Dict[str, Artifact] = {}
        self._by_run: Dict[str, List[str]] = {}
        self._by_workflow_node: Dict[tuple, List[str]] = {}
        self._lock = threading.RLock()
        self._persist_path = persist_path
        if persist_path is not None:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            if persist_path.exists():
                self._load_from_disk(persist_path)

    def put(
        self,
        workflow: str,
        node: str,
        tenant_id: str,
        run_id: str,
        content: Mapping[str, Any],
        parent_ids: Iterable[str] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        clock: Optional[float] = None,
    ) -> Artifact:
        """Insert an artifact and return the immutable record."""

        if not workflow or not node or not tenant_id or not run_id:
            raise ValueError("workflow/node/tenant_id/run_id must all be non-empty")
        artifact = Artifact(
            artifact_id=compute_artifact_id(content),
            workflow=workflow,
            node=node,
            tenant_id=tenant_id,
            run_id=run_id,
            content=dict(content),
            parent_ids=tuple(parent_ids),
            created_at=clock if clock is not None else time.time(),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            existing = self._artifacts.get(artifact.artifact_id)
            if existing is None:
                self._artifacts[artifact.artifact_id] = artifact
                self._by_run.setdefault(run_id, []).append(artifact.artifact_id)
                self._by_workflow_node.setdefault(
                    (workflow, node), []
                ).append(artifact.artifact_id)
                self._persist(artifact)
                return artifact
            return existing

    def get(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def list_for_run(self, run_id: str) -> List[Artifact]:
        with self._lock:
            ids = list(self._by_run.get(run_id, ()))
            return [self._artifacts[i] for i in ids if i in self._artifacts]

    def list_for_node(self, workflow: str, node: str) -> List[Artifact]:
        with self._lock:
            ids = list(self._by_workflow_node.get((workflow, node), ()))
            return [self._artifacts[i] for i in ids if i in self._artifacts]

    def replay(self) -> List[Artifact]:
        """Return all artifacts in insertion order (for crash-recovery tests)."""

        with self._lock:
            return list(self._artifacts.values())

    def _persist(self, artifact: Artifact) -> None:
        if self._persist_path is None:
            return
        try:
            with self._persist_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(artifact.to_dict()) + "\n")
        except OSError:
            pass

    def _load_from_disk(self, path: Path) -> None:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    artifact = Artifact.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                self._artifacts[artifact.artifact_id] = artifact
                self._by_run.setdefault(artifact.run_id, []).append(artifact.artifact_id)
                self._by_workflow_node.setdefault(
                    (artifact.workflow, artifact.node), []
                ).append(artifact.artifact_id)
        except OSError:
            pass


def make_idempotency_key(*parts: Any) -> str:
    """Stable idempotency key for orchestrator retries."""

    if not parts:
        return uuid.uuid4().hex
    return "idem:" + hashlib.sha256(_canonical_json(parts)).hexdigest()

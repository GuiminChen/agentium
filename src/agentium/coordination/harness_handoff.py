"""Harness handoff artifact presence checks (Anthropic long-running harness / #3 / #22)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from agentium.coordination.artifact_store import Artifact, ArtifactStore


@dataclass(frozen=True)
class HandoffVerificationResult:
    """Outcome of verifying ``handoff_artifact_keys`` against stored artifacts."""

    ok: bool
    missing_keys: tuple[str, ...]
    reason: str | None


_HANDOFF_META = "handoff_key"


def _artifact_satisfies_key(artifact: Artifact, key: str) -> bool:
    k = key.strip()
    if not k:
        return False
    if artifact.metadata.get(_HANDOFF_META) == k:
        return True
    content = artifact.content
    if content.get(_HANDOFF_META) == k:
        return True
    if k in content and content[k] not in (None, "", {}):
        return True
    return False


def verify_handoff_artifact_keys(
    store: ArtifactStore,
    *,
    tenant_id: str,
    run_id: str,
    keys: Sequence[str],
) -> HandoffVerificationResult:
    """Return ``ok`` if every non-empty key is satisfied by at least one run artifact.

    Convention: a key matches if any artifact for ``run_id`` (and ``tenant_id``) has
    ``metadata["handoff_key"] == key``, or ``content["handoff_key"] == key``, or a
    top-level content field named exactly ``key`` with a non-empty value.
    """

    want = [str(x).strip() for x in keys if str(x).strip()]
    if not want:
        return HandoffVerificationResult(ok=True, missing_keys=(), reason=None)
    tid = str(tenant_id).strip()
    rid = str(run_id).strip()
    artifacts: List[Artifact] = [
        a for a in store.list_for_run(rid) if a.tenant_id == tid
    ]
    missing: list[str] = []
    for k in want:
        if not any(_artifact_satisfies_key(a, k) for a in artifacts):
            missing.append(k)
    if missing:
        return HandoffVerificationResult(
            ok=False,
            missing_keys=tuple(missing),
            reason="missing_handoff_keys",
        )
    return HandoffVerificationResult(ok=True, missing_keys=(), reason=None)

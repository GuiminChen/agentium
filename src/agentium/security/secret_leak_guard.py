"""Secret leak guard with high-entropy detection in addition to DLP regexes.

The :class:`DLPClassifier` covers known *patterns* (AWS keys, JWT bearers,
PEM blocks).  PRD §3.16 explicitly calls out a complementary detector that
reasons over the *shape* of the value, so freshly generated random secrets do
not slip through just because the pattern wasn't pre-registered.

This module ships a deterministic, dependency-free implementation:

- splits inputs into candidate tokens (long ascii words, hex blobs, base64);
- computes Shannon entropy per token;
- emits a :class:`SecretLeakDecision` describing whether the payload should be
  blocked, what tokens look risky, and a redacted preview suitable for
  audit-only logging.

Use :meth:`SecretLeakGuard.scan_payload` for tool outputs and outbound
channel payloads alike.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Sequence


_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{16,}")


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


@dataclass(frozen=True)
class SecretLeakHit:
    """One suspicious token discovered by :class:`SecretLeakGuard`."""

    token: str
    entropy: float
    location: str


@dataclass
class SecretLeakDecision:
    """Aggregate verdict returned by :meth:`SecretLeakGuard.scan_payload`."""

    blocked: bool
    hits: List[SecretLeakHit] = field(default_factory=list)
    redacted_preview: str = ""


class SecretLeakGuard:
    """Configurable high-entropy token detector.

    Args:
        min_entropy: tokens with Shannon entropy ``>= min_entropy`` are flagged.
            Defaults to ``4.5`` which catches typical 32-byte secrets.
        min_length: minimum candidate token length.
        block_threshold: how many hits are required to escalate to ``blocked``.
        ignore_substrings: substrings to skip when classifying (e.g. fixtures,
            checksum prefixes).  Matched case-insensitively.
    """

    def __init__(
        self,
        *,
        min_entropy: float = 4.5,
        min_length: int = 20,
        block_threshold: int = 1,
        ignore_substrings: Sequence[str] = (
            "sha256:",
            "fixture",
            "lorem ipsum",
        ),
    ) -> None:
        if min_entropy < 0:
            raise ValueError("min_entropy must be non-negative")
        if min_length < 8:
            raise ValueError("min_length must be >= 8")
        if block_threshold < 1:
            raise ValueError("block_threshold must be >= 1")
        self._min_entropy = min_entropy
        self._min_length = min_length
        self._block_threshold = block_threshold
        self._ignore = tuple(s.lower() for s in ignore_substrings)

    def scan_text(self, text: str, *, location: str = "text") -> SecretLeakDecision:
        """Scan ``text`` and return a decision."""

        if not text:
            return SecretLeakDecision(blocked=False, hits=[], redacted_preview="")
        lowered = text.lower()
        hits: List[SecretLeakHit] = []
        redacted = text
        for match in _TOKEN_RE.finditer(text):
            token = match.group(0)
            if len(token) < self._min_length:
                continue
            if any(s in lowered for s in self._ignore):
                continue
            entropy = _shannon_entropy(token)
            if entropy < self._min_entropy:
                continue
            hits.append(
                SecretLeakHit(token=token, entropy=entropy, location=location)
            )
            redacted = redacted.replace(token, "[SECRET_REDACTED]")
        return SecretLeakDecision(
            blocked=len(hits) >= self._block_threshold,
            hits=hits,
            redacted_preview=redacted,
        )

    def scan_payload(self, payload: Mapping[str, Any]) -> SecretLeakDecision:
        """Recursively scan a payload's string leaves."""

        if not payload:
            return SecretLeakDecision(blocked=False, hits=[], redacted_preview="")
        all_hits: List[SecretLeakHit] = []
        previews: List[str] = []
        for path, value in _walk_string_leaves(payload):
            decision = self.scan_text(value, location=path)
            all_hits.extend(decision.hits)
            previews.append(f"{path}={decision.redacted_preview}")
        return SecretLeakDecision(
            blocked=len(all_hits) >= self._block_threshold,
            hits=all_hits,
            redacted_preview="\n".join(previews),
        )


def _walk_string_leaves(node: Any, prefix: str = "") -> Iterable[tuple]:
    if isinstance(node, str):
        yield prefix or "$", node
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_string_leaves(value, child_prefix)
        return
    if isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk_string_leaves(value, f"{prefix}[{index}]")

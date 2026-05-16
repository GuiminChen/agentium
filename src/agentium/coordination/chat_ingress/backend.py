"""Abstract chat ingress storage (lease, followup queue, steer buffer, collect buffer)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    from agentium.coordination.chat_ingress.types import CollectAppendResult


class ChatIngressBackend(Protocol):
    """Pluggable session-scoped lease + queue state (Redis / PostgreSQL / SQLite / memory)."""

    def has_lease(self, session_key: str) -> bool:
        """Return True when this session holds an active run lease."""

    def try_acquire_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        """Acquire exclusive run lease; False if already held by another token."""

    def renew_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        """Extend TTL if token matches current holder."""

    def release_lease(self, session_key: str, token: str) -> None:
        """Release lease if token matches."""

    def followup_depth(self, session_key: str) -> int:
        """Count queued followup payloads."""

    def followup_enqueue(self, session_key: str, payload_json: str) -> int:
        """Append one follow-up payload; return new queue depth."""

    def followup_pop(self, session_key: str) -> Optional[str]:
        """Pop one FIFO follow-up payload JSON or None."""

    def steer_append(self, session_key: str, text: str) -> None:
        """Append steer text for later drain between tool/LLM boundaries."""

    def steer_drain(self, session_key: str) -> str:
        """Pop and merge all pending steer fragments with newlines."""

    # --- collect (M2) ---

    def collect_append(
        self,
        session_key: str,
        fragment: str,
        debounce_ms: int,
        cap: int,
        on_flush: Optional[Callable[[str, str], None]] = None,
    ) -> "CollectAppendResult":
        """Append a collect fragment."""

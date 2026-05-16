"""In-memory ingress backend: dev/tests and single-process fallback."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

from agentium.coordination.chat_ingress.types import CollectAppendResult


@dataclass
class _Lease:
    token: str
    deadline_monotonic: float


@dataclass
class _CollectState:
    fragments: List[str] = field(default_factory=list)
    deadline_monotonic: float = 0.0
    timer: Optional[threading.Timer] = None


class MemoryChatIngressBackend:
    """Thread-safe lease + FIFO followup + steer buffer + collect debounce (single process)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: Dict[str, _Lease] = {}
        self._followup: Dict[str, Deque[str]] = {}
        self._steer: Dict[str, List[str]] = {}
        self._collect: Dict[str, _CollectState] = {}

    def has_lease(self, session_key: str) -> bool:
        with self._lock:
            le = self._leases.get(session_key)
            if le is None:
                return False
            if time.monotonic() > le.deadline_monotonic:
                del self._leases[session_key]
                return False
            return True

    def try_acquire_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.monotonic()
        with self._lock:
            le = self._leases.get(session_key)
            if le is not None and now <= le.deadline_monotonic:
                return False
            self._leases[session_key] = _Lease(token=token, deadline_monotonic=now + ttl_sec)
            return True

    def renew_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.monotonic()
        with self._lock:
            le = self._leases.get(session_key)
            if le is None or le.token != token:
                return False
            if now > le.deadline_monotonic:
                del self._leases[session_key]
                return False
            le.deadline_monotonic = now + ttl_sec
            return True

    def release_lease(self, session_key: str, token: str) -> None:
        with self._lock:
            le = self._leases.get(session_key)
            if le is not None and le.token == token:
                del self._leases[session_key]

    def followup_depth(self, session_key: str) -> int:
        with self._lock:
            return len(self._followup.get(session_key, ()))

    def followup_enqueue(self, session_key: str, payload_json: str) -> int:
        with self._lock:
            q = self._followup.setdefault(session_key, deque())
            q.append(payload_json)
            return len(q)

    def followup_pop(self, session_key: str) -> Optional[str]:
        with self._lock:
            q = self._followup.get(session_key)
            if not q:
                return None
            return q.popleft()

    def steer_append(self, session_key: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        with self._lock:
            self._steer.setdefault(session_key, []).append(t)

    def steer_drain(self, session_key: str) -> str:
        with self._lock:
            parts = self._steer.pop(session_key, [])
        return "\n\n".join(parts) if parts else ""

    def collect_append(
        self,
        session_key: str,
        fragment: str,
        debounce_ms: int,
        cap: int,
        on_flush: Optional[Callable[[str, str], None]] = None,
    ) -> CollectAppendResult:
        frag = (fragment or "").strip()
        if not frag:
            return CollectAppendResult(
                defer_http=False,
                fragment_depth=0,
                flush_after_ms=None,
                merged_immediate_text=None,
            )
        delay = max(0, debounce_ms) / 1000.0
        with self._lock:
            st = self._collect.setdefault(session_key, _CollectState())
            st.fragments.append(frag)
            if len(st.fragments) > cap:
                st.fragments = st.fragments[-cap:]
            depth = len(st.fragments)

            if debounce_ms <= 0:
                merged = "\n\n".join(st.fragments)
                st.fragments.clear()
                if st.timer is not None:
                    st.timer.cancel()
                    st.timer = None
                return CollectAppendResult(
                    defer_http=False,
                    fragment_depth=depth,
                    flush_after_ms=None,
                    merged_immediate_text=merged,
                )

            now = time.monotonic()
            st.deadline_monotonic = now + delay
            if st.timer is not None:
                st.timer.cancel()

            def _fire() -> None:
                merged_slice: Optional[str] = None
                sk = session_key
                with self._lock:
                    st2 = self._collect.get(sk)
                    if st2 is None:
                        return
                    if time.monotonic() + 0.002 < st2.deadline_monotonic:
                        return
                    if not st2.fragments:
                        return
                    merged_slice = "\n\n".join(st2.fragments)
                    st2.fragments.clear()
                    st2.timer = None
                if merged_slice is not None and on_flush is not None:
                    on_flush(sk, merged_slice)

            st.timer = threading.Timer(delay, _fire)
            st.timer.daemon = True
            st.timer.start()

        return CollectAppendResult(
            defer_http=True,
            fragment_depth=depth,
            flush_after_ms=debounce_ms,
            merged_immediate_text=None,
        )

    def collect_peek_merged_if_ready(self, session_key: str, debounce_ms: int) -> Optional[str]:
        _ = debounce_ms
        with self._lock:
            st = self._collect.get(session_key)
            if st is None or not st.fragments:
                return None
            if time.monotonic() < st.deadline_monotonic:
                return None
            merged = "\n\n".join(st.fragments)
            st.fragments.clear()
            if st.timer is not None:
                st.timer.cancel()
                st.timer = None
            return merged

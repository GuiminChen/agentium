"""Orchestrate admission, lease, follow-up drain, and collect flush hooks."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Union

from agentium.app.settings import AppSettings
from agentium.coordination.chat_ingress.backend import ChatIngressBackend
from agentium.coordination.chat_ingress.exceptions import ChatIngressDeferred
from agentium.coordination.chat_ingress.memory_backend import MemoryChatIngressBackend
from agentium.coordination.chat_ingress.postgres_backend import PostgresChatIngressBackend
from agentium.coordination.chat_ingress.redis_backend import RedisChatIngressBackend
from agentium.coordination.chat_ingress.sqlite_backend import SqliteChatIngressBackend

BackendUnion = Union[
    MemoryChatIngressBackend,
    RedisChatIngressBackend,
    SqliteChatIngressBackend,
    PostgresChatIngressBackend,
]


@dataclass(frozen=True)
class IngressAdmission:
    """Result of admitting one user turn through ingress."""

    lease_token: str
    working_content: str
    """User text for this turn (may be collect-merged)."""


class ChatIngressCoordinator:
    """Session-scoped admission for collect/followup/steer (OpenClaw-style)."""

    def __init__(self, *, backend: ChatIngressBackend, settings: AppSettings) -> None:
        self._b = backend
        self._settings = settings

    @property
    def backend(self) -> ChatIngressBackend:
        return self._b

    def admit_user_turn(
        self,
        *,
        session_key: str,
        effective_disposition: str,
        working_content: str,
        regenerate: bool,
        followup_payload: Dict[str, Any],
        collect_flush: Optional[Callable[[str, str], None]] = None,
        from_drain: bool = False,
        bypass_collect_buffer: bool = False,
    ) -> IngressAdmission:
        """Decide whether this HTTP request runs now or defers (raises :exc:`ChatIngressDeferred`)."""

        disp = (effective_disposition or "collect").strip().lower()
        if disp not in {"collect", "followup", "steer"}:
            disp = "collect"
        text = (working_content or "").strip()
        ttl = float(self._settings.chat_ingress_lease_ttl_seconds)

        if regenerate or from_drain:
            token = str(uuid.uuid4())
            ok = self._b.try_acquire_lease(session_key, token, ttl)
            if not ok:
                # Stale lease: clear by treating as steal — release not ours; force queue not applicable
                raise ChatIngressDeferred("followup", queue_depth=self._b.followup_depth(session_key))
            return IngressAdmission(lease_token=token, working_content=text)

        if disp == "followup" and self._b.has_lease(session_key):
            depth = self._b.followup_enqueue(session_key, json.dumps(followup_payload, ensure_ascii=False))
            raise ChatIngressDeferred("followup", queue_depth=depth)

        if disp == "steer" and self._b.has_lease(session_key):
            self._b.steer_append(session_key, text)
            raise ChatIngressDeferred("steer", queue_depth=1)

        if disp == "collect" and not bypass_collect_buffer:
            cap = max(1, self._settings.chat_ingress_queue_cap)
            deb = max(0, self._settings.chat_ingress_debounce_ms)
            result = self._b.collect_append(
                session_key,
                text,
                deb,
                cap,
                on_flush=collect_flush,
            )
            if result.defer_http:
                raise ChatIngressDeferred(
                    "collect",
                    queue_depth=result.fragment_depth,
                    collect_flush_after_ms=result.flush_after_ms,
                )
            merged = (result.merged_immediate_text or text).strip()
            if not merged and not text:
                merged = text
            use_text = merged if merged else text
            token = str(uuid.uuid4())
            if not self._b.try_acquire_lease(session_key, token, ttl):
                depth = self._b.followup_enqueue(session_key, json.dumps(followup_payload, ensure_ascii=False))
                raise ChatIngressDeferred("followup", queue_depth=depth)
            return IngressAdmission(lease_token=token, working_content=use_text)

        # followup or steer while idle → normal message
        token = str(uuid.uuid4())
        if not self._b.try_acquire_lease(session_key, token, ttl):
            depth = self._b.followup_enqueue(session_key, json.dumps(followup_payload, ensure_ascii=False))
            raise ChatIngressDeferred("followup", queue_depth=depth)
        return IngressAdmission(lease_token=token, working_content=text)

    def renew_or_release_after_error(
        self,
        *,
        session_key: str,
        lease_token: str,
    ) -> None:
        """Release lease on failure paths so sessions do not wedge."""

        self._b.release_lease(session_key, lease_token)

    def renew_lease_for_stream(
        self,
        *,
        session_key: str,
        lease_token: str,
    ) -> bool:
        """Renew lease during long SSE streams."""

        return self._b.renew_lease(
            session_key,
            lease_token,
            float(self._settings.chat_ingress_lease_ttl_seconds),
        )

    def finish_turn_release_and_drain(
        self,
        *,
        session_key: str,
        lease_token: str,
        drain_fn: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Release lease and process queued follow-ups FIFO via ``drain_fn``."""

        self._b.release_lease(session_key, lease_token)
        while True:
            raw = self._b.followup_pop(session_key)
            if raw is None:
                break
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                drain_fn(payload)

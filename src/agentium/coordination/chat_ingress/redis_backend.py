"""Redis-backed ingress (multi-instance; use hash tags under Redis Cluster)."""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from agentium.app.settings import AppSettings
from agentium.coordination.chat_ingress.types import CollectAppendResult


class RedisChatIngressBackend:
    """Lease + lists in Redis; collect uses fragment list + deadline key."""

    def __init__(self, *, client: object, key_prefix: str) -> None:
        self._r = client
        self._prefix = (key_prefix or "agentium:ingress:").rstrip(":") + ":"

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "RedisChatIngressBackend":
        try:
            import redis  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Redis chat ingress requires the 'redis' package (pip install redis)."
            ) from exc
        url = (settings.chat_ingress_redis_url or settings.redis_url or "").strip()
        if not url:
            raise RuntimeError("AGENTIUM_REDIS_URL or AGENTIUM_CHAT_INGRESS_REDIS_URL required for redis ingress.")
        client = redis.Redis.from_url(url, decode_responses=True)  # type: ignore[operator]

        return cls(client=client, key_prefix=settings.chat_ingress_redis_key_prefix or "agentium:ingress")

    def _k(self, session_key: str, suffix: str) -> str:
        # Hash tag for cluster: wrap session segment in braces
        safe = session_key.replace(":", "_")
        return f"{{{safe}}}{self._prefix}{suffix}"

    def has_lease(self, session_key: str) -> bool:
        return bool(self._r.exists(self._k(session_key, "lease")))  # type: ignore[union-attr]

    def try_acquire_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        k = self._k(session_key, "lease")
        ex = max(1, int(ttl_sec))
        ok = bool(self._r.set(k, token, nx=True, ex=ex))  # type: ignore[arg-type]
        if ok:
            return True
        cur = self._r.get(k)  # type: ignore[assignment]
        if cur is not None and str(cur) == token:
            self._r.expire(k, ex)  # type: ignore[union-attr]
            return True
        return False

    def renew_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        k = self._k(session_key, "lease")
        cur = self._r.get(k)  # type: ignore[assignment]
        if cur is None or str(cur) != token:
            return False
        self._r.expire(k, max(1, int(ttl_sec)))  # type: ignore[union-attr]
        return True

    def release_lease(self, session_key: str, token: str) -> None:
        k = self._k(session_key, "lease")
        cur = self._r.get(k)  # type: ignore[assignment]
        if cur is not None and str(cur) == token:
            self._r.delete(k)  # type: ignore[union-attr]

    def followup_depth(self, session_key: str) -> int:
        return int(self._r.llen(self._k(session_key, "followup")))  # type: ignore[arg-type]

    def followup_enqueue(self, session_key: str, payload_json: str) -> int:
        key = self._k(session_key, "followup")
        self._r.rpush(key, payload_json)  # type: ignore[union-attr]
        return int(self._r.llen(key))  # type: ignore[arg-type]

    def followup_pop(self, session_key: str) -> Optional[str]:
        out = self._r.lpop(self._k(session_key, "followup"))  # type: ignore[assignment]
        return str(out) if out is not None else None

    def steer_append(self, session_key: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        self._r.rpush(self._k(session_key, "steer"), t)  # type: ignore[union-attr]

    def steer_drain(self, session_key: str) -> str:
        key = self._k(session_key, "steer")
        parts: List[str] = []
        while True:
            p = self._r.lpop(key)  # type: ignore[assignment]
            if p is None:
                break
            parts.append(str(p))
        return "\n\n".join(parts) if parts else ""

    def collect_append(
        self,
        session_key: str,
        fragment: str,
        debounce_ms: int,
        cap: int,
        on_flush: Optional[Callable[[str, str], None]] = None,
    ) -> CollectAppendResult:
        _ = on_flush  # Redis deferred flush: use peek + cron in prod; here lazy-merge on timer N/A without keyspace
        frag = (fragment or "").strip()
        if not frag:
            return CollectAppendResult(False, 0, None, None)
        fk = self._k(session_key, "collect_frags")
        dk = self._k(session_key, "collect_deadline")
        pipe = self._r.pipeline(transaction=True)  # type: ignore[attr-defined]
        pipe.rpush(fk, frag)  # type: ignore[union-attr]
        pipe.ltrim(fk, -cap, -1)  # type: ignore[union-attr]
        pipe.execute()  # type: ignore[union-attr]
        depth = int(self._r.llen(fk))  # type: ignore[arg-type]

        if debounce_ms <= 0:
            frags = self._r.lrange(fk, 0, -1)  # type: ignore[assignment]
            self._r.delete(fk)  # type: ignore[union-attr]
            self._r.delete(dk)  # type: ignore[union-attr]
            merged = "\n\n".join(str(x) for x in (frags or []))
            return CollectAppendResult(False, depth, None, merged or None)

        deadline = time.time() + debounce_ms / 1000.0
        self._r.set(dk, str(deadline))  # type: ignore[union-attr]
        _ = deadline
        # Without server-side timer, HTTP defers; merge on collect_peek or next admit
        return CollectAppendResult(True, depth, debounce_ms, None)

    def collect_peek_merged_if_ready(self, session_key: str, debounce_ms: int) -> Optional[str]:
        _ = debounce_ms
        dk = self._k(session_key, "collect_deadline")
        fk = self._k(session_key, "collect_frags")
        dl = self._r.get(dk)  # type: ignore[assignment]
        if not dl:
            return None
        try:
            dl_f = float(str(dl))
        except ValueError:
            return None
        if time.time() < dl_f:
            return None
        frags = self._r.lrange(fk, 0, -1)  # type: ignore[assignment]
        self._r.delete(fk)  # type: ignore[union-attr]
        self._r.delete(dk)  # type: ignore[union-attr]
        merged = "\n\n".join(str(x) for x in (frags or []))
        return merged or None

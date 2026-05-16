"""Build chat ingress backends from :class:`~agentium.app.settings.AppSettings`."""

from __future__ import annotations

import structlog
from typing import Optional, Union

from agentium.app.settings import AppSettings
from agentium.coordination.chat_ingress.coordinator import ChatIngressCoordinator
from agentium.coordination.chat_ingress.memory_backend import MemoryChatIngressBackend
from agentium.coordination.chat_ingress.postgres_backend import PostgresChatIngressBackend
from agentium.coordination.chat_ingress.redis_backend import RedisChatIngressBackend
from agentium.coordination.chat_ingress.sqlite_backend import SqliteChatIngressBackend

_LOGGER = structlog.get_logger(__name__)

ChatIngressBackendImpl = Union[
    MemoryChatIngressBackend,
    RedisChatIngressBackend,
    SqliteChatIngressBackend,
    PostgresChatIngressBackend,
]


def build_chat_ingress_backend(settings: AppSettings) -> Optional[ChatIngressBackendImpl]:
    """Return configured backend or None when ``chat_ingress_backend`` is ``off``."""

    kind = settings.chat_ingress_backend
    if kind == "off":
        return None
    if kind == "memory":
        return MemoryChatIngressBackend()
    if kind == "redis":
        return RedisChatIngressBackend.from_settings(settings)
    if kind == "sqlite":
        return SqliteChatIngressBackend(path=settings.chat_ingress_sqlite_path)
    if kind == "postgresql":
        return PostgresChatIngressBackend(url=settings.chat_ingress_database_url or "")
    _LOGGER.warning("chat_ingress_unknown_backend", kind=kind)
    return None


def build_chat_ingress_coordinator(settings: AppSettings) -> Optional[ChatIngressCoordinator]:
    """Construct coordinator when a non-off backend is configured."""

    backend = build_chat_ingress_backend(settings)
    if backend is None:
        return None
    return ChatIngressCoordinator(backend=backend, settings=settings)

"""Default ``chat_ingress_*`` kwargs for manual :class:`~agentium.app.settings.AppSettings` in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def chat_ingress_off_fields(data_dir: Path) -> dict[str, Any]:
    """Ingress disabled; same defaults as production ``off`` + a sqlite path under ``data_dir``."""

    return {
        "chat_ingress_backend": "off",
        "chat_ingress_debounce_ms": 500,
        "chat_ingress_queue_cap": 20,
        "chat_ingress_lease_ttl_seconds": 600.0,
        "chat_ingress_redis_key_prefix": "agentium:ingress",
        "chat_ingress_redis_url": None,
        "chat_ingress_database_url": None,
        "chat_ingress_sqlite_path": (data_dir / "chat_ingress.db").resolve(),
    }


def chat_ingress_memory_fields(data_dir: Path) -> dict[str, Any]:
    """In-process ingress backend for HTTP/integration tests."""

    out = chat_ingress_off_fields(data_dir)
    out["chat_ingress_backend"] = "memory"
    return out

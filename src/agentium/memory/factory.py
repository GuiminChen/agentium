"""Build memory backends from :class:`~agentium.app.plugins_config.MemoryPluginConfig`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Sequence

from agentium.app.plugins_config import Mem0PluginConfig, MemoryPluginConfig
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.backends.mem0_adapter import Mem0Backend, Mem0Client
from agentium.memory.backends.sqlite_backend import SqliteMemoryBackend


def resolve_sqlite_path(cfg: MemoryPluginConfig, data_dir: Path) -> Path:
    raw = Path(cfg.sqlite_relative_path)
    return raw if raw.is_absolute() else (data_dir / raw).resolve()


def build_memory_backend(
    cfg: MemoryPluginConfig, data_dir: Path
) -> InMemoryBackend | SqliteMemoryBackend | Mem0Backend:
    """Construct a backend from plugin YAML (``memory`` section)."""

    if cfg.backend == "memory":
        return InMemoryBackend()
    if cfg.backend == "sqlite":
        return SqliteMemoryBackend(resolve_sqlite_path(cfg, data_dir))
    if cfg.backend == "mem0":
        client = build_mem0_client(cfg.mem0)
        return Mem0Backend(client=client)
    raise ValueError(f"unknown memory.backend: {cfg.backend!r}")


class _Mem0PlatformAdapter:
    """Maps Agentium :class:`Mem0Client` to ``mem0ai`` ``MemoryClient`` (managed API).

    Uses ``user_id`` = ``{tenant_id}::{layer}`` and packs key/payload into the message
    body. Search uses ``user_id`` filter. This is a pragmatic bridge; adjust against
    Mem0 docs for production deployments.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def add(
        self,
        *,
        tenant_id: str,
        layer: str,
        key: str,
        payload: dict,
        created_at: str,
    ) -> None:
        user_id = f"{tenant_id}::{layer}"
        body = json.dumps(
            {"key": key, "payload": payload, "created_at": created_at},
            default=str,
        )
        self._raw.add(
            [{"role": "user", "content": body}],
            user_id=user_id,
            metadata={"agentium_key": key},
        )

    def search(
        self,
        *,
        tenant_id: str,
        layer: str,
        limit: int,
    ) -> Sequence[dict]:
        user_id = f"{tenant_id}::{layer}"
        try:
            hits = self._raw.search(query="agentium", user_id=user_id, limit=limit)
        except TypeError:
            hits = self._raw.search("agentium", filters={"user_id": user_id}, limit=limit)
        rows: List[dict] = []
        if not isinstance(hits, list):
            return rows
        for h in hits[:limit]:
            row = self._hit_to_row(h, tenant_id=tenant_id, layer=layer)
            if row is not None:
                rows.append(row)
        return rows

    @staticmethod
    def _hit_to_row(h: Any, *, tenant_id: str, layer: str) -> dict | None:
        if isinstance(h, dict):
            memory = h.get("memory") if "memory" in h else h.get("text") or h
            if isinstance(memory, str):
                try:
                    data = json.loads(memory)
                    if isinstance(data, dict) and "payload" in data:
                        return {
                            "tenant_id": tenant_id,
                            "layer": layer,
                            "key": str(data.get("key", "mem0")),
                            "payload": data.get("payload", {}),
                            "created_at": data.get("created_at", ""),
                        }
                except json.JSONDecodeError:
                    return {
                        "tenant_id": tenant_id,
                        "layer": layer,
                        "key": "mem0",
                        "payload": {"text": memory},
                        "created_at": "",
                    }
        return None

    def delete(self, *, tenant_id: str) -> int:
        try:
            return int(self._raw.delete_all(user_id=tenant_id) or 0)
        except TypeError:
            try:
                self._raw.delete_all(user_id=tenant_id)
                return 1
            except Exception:
                return 0


def build_mem0_client(cfg: Mem0PluginConfig) -> Mem0Client:
    """Instantiate Mem0 (managed) client and adapt to :class:`Mem0Client`."""

    ref = (cfg.api_key_from_env or "").strip()
    if not ref:
        raise ValueError(
            "plugins.memory.mem0.api_key_from_env is required when memory.backend is 'mem0'"
        )
    api_key = os.environ.get(ref, "").strip()
    if not api_key:
        raise ValueError(
            f"Environment variable {ref!r} is empty (required for mem0 backend)"
        )
    try:
        from mem0 import MemoryClient
    except ImportError as exc:
        raise RuntimeError(
            "mem0 backend requires the mem0ai package. Install with: pip install 'agentium[mem0]'"
        ) from exc

    kwargs: dict[str, Any] = {"api_key": api_key}
    if cfg.base_url.strip():
        kwargs["host"] = cfg.base_url.strip()
    raw = MemoryClient(**kwargs)
    return _Mem0PlatformAdapter(raw)


__all__ = ["build_memory_backend", "build_mem0_client", "resolve_sqlite_path"]

"""Memory backend implementations.

The first-party :class:`~agentium.memory.backends.inmemory_backend.InMemoryBackend`
and :class:`~agentium.memory.backends.sqlite_backend.SqliteMemoryBackend` cover
local and persistent storage. The :class:`~agentium.memory.backends.mem0_adapter.Mem0Backend`
is an experimental adapter that plugs any Mem0-style client behind the same
``MemoryBackend`` protocol so :class:`MemoryService` can keep enforcing tenant
isolation regardless of the durable store.
"""

from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.backends.mem0_adapter import Mem0Backend, Mem0Client
from agentium.memory.backends.sqlite_backend import SqliteMemoryBackend

__all__ = [
    "InMemoryBackend",
    "Mem0Backend",
    "Mem0Client",
    "SqliteMemoryBackend",
]

"""Session-scoped wiki raw uploads (blob store + ingest fan-out)."""

from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath
from typing import Final

_ALLOWED_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".txt", ".json", ".csv", ".pdf"})
_MAX_FILENAME_BYTES: Final[int] = 220

_CHAT_SESSION_ID_SAFE: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$",
)


class SessionMaterialUploadError(Exception):
    """Rejected upload parameters or blob constraints."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def assert_safe_chat_session_id(session_id: str) -> None:
    sid = session_id.strip()
    if not _CHAT_SESSION_ID_SAFE.match(sid):
        raise SessionMaterialUploadError(
            code="invalid_session_id",
            message="session_id must match chat session id rules.",
        )


def sanitize_upload_filename(raw_name: str) -> str:
    """Return a single-path-segment basename safe for wiki blob keys."""

    base = PurePosixPath(raw_name.strip()).name.strip()
    if not base or base in (".", ".."):
        raise SessionMaterialUploadError(
            code="invalid_filename",
            message="filename must be a non-empty basename.",
        )
    lowered = base.lower()
    dot = lowered.rfind(".")
    if dot < 0:
        raise SessionMaterialUploadError(
            code="invalid_file_suffix",
            message="filename must end with an allowed suffix.",
        )
    suf = lowered[dot:]
    if suf not in _ALLOWED_SUFFIXES:
        raise SessionMaterialUploadError(
            code="invalid_file_suffix",
            message=f"allowed suffixes: {sorted(_ALLOWED_SUFFIXES)}.",
        )
    stem = base[:dot]
    stem_sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("._-") or "upload"
    out = f"{stem_sanitized}{base[dot:]}"
    if len(out.encode("utf-8")) > _MAX_FILENAME_BYTES:
        raise SessionMaterialUploadError(
            code="filename_too_long",
            message="filename exceeds maximum length after sanitization.",
        )
    return out


def build_workspace_blob_key(safe_filename: str) -> str:
    """Return blob store key under ``workspace/`` (posix, no traversal)."""

    uid = uuid.uuid4().hex
    blob_key = f"workspace/{uid}_{safe_filename}"
    blob_key = blob_key.strip().replace("\\", "/").lstrip("/")
    if ".." in blob_key.split("/"):
        raise SessionMaterialUploadError(
            code="invalid_blob_key",
            message="generated blob_key failed validation.",
        )
    return blob_key


def validate_decoded_size(*, raw_len: int, max_decoded_bytes: int) -> None:
    if raw_len <= 0:
        raise SessionMaterialUploadError(
            code="empty_payload",
            message="decoded upload must be non-empty.",
        )
    if raw_len > max_decoded_bytes:
        raise SessionMaterialUploadError(
            code="payload_too_large",
            message="decoded content exceeds session_upload_max_decoded_bytes.",
        )

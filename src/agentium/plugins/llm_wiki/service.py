"""Agentium-side LLM-Wiki integration using the ``crate`` package."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Tuple

import structlog

from agentium.app.plugins_config import LlmWikiPluginConfig
from agentium.app.settings import AppSettings
from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore

_LOGGER = structlog.get_logger(__name__)


class LlmWikiPluginService:
    """Blob store + wiki DB + ``crate.host_api.LlmWikiHost`` facade."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        cfg: LlmWikiPluginConfig,
        job_store: SqliteWikiIngestJobStore,
        host: Any,
        blobs: Any,
        wiki_db: Any,
    ) -> None:
        self._settings = settings
        self._cfg = cfg
        self._jobs = job_store
        self._host = host
        self._blobs = blobs
        self._wiki_db = wiki_db

    @property
    def job_store(self) -> SqliteWikiIngestJobStore:
        return self._jobs

    @property
    def host(self) -> Any:
        return self._host

    def tenant_vault_root(self, tenant_id: str) -> Path:
        base = self._settings.data_dir / self._cfg.vault_relative_path / tenant_id.strip()
        return base.resolve()

    def enqueue_ingest_job(
        self,
        *,
        tenant_id: str,
        blob_key: str,
        session_id: str,
        deferred_sink: Any,
    ) -> str:
        """Persist a job and submit ``llm_wiki.ingest_compile`` when *deferred_sink* is set."""

        from agentium.plugins.llm_wiki.deferred import KIND_LLM_WIKI_INGEST_COMPILE

        jid = self._jobs.create_job(
            tenant_id=tenant_id,
            blob_key=blob_key,
            session_id=session_id,
            vault_key_hint=str(self.tenant_vault_root(tenant_id)),
        )
        if deferred_sink is None:
            self._jobs.update_status(jid, status="failed", error="deferred_tasks_disabled")
            return jid
        deferred_sink.enqueue(
            KIND_LLM_WIKI_INGEST_COMPILE,
            {"job_id": jid, "tenant_id": tenant_id},
            lane="wiki",
        )
        return jid

    def run_ingest_job(self, job_id: str) -> None:
        """Execute compile/index for one job (deferred handler entry)."""

        rec = self._jobs.get_job(job_id)
        if rec is None:
            _LOGGER.warning("wiki_job_missing", job_id=job_id)
            return
        self._jobs.update_status(job_id, status="running")
        try:
            from crate.compile_run import extract_pdf_text
            from crate.stores.wiki_paths import wiki_logical_path_for_blob

            tenant_id = rec.tenant_id
            blob_key = rec.blob_key
            vault = self.tenant_vault_root(tenant_id)
            vault.mkdir(parents=True, exist_ok=True)
            paths = self._host.materialize_to_vault(
                tenant_id, [blob_key], vault
            )
            if not paths:
                raise RuntimeError(f"blob not found or failed materialize: {blob_key!r}")
            main_path = paths[0]
            suf = main_path.suffix.lower()
            if suf == ".md":
                body = main_path.read_text(encoding="utf-8", errors="replace")
            elif suf == ".pdf":
                body = extract_pdf_text(main_path)
            else:
                body = main_path.read_text(encoding="utf-8", errors="replace")
            logical_path = wiki_logical_path_for_blob(
                blob_key=blob_key, session_id=rec.session_id
            )
            self._host.upsert_markdown_page(
                tenant_id=tenant_id,
                logical_path=logical_path,
                body_md=body,
            )
            self._host.reindex_page_embeddings(
                tenant_id=tenant_id,
                page_path=logical_path,
                markdown=body,
            )
            self._jobs.update_status(job_id, status="succeeded")
            _LOGGER.info("wiki_job_succeeded", job_id=job_id, tenant_id=tenant_id)
        except Exception as exc:
            _LOGGER.exception("wiki_job_failed", job_id=job_id)
            self._jobs.update_status(job_id, status="failed", error=str(exc)[:2048])

    def wiki_search_precheck(
        self,
        *,
        tenant_id: str,
        scope: str,
        session_id: str,
        wait_for_job_ids: Any,
    ) -> Optional[dict[str, Any]]:
        """Return gate violation dict for HTTP/tool callers, or ``None`` if allowed."""

        from agentium.plugins.llm_wiki.search_gate import wiki_search_gate_violation

        normalized = _normalize_wait_for_job_ids(wait_for_job_ids)
        return wiki_search_gate_violation(
            self._jobs,
            self._cfg,
            tenant_id=tenant_id,
            scope=scope,
            session_id=session_id,
            wait_for_job_ids=normalized,
        )

    def enqueue_session_material(
        self,
        *,
        tenant_id: str,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        deferred_sink: Any,
    ) -> Tuple[str, str]:
        """Write bytes to RawBlobStore, validate paths, queue ingest for *session_id*.

        Returns:
            Tuple of ``(blob_key, job_id)``.

        Raises:
            SessionMaterialUploadError: Invalid ids, suffix, size, or blob path rules.
        """

        from agentium.plugins.llm_wiki.session_material import (
            SessionMaterialUploadError,
            assert_safe_chat_session_id,
            build_workspace_blob_key,
            sanitize_upload_filename,
            validate_decoded_size,
        )

        tid = tenant_id.strip()
        if not tid:
            raise SessionMaterialUploadError(
                code="invalid_tenant",
                message="tenant_id is required.",
            )
        sid = session_id.strip()
        assert_safe_chat_session_id(sid)
        validate_decoded_size(
            raw_len=len(raw_bytes),
            max_decoded_bytes=int(self._cfg.session_upload_max_decoded_bytes),
        )
        safe_name = sanitize_upload_filename(filename)
        blob_key = build_workspace_blob_key(safe_name)
        try:
            from crate.stores.wiki_paths import wiki_logical_path_for_blob

            wiki_logical_path_for_blob(blob_key=blob_key, session_id=sid)
        except ValueError as exc:
            raise SessionMaterialUploadError(
                code="invalid_blob_key",
                message=str(exc),
            ) from exc

        self._blobs.put(tid, blob_key, raw_bytes)
        jid = self.enqueue_ingest_job(
            tenant_id=tid,
            blob_key=blob_key,
            session_id=sid,
            deferred_sink=deferred_sink,
        )
        _LOGGER.info(
            "wiki_session_upload",
            tenant_id=tid,
            session_id=sid,
            blob_key=blob_key,
            job_id=jid,
            filename=safe_name,
        )
        return blob_key, jid


def _normalize_wait_for_job_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(s)
            elif isinstance(item, (int, float)):
                out.append(str(item))
        return out
    return []


def build_llm_wiki_plugin_service(
    settings: AppSettings,
    *,
    cfg: LlmWikiPluginConfig,
) -> Optional[LlmWikiPluginService]:
    """Return a configured service, or ``None`` if ``crate`` is unavailable or disabled."""

    if not cfg.enabled:
        _LOGGER.info("llm_wiki_disabled_in_plugins_config")
        return None
    try:
        from crate.host_api import HostWikiConfig, LlmWikiHost
        from crate.stores import (
            RawBlobBackend,
            WikiDbBackend,
            build_raw_blob_store,
            build_wiki_database,
        )
    except ImportError as exc:
        _LOGGER.warning(
            "crate_package_missing_disable_llm_wiki",
            import_error=str(exc),
            hint="Install the in-repo package: pip install -e ./crate (same interpreter as agentium)",
        )
        return None

    data = settings.data_dir
    raw_cfg = cfg.raw_storage
    cos_stage = (data / raw_cfg.cos_staging_relative_path).resolve()
    base_path = (data / raw_cfg.base_path).resolve()

    blobs = build_raw_blob_store(
        RawBlobBackend(raw_cfg.backend),
        base_path=base_path,
        cos_staging_path=cos_stage,
    )

    wcfg = cfg.wiki_db
    if wcfg.backend == "sqlite":
        wiki_path = data / wcfg.sqlite_relative_path
        if not wiki_path.is_absolute():
            wiki_path = wiki_path.resolve()
        wiki_db = build_wiki_database(WikiDbBackend.sqlite, sqlite_path=wiki_path)
    else:
        env_name = (wcfg.postgresql_conninfo_from_env or "").strip()
        conn = os.environ.get(env_name, "").strip() if env_name else ""
        if not conn:
            _LOGGER.error("llm_wiki_postgresql_conninfo_missing", env=env_name or None)
            return None
        wiki_db = build_wiki_database(
            WikiDbBackend.postgresql, postgresql_conninfo=conn
        )

    try:
        from crate.embedding_config import load_embedding_config
    except ImportError:
        load_embedding_config = None  # type: ignore[assignment]

    emb_cfg = load_embedding_config() if load_embedding_config else None
    host = LlmWikiHost(
        wiki_db=wiki_db,
        blobs=blobs,
        host_wiki_config=HostWikiConfig(embedding=emb_cfg),
    )
    job_store = SqliteWikiIngestJobStore(settings.sqlite_db_path)
    return LlmWikiPluginService(
        settings=settings,
        cfg=cfg,
        job_store=job_store,
        host=host,
        blobs=blobs,
        wiki_db=wiki_db,
    )


def _safe_read_yaml_llm_wiki_enabled(path: Path) -> Optional[bool]:
    """Read ``llm_wiki.enabled`` from YAML without merging env (for diagnostics only)."""

    try:
        import yaml

        if not path.is_file():
            return None
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        block = raw.get("llm_wiki")
        if not isinstance(block, dict) or "enabled" not in block:
            return None
        val = block["enabled"]
        if not isinstance(val, bool):
            return bool(val)
        return val
    except Exception:
        return None


def build_llm_wiki_wire_ping_payload(
    settings: AppSettings,
    *,
    service_wired: bool,
) -> dict[str, Any]:
    """Build JSON for ``GET /v1/wiki/ping``: wiring visibility without secrets."""

    cfg = settings.plugins.llm_wiki
    import sys

    cfg_path = settings.plugins_config_path
    wiki_env_override = os.getenv("AGENTIUM_LLM_WIKI_ENABLED", "").strip()
    yaml_disk_enabled = _safe_read_yaml_llm_wiki_enabled(cfg_path)

    out: dict[str, Any] = {
        "service_wired": service_wired,
        "plugins_config_path": str(cfg_path),
        "plugins_llm_wiki_enabled_in_effective_settings": cfg.enabled,
        "yaml_disk_llm_wiki_enabled_if_readable": yaml_disk_enabled,
        "environment_AGENTIUM_LLM_WIKI_ENABLED": wiki_env_override,
        "wiki_db_backend": cfg.wiki_db.backend,
        "python_executable": sys.executable,
        "crate_import_ok": False,
        "crate_file": "",
        "crate_import_error": None,
        "postgresql_conninfo_env_expected": cfg.wiki_db.backend == "postgresql",
        "postgresql_conninfo_env_name": "",
        "postgresql_conninfo_env_non_empty": None,
    }
    env_conn_name = (cfg.wiki_db.postgresql_conninfo_from_env or "").strip()
    out["postgresql_conninfo_env_name"] = env_conn_name
    if cfg.wiki_db.backend == "postgresql":
        conn_raw = (
            os.environ.get(env_conn_name, "").strip() if env_conn_name else ""
        )
        out["postgresql_conninfo_env_non_empty"] = bool(conn_raw)
    else:
        out["postgresql_conninfo_env_non_empty"] = True

    try:
        import crate

        spec = getattr(crate, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec is not None else None
        origin_s = origin if isinstance(origin, str) else ""
        crate_file = str(getattr(crate, "__file__", "") or origin_s).strip()
        out["crate_import_ok"] = True
        out["crate_file"] = crate_file
    except ImportError as exc:
        out["crate_import_error"] = str(exc)[:512]

    hints: list[str] = []

    wiki_env_lc = wiki_env_override.lower()
    forcing_off_via_env = wiki_env_lc in ("0", "false", "no", "off")

    if yaml_disk_enabled is True and not cfg.enabled and forcing_off_via_env:
        hints.append(
            "YAML `llm_wiki.enabled` is true, but AGENTIUM_LLM_WIKI_ENABLED is forcing OFF "
            "(value is false/0/off). Unset AGENTIUM_LLM_WIKI_ENABLED or set it to 1, then restart.",
        )
    elif yaml_disk_enabled is False:
        hints.append(
            f"YAML on disk has llm_wiki.enabled=false — edit {cfg_path.name} to `enabled: true` "
            "or set AGENTIUM_LLM_WIKI_ENABLED=1, then restart.",
        )
    elif yaml_disk_enabled is None and not cfg.enabled:
        hints.append(
            "Could not read llm_wiki.enabled from plugins YAML path — verify file exists and is valid YAML.",
        )

    if not cfg.enabled and not hints:
        hints.append(
            "Effective llm_wiki.enabled is false — set it in plugins YAML or AGENTIUM_LLM_WIKI_ENABLED=1, "
            "then restart.",
        )

    if not bool(out["crate_import_ok"]):
        hints.append(
            "`import crate` failed — run `pip install -e ./crate` with the same Python as "
            "`python_executable` (prefer this repo's `project_agentium/crate` directory).",
        )
    if (
        cfg.wiki_db.backend == "postgresql"
        and not bool(out["postgresql_conninfo_env_non_empty"])
    ):
        hints.append(
            "wiki_db.backend=postgresql but PostgreSQL conninfo env is missing or empty — "
            "set the env var referenced by postgresql_conninfo_from_env.",
        )
    if (
        cfg.enabled
        and bool(out["crate_import_ok"])
        and not service_wired
        and cfg.wiki_db.backend == "sqlite"
    ):
        hints.append(
            "Enabled + crate import succeeded but service is still unwired — restart Agentium "
            "or inspect bootstrap startup logs.",
        )

    out["hints"] = hints
    return out

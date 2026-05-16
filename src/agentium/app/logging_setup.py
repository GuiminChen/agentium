"""Process-wide logging for long-running Agentium services (e.g. ``agentium serve``)."""

from __future__ import annotations

import logging
import logging.handlers
from typing import Final

import structlog

from agentium.app.settings import AppSettings

_AGENTIUM_HANDLER_ATTR: Final[str] = "_agentium_logging_handler"
_STRUCTLOG_CONFIGURED: bool = False


def reset_logging_for_tests() -> None:
    """Remove handlers added by :func:`setup_logging` and allow structlog re-configuration.

    Intended for unit tests only.
    """

    global _STRUCTLOG_CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _AGENTIUM_HANDLER_ATTR, False):
            root.removeHandler(handler)
    _STRUCTLOG_CONFIGURED = False


def setup_logging(settings: AppSettings) -> None:
    """Attach console and optional daily-rotating file handlers to the root logger.

    Structlog loggers emit via stdlib; messages share the same formatter and handlers.
    Safe to call repeatedly: replaces only handlers previously added by this module.

    Args:
        settings: Resolved application settings (paths, backup count, console toggle).
    """

    global _STRUCTLOG_CONFIGURED

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in list(root.handlers):
        if getattr(handler, _AGENTIUM_HANDLER_ATTR, False):
            root.removeHandler(handler)

    foreign_pre_chain = (
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
    )

    if not _STRUCTLOG_CONFIGURED:
        structlog.configure(
            processors=(
                foreign_pre_chain
                + (
                    structlog.processors.format_exc_info,
                    structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
                )
            ),
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        _STRUCTLOG_CONFIGURED = True

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=foreign_pre_chain,
    )

    if settings.log_to_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        setattr(stream_handler, _AGENTIUM_HANDLER_ATTR, True)
        root.addHandler(stream_handler)

    if settings.log_file_path is not None:
        log_path = settings.log_file_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_path),
            when="midnight",
            interval=1,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
            utc=False,
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _AGENTIUM_HANDLER_ATTR, True)
        root.addHandler(file_handler)

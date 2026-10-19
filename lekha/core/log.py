"""Structured logging.

Every log line is a structured event (JSON in staging/production). Values bound with
``structlog.contextvars`` — such as the request ID — are attached to every line logged
while handling that request, which is how one request is traced across the codebase.

Never log document contents, tokens, passwords, or personal data.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog

from lekha.core.config import LogFormat, Settings


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever ``sys.stdout`` is *now*, so replaced streams are honoured."""

    @property
    def stream(self) -> TextIO:
        return sys.stdout

    @stream.setter
    def stream(self, _value: TextIO) -> None:
        pass


def configure_logging(settings: Settings) -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.types.Processor
    if settings.log_format is LogFormat.JSON:
        shared_processors.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route standard-library logs (uvicorn, SQLAlchemy, ...) through the same renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = _StdoutHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Uvicorn's own access log would duplicate ours and logs raw query strings.
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP client libraries log full request URLs (including query strings) at INFO.
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

"""Append-only audit log.

Public interface: other modules import from ``lekha.audit`` only.
"""

from lekha.audit.model import (
    MAX_PATH_LENGTH,
    AuditEvent,
    AuditSink,
    Outcome,
    outcome_for_status,
)

__all__ = ["MAX_PATH_LENGTH", "AuditEvent", "AuditSink", "Outcome", "outcome_for_status"]

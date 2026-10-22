"""Interfaces to outside systems (Phase 5: ticketing). Implementations live elsewhere: the
simulated ticketing system in ``db.actions``; a real HR/IT system once Phase 0 names one.

Public interface: other modules import from ``lekha.connectors`` only.
"""

from lekha.connectors.ticketing import (
    TicketingSystem,
    TicketingUnavailableError,
    TicketRejectedError,
)

__all__ = ["TicketRejectedError", "TicketingSystem", "TicketingUnavailableError"]

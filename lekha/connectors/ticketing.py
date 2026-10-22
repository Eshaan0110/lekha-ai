"""The ticketing interface every HR/IT system adapter implements. Pure: no I/O here.

Contract: ``create_ticket`` with the same idempotency key must never create a second ticket;
it returns the reference of the first. That is what makes a retried execution safe.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from lekha.actions import TicketInputs


class TicketingUnavailableError(RuntimeError):
    """The system could not be reached or timed out; trying again later may work."""


class TicketRejectedError(RuntimeError):
    """The system refused the ticket; trying again will not help."""


class TicketingSystem(Protocol):
    async def create_ticket(
        self, organisation_id: uuid.UUID, idempotency_key: str, ticket: TicketInputs
    ) -> str:
        """Create the ticket (once) and return its reference in that system."""
        ...

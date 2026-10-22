"""The tools Lekha may propose. Exactly one for now: create a ticket (Phase 5, owner D4).

Inputs are validated before a proposal is stored, again when an approver edits them, and
the executor receives only validated inputs. Pure: no I/O.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TicketCategory(StrEnum):
    FACILITIES = "facilities"
    IT = "it"
    HR = "hr"
    SAFETY = "safety"


class TicketPriority(StrEnum):
    P1 = "P1"  # stop-the-line urgent
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class TicketInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=120)
    category: TicketCategory
    priority: TicketPriority = TicketPriority.P3
    description: str = Field(min_length=1, max_length=2000)


TOOLS: dict[str, type[BaseModel]] = {"create_ticket": TicketInputs}


class UnknownToolError(ValueError):
    """The proposal names a tool Lekha doesn't have."""


def validate_inputs(tool: str, inputs: dict[str, Any]) -> dict[str, Any]:  # Any: tool JSON
    """The inputs as the tool will receive them; raises ValidationError or UnknownToolError."""
    model = TOOLS.get(tool)
    if model is None:
        raise UnknownToolError(tool)
    return model.model_validate(inputs).model_dump(mode="json")

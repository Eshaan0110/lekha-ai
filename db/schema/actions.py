"""Actions (plan "Safe Actions"): proposal → approval → execution, plus the SIMULATED
ticketing system that stands in for the real HR/IT system until Phase 0 names one."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum
from lekha.actions import ProposalStatus


class ActionProposal(TenantScoped, Base):
    __tablename__ = "action_proposal"

    business_unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    derived_from: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # e.g. a call
    proposed_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        pg_enum(ProposalStatus, "proposal_status"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_action_proposal_org_id"),
        UniqueConstraint("organisation_id", "idempotency_key", name="uq_action_proposal_key"),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "proposed_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_action_proposal_proposer",
        ),
        CheckConstraint("tool IN ('create_ticket')", name="tool_known"),
        CheckConstraint("char_length(idempotency_key) BETWEEN 8 AND 200", name="key_length"),
        Index("ix_action_proposal_unit_status", "business_unit_id", "status"),
    )


class ActionApproval(TenantScoped, Base):
    """A named human's decision. One per proposal."""

    __tablename__ = "action_approval"

    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    approver_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    approved: Mapped[bool] = mapped_column(nullable=False)
    edited_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # if changed on approval
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_action_approval_proposal"),
        ForeignKeyConstraint(
            ["organisation_id", "proposal_id"],
            ["action_proposal.organisation_id", "action_proposal.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "approver_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_action_approval_approver",
        ),
        CheckConstraint("char_length(reason) <= 500", name="reason_length"),
    )


class ActionExecution(TenantScoped, Base):
    """The one execution of an approved proposal, keyed by its idempotency key."""

    __tablename__ = "action_execution"

    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    succeeded: Mapped[bool] = mapped_column(nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="1")
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_action_execution_proposal"),
        UniqueConstraint("organisation_id", "idempotency_key", name="uq_action_execution_key"),
        ForeignKeyConstraint(
            ["organisation_id", "proposal_id"],
            ["action_proposal.organisation_id", "action_proposal.id"],
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z_]{1,60}$'", name="error_code_format"
        ),
    )


class SimulatedTicket(TenantScoped, Base):
    """SIMULATION ONLY: the fake HR/IT ticketing system (owner decision D4). Never a real
    system; its references start with "SIM-" so they can't be mistaken for real tickets."""

    __tablename__ = "simulated_ticket"

    reference: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "reference", name="uq_simulated_ticket_reference"),
        UniqueConstraint("organisation_id", "idempotency_key", name="uq_simulated_ticket_key"),
        CheckConstraint("reference ~ '^SIM-[0-9A-F]{8}$'", name="reference_format"),
    )

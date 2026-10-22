"""Action rules (who may propose and approve) and the create_ticket tool's input checks."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from lekha.actions import (
    APPROVER_ROLE,
    ApprovalDenial,
    UnknownToolError,
    approval_denial,
    can_propose,
    validate_inputs,
)
from lekha.auth import AuthContext

ALPHA = uuid.uuid4()
BETA = uuid.uuid4()


def _ctx(roles: dict[uuid.UUID, set[str]]) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        display_name="Test",
        badges=frozenset(),
        roles_by_bu={bu: frozenset(r) for bu, r in roles.items()},
    )


def test_anyone_with_a_role_in_the_unit_may_propose_there_and_nowhere_else() -> None:
    ctx = _ctx({ALPHA: {"employee"}})
    assert can_propose(ctx, ALPHA)
    assert not can_propose(ctx, BETA)


def test_an_approver_of_the_unit_may_approve_someone_elses_proposal() -> None:
    ctx = _ctx({ALPHA: {APPROVER_ROLE}})
    assert approval_denial(ctx, business_unit_id=ALPHA, proposed_by=uuid.uuid4()) is None


def test_approvers_never_approve_their_own_proposal() -> None:
    ctx = _ctx({ALPHA: {APPROVER_ROLE}})
    denial = approval_denial(ctx, business_unit_id=ALPHA, proposed_by=ctx.user_id)
    assert denial is ApprovalDenial.OWN_PROPOSAL


@pytest.mark.parametrize(
    "roles",
    [
        {ALPHA: {"employee", "curator"}},  # other roles in the unit are not enough
        {BETA: {APPROVER_ROLE}},  # an approver elsewhere is not an approver here
        {},
    ],
)
def test_only_an_approver_of_that_unit_may_approve(roles: dict[uuid.UUID, set[str]]) -> None:
    denial = approval_denial(_ctx(roles), business_unit_id=ALPHA, proposed_by=uuid.uuid4())
    assert denial is ApprovalDenial.NOT_APPROVER


def test_ticket_inputs_are_normalised_with_a_default_priority() -> None:
    inputs = validate_inputs(
        "create_ticket",
        {"title": "  Line 3 compressor leaking ", "category": "facilities", "description": "Oil"},
    )
    assert inputs == {
        "title": "Line 3 compressor leaking",
        "category": "facilities",
        "priority": "P3",
        "description": "Oil",
    }


@pytest.mark.parametrize(
    "inputs",
    [
        {"title": "ab", "category": "it", "description": "x"},  # title too short
        {"title": "Laptop", "category": "payroll", "description": "x"},  # unknown category
        {"title": "Laptop", "category": "it", "description": "x", "priority": "P0"},
        {"title": "Laptop", "category": "it", "description": ""},
        {"title": "Laptop", "category": "it", "description": "x", "assignee": "ravi"},  # extra
    ],
)
def test_invalid_ticket_inputs_are_refused(inputs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        validate_inputs("create_ticket", inputs)


def test_unknown_tools_are_refused() -> None:
    with pytest.raises(UnknownToolError):
        validate_inputs("send_email", {"to": "someone"})

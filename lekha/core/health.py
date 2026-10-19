"""Readiness checks for the services Lekha depends on.

*Liveness* answers "is the process running?" and never touches dependencies.
*Readiness* answers "can this instance serve traffic right now?" by probing each
dependency concurrently, each with its own timeout, so one slow dependency cannot
hang the probe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger("lekha.health")


class CheckStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    """A named async probe. The probe raises any exception to signal failure."""

    name: str
    probe: Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    checks: dict[str, CheckStatus]

    @property
    def ready(self) -> bool:
        return all(status is CheckStatus.OK for status in self.checks.values())


async def _run_one(check: ReadinessCheck, timeout_seconds: float) -> CheckStatus:
    try:
        await asyncio.wait_for(check.probe(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("readiness_check_timeout", check=check.name, timeout_s=timeout_seconds)
        return CheckStatus.UNAVAILABLE
    except Exception as exc:  # a probe may fail in many driver-specific ways
        # Log the exception type only; messages can contain hostnames or credentials.
        logger.warning("readiness_check_failed", check=check.name, error=type(exc).__name__)
        return CheckStatus.UNAVAILABLE
    return CheckStatus.OK


async def run_readiness_checks(
    checks: Sequence[ReadinessCheck], timeout_seconds: float
) -> ReadinessReport:
    statuses = await asyncio.gather(*(_run_one(c, timeout_seconds) for c in checks))
    return ReadinessReport(checks={c.name: s for c, s in zip(checks, statuses, strict=True)})

"""Layered health (design §9).

`down` only when the database is unreachable; anything else that fails makes Banyan `degraded`
while it keeps serving. Checks report states, never URLs or secrets.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from pydantic import BaseModel

from banyan.steward.client import StewardHealth, StewardUnavailable

CheckStatus = Literal["ok", "degraded", "error", "timeout", "unreachable", "not_configured"]
OverallStatus = Literal["ok", "degraded", "down"]

# Under a typical 4 s container HEALTHCHECK, so /health always answers with a report.
CHECK_TIMEOUT_SECONDS = 3.0


class StewardProbe(Protocol):
    async def health(self) -> StewardHealth: ...


class HealthChecks(BaseModel):
    db: CheckStatus
    steward: CheckStatus
    search: CheckStatus
    roles: dict[str, CheckStatus]


class HealthOut(BaseModel):
    status: OverallStatus
    checks: HealthChecks


async def check_health(
    db_ping: Callable[[], Awaitable[None]],
    steward: StewardProbe | None,
    *,
    timeout_s: float = CHECK_TIMEOUT_SECONDS,
) -> HealthOut:
    if steward is None:
        db, steward_status = await _bounded(db_ping(), timeout_s), "not_configured"
    else:
        db, steward_status = await asyncio.gather(
            _bounded(db_ping(), timeout_s), _bounded(steward.health(), timeout_s)
        )
    checks = HealthChecks(
        db=db,
        steward=steward_status,
        search="not_configured",  # B4
        roles={},  # B1: one entry per configured role
    )
    return HealthOut(status=_overall(checks), checks=checks)


def _overall(checks: HealthChecks) -> OverallStatus:
    if checks.db != "ok":
        return "down"
    others = [checks.steward, checks.search, *checks.roles.values()]
    if all(s in ("ok", "not_configured") for s in others):
        return "ok"
    return "degraded"


async def _bounded(check: Awaitable[CheckStatus | None], timeout_s: float) -> CheckStatus:
    """None means ok. A hang becomes `timeout`, an unreachable steward `unreachable`, anything
    else unexpected `error`: /health never raises."""
    try:
        async with asyncio.timeout(timeout_s):
            return await check or "ok"
    except TimeoutError:
        return "timeout"
    except StewardUnavailable:
        return "unreachable"
    except Exception:
        return "error"

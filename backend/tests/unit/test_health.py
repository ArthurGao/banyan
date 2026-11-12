import asyncio

from banyan.services.health import check_health
from banyan.steward import FakeSteward, StewardUnavailable
from banyan.steward.client import StewardHealth


async def db_ok() -> None:
    return None


async def db_down() -> None:
    raise ConnectionRefusedError("connection refused")


async def hang() -> None:
    await asyncio.sleep(10)


class BrokenSteward:
    async def health(self) -> StewardHealth:
        raise StewardUnavailable("GET /health: ConnectError")


class HangingSteward:
    async def health(self) -> StewardHealth:
        await asyncio.sleep(10)
        return "ok"


async def test_all_ok_without_steward_configured() -> None:
    report = await check_health(db_ok, None)
    assert report.status == "ok"
    assert report.checks.model_dump() == {
        "db": "ok",
        "steward": "not_configured",
        "search": "not_configured",
        "roles": {},
    }


async def test_database_down_is_down() -> None:
    report = await check_health(db_down, FakeSteward())
    assert (report.status, report.checks.db, report.checks.steward) == ("down", "error", "ok")


async def test_database_hang_times_out_and_is_down() -> None:
    report = await check_health(hang, None, timeout_s=0.05)
    assert (report.status, report.checks.db) == ("down", "timeout")


async def test_steward_unreachable_degrades() -> None:
    report = await check_health(db_ok, BrokenSteward())
    assert (report.status, report.checks.steward) == ("degraded", "unreachable")


async def test_steward_hang_times_out_and_degrades() -> None:
    report = await check_health(db_ok, HangingSteward(), timeout_s=0.05)
    assert (report.status, report.checks.steward) == ("degraded", "timeout")


async def test_steward_reporting_degraded_degrades_banyan() -> None:
    report = await check_health(db_ok, FakeSteward(health="degraded"))
    assert (report.status, report.checks.steward) == ("degraded", "degraded")


async def test_steward_ok_is_ok() -> None:
    report = await check_health(db_ok, FakeSteward())
    assert (report.status, report.checks.steward) == ("ok", "ok")


async def test_a_configured_search_is_reported_without_a_probe() -> None:
    report = await check_health(db_ok, None, search="ok")
    assert (report.status, report.checks.search) == ("ok", "ok")

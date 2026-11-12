from fastapi import APIRouter, Request, Response

from banyan.services.health import HealthOut, check_health
from banyan.storage.db import ping

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    responses={503: {"model": HealthOut, "description": "The database is unreachable"}},
)
async def health(request: Request, response: Response) -> HealthOut:
    """Unauthenticated: component states only, no data (design §9)."""
    state = request.app.state
    report = await check_health(lambda: ping(state.engine), state.steward)
    if report.status == "down":
        response.status_code = 503
    return report

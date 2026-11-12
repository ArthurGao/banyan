"""Request dependencies: the API token and a database session.

Unguarded on purpose (web-authz: a written list of public paths): `/health` (component states only),
`/openapi.json`, `/docs`, `/redoc` (the API's shape, no data). Everything under `/v1` requires the
token. Single user (AGENTS.md rule 11): every object belongs to that user, so there is no per-object
ownership check beyond the token.
"""

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.services.answers import Runtime

_bearer = HTTPBearer(auto_error=False)


async def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected: str = request.app.state.settings.api_token.get_secret_value()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="missing or wrong API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as s:
        yield s


Session = Annotated[AsyncSession, Depends(session)]


def runtime(request: Request) -> Runtime:
    """What an answer needs once the request's session is gone (the stream outlives it)."""
    state = request.app.state
    return Runtime.from_settings(state.llm, state.steward, state.sessions, state.settings)


AnswerRuntime = Annotated[Runtime, Depends(runtime)]

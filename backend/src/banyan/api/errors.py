"""Domain errors, and steward's answers to a request that needed it, as RFC 7807 problems."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from banyan.errors import BanyanError
from banyan.steward import StewardRejected, StewardUnavailable

PROBLEM = "application/problem+json"


def problem(status: int, code: str, detail: str) -> JSONResponse:
    body = {"type": f"about:blank#{code}", "title": code, "status": status, "detail": detail}
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install(app: FastAPI) -> None:
    async def domain_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, BanyanError)
        return problem(exc.status, exc.code, exc.detail)

    async def steward_down(_: Request, exc: Exception) -> JSONResponse:
        return problem(503, "steward_unavailable", str(exc))

    async def steward_refused(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StewardRejected)
        code = {404: "not_found", 409: "conflict"}.get(exc.status, "steward_rejected")
        return problem(exc.status, code, exc.detail)

    app.add_exception_handler(BanyanError, domain_error)
    # Requests that read or manage steward state directly (the memory page, design §8.5).
    app.add_exception_handler(StewardUnavailable, steward_down)
    app.add_exception_handler(StewardRejected, steward_refused)

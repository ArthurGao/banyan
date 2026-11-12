"""Domain errors as RFC 7807 problem responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from banyan.errors import BanyanError

PROBLEM = "application/problem+json"


def problem(status: int, code: str, detail: str) -> JSONResponse:
    body = {"type": f"about:blank#{code}", "title": code, "status": status, "detail": detail}
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install(app: FastAPI) -> None:
    async def domain_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, BanyanError)
        return problem(exc.status, exc.code, exc.detail)

    app.add_exception_handler(BanyanError, domain_error)

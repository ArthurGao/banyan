"""Domain errors. The API turns them into RFC 7807 problem responses (api/errors.py)."""


class BanyanError(Exception):
    status = 500
    code = "internal"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(BanyanError):
    status = 404
    code = "not_found"


class Conflict(BanyanError):
    status = 409
    code = "conflict"


class Unprocessable(BanyanError):
    status = 422
    code = "unprocessable"

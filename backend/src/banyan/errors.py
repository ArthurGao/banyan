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


class StewardNotConfigured(BanyanError):
    status = 503
    code = "steward_not_configured"


class RoleFailed(BanyanError):
    """A model call a JSON request needed failed (design §6.4 row 7): no fallback, a 502."""

    status = 502

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.code = f"role_{kind}"

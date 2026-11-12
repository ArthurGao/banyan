"""Settings from the environment (prefix BANYAN_).

Secrets come only from here (AGENTS.md rule 10).
"""

from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DB_SCHEMA = "banyan"
TEST_SCHEMA_SUFFIX = "_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BANYAN_", env_file=".env", extra="ignore")

    # Required on purpose: a default would point a forgotten environment at some database.
    database_url: str
    db_schema: str = Field(default=DEFAULT_DB_SCHEMA, pattern=r"^[a-z_][a-z0-9_]{0,62}$")

    # The single user's API token (design §9): every /v1 request carries it as a Bearer token.
    api_token: SecretStr = Field(min_length=16)
    # The constant user passed to steward (AGENTS.md rule 11).
    user_id: str = Field(default="owner", min_length=1, max_length=200)

    # LLM roles (design §7): the YAML that maps roles to models. Required unless `fakes` is on.
    roles_file: Path | None = None
    role_timeout_s: float = Field(default=60.0, gt=0)
    # Development and frontend e2e only: every role answers from FakeLLM, steward is FakeSteward.
    fakes: bool = False
    fake_delay_s: float = Field(default=0.0, ge=0, le=5)  # between fake stream chunks
    # Branch history sent with a question, in estimated tokens (design §6.5).
    history_budget: int = Field(default=2000, gt=0)

    # llm-steward (design §8.1): all three or none. Unset URL means steward is not configured and
    # /health says so. The API key has scopes read+write; the review key (scope review) is used
    # only to create collections and deprecate entries, because a review key's writes go live
    # `verified`.
    steward_url: str | None = None
    steward_api_key: SecretStr | None = None
    steward_review_key: SecretStr | None = None
    steward_timeout_s: float = Field(default=10.0, gt=0)

    @field_validator("db_schema")
    @classmethod
    def _not_a_test_schema(cls, value: str) -> str:
        # Tests drop `*_test` schemas with CASCADE; the app must never live in one.
        if value.endswith(TEST_SCHEMA_SUFFIX):
            raise ValueError(
                f"schema {value!r} is reserved for tests (ends with {TEST_SCHEMA_SUFFIX!r})"
            )
        return value

    @field_validator("steward_url", "steward_api_key", "steward_review_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # `BANYAN_STEWARD_URL=` in a .env file arrives as "", not as an absent variable.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("steward_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or " " in value:
            raise ValueError("steward_url must be an http(s) URL with a host")
        return value

    @model_validator(mode="after")
    def _steward_complete(self) -> Self:
        # A partial group would only fail at the first request (401/403); fail at startup instead.
        if self.steward_url is not None and (
            self.steward_api_key is None or self.steward_review_key is None
        ):
            raise ValueError(
                "steward_url is set: steward_api_key and steward_review_key are required too"
            )
        return self

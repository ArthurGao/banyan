"""Settings from the environment (prefix BANYAN_).

Secrets come only from here (AGENTS.md rule 10).
"""

from pathlib import Path
from typing import Any, Literal, Self
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
    # A KB hit: `judge.sufficiency` says sufficient with at least this coverage (design §6.5).
    judge_threshold: float = Field(default=0.7, ge=0, le=1)
    judge_threshold_explain: float = Field(default=0.85, ge=0, le=1)
    # Retrieval (design §6.2 step 3, §6.5): items kept and their estimated tokens.
    retrieve_top_k: int = Field(default=8, ge=1, le=50)
    context_budget: int = Field(default=3000, gt=0)

    # Jobs and the worker (design §4, §5.1.1).
    run_worker: bool = (
        True  # `banyan serve` also runs the worker; false when `banyan worker` runs it
    )
    worker_slots: int = Field(default=4, ge=1, le=32)
    job_lease_s: float = Field(default=300.0, gt=0)
    job_max_attempts: int = Field(default=6, ge=1)
    bulk_concurrency: int = Field(default=2, ge=1)
    bulk_max_tokens: int = Field(default=500_000, gt=0)
    explain_tokens_estimate: int = Field(default=20_000, gt=0)
    # A `streaming` answer older than this belongs to a process that died; it becomes `failed`.
    stream_stale_s: float = Field(default=600.0, gt=0)

    # llm-steward (design §8.1): all three or none. Unset URL means steward is not configured and
    # /health says so. The API key has scopes read+write; the review key (scope review) is used
    # only to create collections and deprecate entries, because a review key's writes go live
    # `verified`.
    steward_url: str | None = None
    steward_api_key: SecretStr | None = None
    steward_review_key: SecretStr | None = None
    steward_timeout_s: float = Field(default=10.0, gt=0)

    # The web (design §7.3, §6.5). No provider: no web tools; a time-sensitive answer is marked
    # `stale_risk`. Tavily is the default provider (owner decision 2026-11-12).
    search_provider: Literal["tavily"] | None = None
    search_api_key: SecretStr | None = None
    search_max_results: int = Field(default=5, ge=1, le=20)
    fetch_max_tokens: int = Field(default=4000, gt=0)  # one fetched page, after extraction
    fetch_max_bytes: int = Field(default=2_000_000, gt=0)  # one response body, before extraction
    allow_private_urls: bool = False  # local tests only: lifts the outbound guard's address rule
    web_valid_days: int = Field(default=90, gt=0)  # web-derived KB entries expire (design §8.2)
    # The answer loop (answer.compose / node.explain with tools, design §6.2 step 5).
    answer_max_steps: int = Field(default=6, ge=1, le=20)  # model requests in one answer
    answer_max_tokens: int = Field(default=2000, gt=0)  # answer.compose output
    answer_timeout_s: float = Field(default=180.0, gt=0)  # the whole loop, searches included
    # The whole loop's tokens, input and output of every request (AGENTS.md rule 3: a hard
    # token budget). Each request re-sends what came before, fetched pages included.
    answer_loop_tokens: int = Field(default=100_000, gt=0)

    @field_validator("db_schema")
    @classmethod
    def _not_a_test_schema(cls, value: str) -> str:
        # Tests drop `*_test` schemas with CASCADE; the app must never live in one.
        if value.endswith(TEST_SCHEMA_SUFFIX):
            raise ValueError(
                f"schema {value!r} is reserved for tests (ends with {TEST_SCHEMA_SUFFIX!r})"
            )
        return value

    @field_validator(
        "steward_url", "steward_api_key", "steward_review_key", "search_provider",
        "search_api_key", mode="before",
    )  # fmt: skip
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

    @model_validator(mode="after")
    def _search_complete(self) -> Self:
        if self.search_provider is not None and self.search_api_key is None:
            raise ValueError("search_provider is set: search_api_key is required too")
        return self

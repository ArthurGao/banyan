import pytest
from pydantic import ValidationError

from banyan.config import Settings

URL = "postgresql+asyncpg://u:p@localhost:5432/banyan"
TOKEN = "a-token-of-16-chars"

pytestmark = pytest.mark.usefixtures("no_env")


def test_database_url_is_required() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, api_token=TOKEN)  # type: ignore[call-arg, arg-type]


def test_defaults_to_the_banyan_schema_and_no_steward() -> None:
    settings = Settings(_env_file=None, database_url=URL, api_token=TOKEN)
    assert settings.db_schema == "banyan"
    assert settings.steward_url is None


@pytest.mark.parametrize("schema", ["banyan_test", "anything_test"])
def test_a_test_schema_is_refused(schema: str) -> None:
    with pytest.raises(ValidationError, match="reserved for tests"):
        Settings(_env_file=None, database_url=URL, api_token=TOKEN, db_schema=schema)


@pytest.mark.parametrize("schema", ["Banyan", "1banyan", "ban-yan", "a; drop schema x"])
def test_a_schema_name_must_be_a_plain_identifier(schema: str) -> None:
    with pytest.raises(ValidationError, match="db_schema"):
        Settings(_env_file=None, database_url=URL, api_token=TOKEN, db_schema=schema)


@pytest.mark.parametrize(
    "keys",
    [{}, {"steward_api_key": "k"}, {"steward_review_key": "r"}],
)
def test_a_partial_steward_group_fails_at_startup(keys: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="steward_api_key and steward_review_key"):
        Settings(_env_file=None, database_url=URL, api_token=TOKEN, steward_url="http://s", **keys)  # type: ignore[arg-type]


def test_a_complete_steward_group_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        database_url=URL,
        api_token=TOKEN,  # type: ignore[arg-type]
        steward_url="http://s",
        steward_api_key="k",  # type: ignore[arg-type]
        steward_review_key="r",  # type: ignore[arg-type]
    )
    assert settings.steward_review_key is not None
    assert settings.steward_review_key.get_secret_value() == "r"


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_steward_url_means_not_configured(blank: str) -> None:
    # `BANYAN_STEWARD_URL=` in a .env file is an empty string, not an absent variable.
    settings = Settings(_env_file=None, database_url=URL, api_token=TOKEN, steward_url=blank)
    assert settings.steward_url is None


def test_blank_steward_keys_count_as_missing() -> None:
    with pytest.raises(ValidationError, match="steward_api_key and steward_review_key"):
        Settings(
            _env_file=None,
            database_url=URL,
            api_token=TOKEN,  # type: ignore[arg-type]
            steward_url="http://s",
            steward_api_key="",  # type: ignore[arg-type]
            steward_review_key=" ",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "url", ["127.0.0.1:8600", "ftp://steward", "http://", "http://?x", "http://bad host"]
)
def test_the_steward_url_must_be_http_with_a_host(url: str) -> None:
    with pytest.raises(ValidationError, match="steward_url"):
        Settings(
            _env_file=None,
            database_url=URL,
            api_token=TOKEN,  # type: ignore[arg-type]
            steward_url=url,
            steward_api_key="k",  # type: ignore[arg-type]
            steward_review_key="r",  # type: ignore[arg-type]
        )


def test_the_api_token_is_required_and_not_trivially_short() -> None:
    with pytest.raises(ValidationError, match="api_token"):
        Settings(_env_file=None, database_url=URL)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="api_token"):
        Settings(_env_file=None, database_url=URL, api_token="short")  # type: ignore[arg-type]

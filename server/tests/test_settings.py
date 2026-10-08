import pytest

from app.infra.settings import (
    VALID_ENVS,
    ConfigError,
    Settings,
    load_settings,
    redact_database_url,
)

VALID_URL = "postgresql+psycopg://app:secret@db.example.com:5432/flutter"


def test_defaults_are_safe():
    settings = load_settings({})
    assert settings.app_env == "development"
    assert settings.database_url is None
    assert settings.db_connect_timeout_seconds == 5
    assert settings.db_application_name == "flutter-wars"


def test_defaults_have_no_database_url():
    assert load_settings({}).database_url is None


@pytest.mark.parametrize("env", VALID_ENVS)
def test_each_valid_environment_loads(env):
    assert load_settings({"APP_ENV": env}).app_env == env


def test_invalid_app_env_rejected():
    with pytest.raises(ConfigError):
        load_settings({"APP_ENV": "staging-typo"})


def test_invalid_database_url_scheme_rejected():
    with pytest.raises(ConfigError):
        load_settings({"DATABASE_URL": "mysql://user:pass@host/db"})


def test_invalid_int_rejected():
    with pytest.raises(ConfigError):
        load_settings({"DB_CONNECT_TIMEOUT_SECONDS": "abc"})


def test_out_of_range_int_rejected():
    with pytest.raises(ConfigError):
        load_settings({"DB_CONNECT_TIMEOUT_SECONDS": "0"})


def test_valid_settings_load():
    settings = load_settings(
        {
            "APP_ENV": "test",
            "DATABASE_URL": VALID_URL,
            "DB_APPLICATION_NAME": "flutter-wars-ci",
        }
    )
    assert settings.app_env == "test"
    assert settings.database_url == VALID_URL
    assert settings.db_application_name == "flutter-wars-ci"


def test_repr_and_describe_never_leak_password():
    secret = "super-secret-password"
    settings = Settings(database_url=f"postgresql+psycopg://app:{secret}@host:5432/db")
    for text in (repr(settings), str(settings.describe())):
        assert secret not in text
        assert "***" in text


def test_redact_handles_unset_and_bad_values():
    assert redact_database_url("") == "<unset>"
    assert "***" in redact_database_url("postgresql://u:p@h/db")
    assert redact_database_url("postgresql://h/db") == "postgresql://h/db"

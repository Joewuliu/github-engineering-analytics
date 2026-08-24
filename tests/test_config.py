from app.config import Settings


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "GitHub Engineering Analytics"
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"


def test_settings_env_var_override(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Custom Name")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Custom Name"
    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"

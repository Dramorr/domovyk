"""FR-PRP-5: DEV_SKIP_OWNERSHIP_CHECK поза dev — застосунок не стартує."""

from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import SecretStr

from domovyk.config import ConfigError, Settings, get_settings


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_dev_flag_allowed_in_dev() -> None:
    s = _settings(app_env="dev", dev_skip_ownership_check=True)
    assert s.dev_skip_ownership_check is True


@pytest.mark.parametrize("env", ["test", "staging", "prod"])
def test_dev_flag_outside_dev_refuses_to_start(env: str) -> None:
    with pytest.raises(ConfigError, match="DEV_SKIP_OWNERSHIP_CHECK"):
        _settings(app_env=env, dev_skip_ownership_check=True)


@pytest.mark.parametrize("env", ["test", "staging", "prod"])
def test_dev_flag_from_env_vars_refuses_to_start(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("DEV_SKIP_OWNERSHIP_CHECK", "true")
    with pytest.raises(ConfigError):
        _settings()


def test_api_refuses_to_start_with_dev_flag_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from domovyk.interfaces.api.app import create_app

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEV_SKIP_OWNERSHIP_CHECK", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError):
            create_app()
    finally:
        get_settings.cache_clear()


def test_worker_refuses_to_start_with_dev_flag_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEV_SKIP_OWNERSHIP_CHECK", "true")
    get_settings.cache_clear()
    sys.modules.pop("domovyk.workers", None)
    try:
        with pytest.raises(ConfigError):
            importlib.import_module("domovyk.workers")
    finally:
        sys.modules.pop("domovyk.workers", None)
        get_settings.cache_clear()


def test_cors_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.test, https://b.test")
    assert _settings().cors_origins == ["https://a.test", "https://b.test"]


def test_require_reports_missing_secrets() -> None:
    s = _settings(telegram_bot_token=SecretStr(""))
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        s.require("telegram_bot_token")

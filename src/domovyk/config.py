"""Налаштування застосунку з env / .env (pydantic-settings).

Усі секрети — лише тут, через змінні середовища. Шаблон — `.env.example`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["dev", "test", "staging", "prod"]


class ConfigError(RuntimeError):
    """Небезпечна або неповна конфігурація: застосунок не має стартувати."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = "dev"

    telegram_bot_token: SecretStr = SecretStr("")
    # Публічна HTTPS-адреса Mini App (GitHub Pages або тунель у dev).
    mini_app_url: str = "https://dramorr.github.io/domovyk/app/"

    database_url: str = "postgresql+asyncpg://domovyk:domovyk@localhost:5432/domovyk"
    redis_url: str = "redis://localhost:6379/0"

    hmac_secret: SecretStr = SecretStr("")
    fernet_key: SecretStr = SecretStr("")

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Origin-и, яким дозволено звертатись до API (FR-TMA-7). Через кому.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["https://dramorr.github.io"]
    )
    # Скільки секунд initData вважається свіжим (FR-TMA-2).
    init_data_max_age_seconds: int = 24 * 60 * 60

    # Перші адміністратори (Telegram ID через кому) — отримують роль admin при реєстрації.
    admin_telegram_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # Версії й адреси документів, з якими погоджується користувач (FR-USR-1).
    terms_version: str = "2026-11-draft"
    privacy_version: str = "2026-11-draft"
    terms_url: str = "https://dramorr.github.io/domovyk/legal/terms.html"
    privacy_url: str = "https://dramorr.github.io/domovyk/legal/privacy.html"

    # Сховище станів FSM бота. memory — для dev без Docker/Redis.
    bot_fsm_storage: Literal["redis", "memory"] = "redis"

    # Тунель для dev (docs/TUNNEL.md): API на ноуті стає доступним Mini App на Pages.
    ngrok_domain: str = ""  # сталий безкоштовний домен, напр. xxx.ngrok-free.dev
    ngrok_authtoken: SecretStr = SecretStr("")
    ngrok_path: str = ""  # шлях до ngrok.exe, якщо його немає в PATH

    # Геокодер адрес (FR-PRP-2): nominatim — OpenStreetMap без ключа; manual — без мережі.
    geocoder: Literal["nominatim", "manual"] = "nominatim"
    nominatim_url: str = "https://nominatim.openstreetmap.org"

    # Сховище файлів (документи власності — зашифровані FERNET_KEY). Папка не в git.
    storage_dir: str = "storage"
    # Документи власності: розмір і скільки днів зберігати після рішення (FR-PRP-4).
    ownership_doc_max_mb: int = Field(default=10, ge=1, le=20)
    ownership_doc_retention_days: int = Field(default=30, ge=1)
    # Рівні заповненості анкети об'єкта (FR-PRP-8), відсотки.
    profile_medium_from: int = Field(default=40, ge=0, le=100)
    profile_high_from: int = Field(default=75, ge=0, le=100)
    # Мінімальна заповненість анкети для публікації оголошення, %.
    listing_min_completeness: int = Field(default=40, ge=0, le=100)

    # Dev-прапорці — лише при APP_ENV=dev (FR-PRP-5).
    dev_skip_ownership_check: bool = False

    @model_validator(mode="before")
    @classmethod
    def _split_cors(cls, data: object) -> object:
        if isinstance(data, dict):
            for key in ("cors_origins", "CORS_ORIGINS", "admin_telegram_ids", "ADMIN_TELEGRAM_IDS"):
                raw = data.get(key)
                if isinstance(raw, str):
                    data[key] = [o.strip() for o in raw.split(",") if o.strip()]
                elif isinstance(raw, int):
                    data[key] = [raw]
        return data

    @model_validator(mode="after")
    def _guard_dev_flags(self) -> Settings:
        if self.dev_skip_ownership_check and self.app_env != "dev":
            raise ConfigError(
                f"DEV_SKIP_OWNERSHIP_CHECK=true дозволено лише при APP_ENV=dev, "
                f"а зараз APP_ENV={self.app_env}. Застосунок не стартує."
            )
        return self

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    def require(self, *names: str) -> None:
        """Перевірити, що потрібні для процесу секрети задані (кличемо на старті)."""
        missing = []
        for name in names:
            value = getattr(self, name)
            raw = value.get_secret_value() if isinstance(value, SecretStr) else value
            if not raw:
                missing.append(name.upper())
        if missing:
            raise ConfigError("Не задано змінні середовища: " + ", ".join(missing))


@lru_cache
def get_settings() -> Settings:
    return Settings()

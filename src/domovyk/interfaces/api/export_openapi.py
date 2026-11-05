"""Вивести OpenAPI-схему для генерації типів Mini App.

`uv run python -m domovyk.interfaces.api.export_openapi > web/openapi.json`
"""

import json
import sys

from cryptography.fernet import Fernet
from pydantic import SecretStr

from domovyk.config import Settings
from domovyk.interfaces.api.app import create_app


def main() -> None:
    # Секрети тут не використовуються — лише щоб зібрати застосунок (без мережі й БД).
    settings = Settings(
        _env_file=None,
        app_env="test",
        telegram_bot_token=SecretStr("123456:openapi-export"),
        hmac_secret=SecretStr("openapi-export"),
        fernet_key=SecretStr(Fernet.generate_key().decode()),
    )
    schema = create_app(settings).openapi()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

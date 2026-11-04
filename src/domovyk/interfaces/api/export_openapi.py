"""Вивести OpenAPI-схему для генерації типів Mini App.

`uv run python -m domovyk.interfaces.api.export_openapi > web/openapi.json`
"""

import json
import sys

from pydantic import SecretStr

from domovyk.config import Settings
from domovyk.interfaces.api.app import create_app


def main() -> None:
    # Токен тут не використовується для перевірок — лише щоб зібрати застосунок.
    settings = Settings(app_env="test", telegram_bot_token=SecretStr("openapi-export"))
    schema = create_app(settings).openapi()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

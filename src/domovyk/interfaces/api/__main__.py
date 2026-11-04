"""Запуск API: `uv run python -m domovyk.interfaces.api`."""

import uvicorn

from domovyk.config import get_settings
from domovyk.core.logging import setup_logging
from domovyk.interfaces.api.app import create_app


def main() -> None:
    settings = get_settings()
    setup_logging()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()

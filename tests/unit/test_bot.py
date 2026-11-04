from __future__ import annotations

from domovyk.interfaces.telegram.keyboards import mini_app_url, open_app_keyboard

BASE = "https://dramorr.github.io/domovyk/app/"


def test_mini_app_url_passes_valid_payload() -> None:
    assert mini_app_url(BASE, "inv_abc-123") == BASE + "?start=inv_abc-123"


def test_mini_app_url_drops_invalid_payload() -> None:
    assert mini_app_url(BASE, "<script>") == BASE
    assert mini_app_url(BASE, "x" * 65) == BASE
    assert mini_app_url(BASE, None) == BASE


def test_open_app_keyboard_uses_web_app() -> None:
    kb = open_app_keyboard(BASE, "src")
    button = kb.inline_keyboard[0][0]
    assert button.web_app is not None
    assert button.web_app.url.endswith("?start=src")

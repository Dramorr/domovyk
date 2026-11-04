from __future__ import annotations

import sys
from pathlib import Path

import pytest

from domovyk.adapters.tunnel.base import TunnelError
from domovyk.adapters.tunnel.ngrok import NgrokTunnel, parse_started_url

STARTED = (
    't=2026-11-04T18:00:00+0200 lvl=info msg="started tunnel" obj=tunnels name=command_line '
    "addr=http://127.0.0.1:8000 url=https://example-domain.ngrok-free.dev\n"
)


def test_parse_started_url() -> None:
    assert parse_started_url(STARTED) == "https://example-domain.ngrok-free.dev"
    assert parse_started_url('lvl=info msg="client session established"') is None


def test_requires_domain() -> None:
    with pytest.raises(TunnelError):
        NgrokTunnel(domain="")


def _fake_ngrok(tmp_path: Path, body: str) -> str:
    """Фейковий «ngrok»: Python-скрипт, що друкує рядки логу. Запускаємо через лаунчер."""
    script = tmp_path / "fake_ngrok.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "ngrok.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / "ngrok"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    return str(launcher)


async def test_start_and_stop_with_fake_process(tmp_path: Path) -> None:
    exe = _fake_ngrok(
        tmp_path,
        f"import sys, time\nprint({STARTED.strip()!r}, flush=True)\ntime.sleep(5)\n",
    )
    tunnel = NgrokTunnel(domain="example-domain.ngrok-free.dev", exe=exe)
    url = await tunnel.start(8000)
    assert url == "https://example-domain.ngrok-free.dev"
    await tunnel.stop()


async def test_reports_ngrok_error(tmp_path: Path) -> None:
    exe = _fake_ngrok(tmp_path, "print('lvl=eror msg=\"failed\" err=ERR_NGROK_108', flush=True)\n")
    tunnel = NgrokTunnel(domain="example-domain.ngrok-free.dev", exe=exe)
    with pytest.raises(TunnelError, match="ERR_NGROK_108"):
        await tunnel.start(8000)
    await tunnel.stop()

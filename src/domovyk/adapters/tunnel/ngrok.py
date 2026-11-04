"""ngrok зі сталим безкоштовним доменом. Запускається дочірнім процесом і зупиняється з ним."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from pathlib import Path

from domovyk.adapters.tunnel.base import TunnelError

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"url=(https://[a-z0-9.-]+)")
_ERR_RE = re.compile(r"ERR_NGROK_\d+")
_START_TIMEOUT = 40.0
_STOP_TIMEOUT = 5.0

# Типове місце, куди ставить ngrok `winget install Ngrok.Ngrok`.
_WINGET_GLOB = "AppData/Local/Microsoft/WinGet/Packages/Ngrok.Ngrok_*/ngrok.exe"


def find_ngrok(explicit: str = "") -> str:
    if explicit:
        return explicit
    found = shutil.which("ngrok")
    if found:
        return found
    for candidate in Path.home().glob(_WINGET_GLOB):
        return str(candidate)
    raise TunnelError("ngrok не знайдено: встанови його або задай NGROK_PATH")


def parse_started_url(line: str) -> str | None:
    if "started tunnel" in line and (m := _URL_RE.search(line)):
        return m.group(1)
    return None


class NgrokTunnel:
    def __init__(self, domain: str, authtoken: str = "", exe: str = "") -> None:
        if not domain:
            raise TunnelError("Не задано NGROK_DOMAIN")
        self._domain = domain
        self._authtoken = authtoken
        self._exe = exe
        self._proc: asyncio.subprocess.Process | None = None
        self._drain: asyncio.Task[None] | None = None

    async def start(self, port: int) -> str:
        env = dict(os.environ)
        if self._authtoken:
            env["NGROK_AUTHTOKEN"] = self._authtoken
        self._proc = await asyncio.create_subprocess_exec(
            find_ngrok(self._exe),
            "http",
            f"--domain={self._domain}",
            f"127.0.0.1:{port}",
            "--log=stdout",
            "--log-format=logfmt",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            url = await asyncio.wait_for(self._read_url(), _START_TIMEOUT)
        except TimeoutError:
            await self.stop()
            raise TunnelError("ngrok не відкрив тунель за 40 с") from None
        except TunnelError:
            await self.stop()
            raise
        # Дочитуємо вивід і далі, інакше буфер заповниться і тунель зависне.
        self._drain = asyncio.create_task(self._drain_output())
        return url

    def _stdout(self) -> asyncio.StreamReader:
        if self._proc is None or self._proc.stdout is None:
            raise TunnelError("ngrok не запущено")
        return self._proc.stdout

    async def _read_url(self) -> str:
        stdout = self._stdout()
        errors: list[str] = []
        while line := await stdout.readline():
            text = line.decode(errors="replace")
            if url := parse_started_url(text):
                return url
            if m := _ERR_RE.search(text):
                errors.append(m.group(0))
                log.error("ngrok: %s", text.strip())
        hint = ""
        if "ERR_NGROK_108" in errors:
            hint = " (уже працює інший агент ngrok цього акаунта — наприклад, spender)"
        raise TunnelError(f"ngrok завершився: {', '.join(errors) or 'без помилки'}{hint}")

    async def _drain_output(self) -> None:
        stdout = self._stdout()
        while await stdout.readline():
            pass

    async def stop(self) -> None:
        if self._drain:
            self._drain.cancel()
            self._drain = None
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), _STOP_TIMEOUT)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        # Дочекатися завершення завжди: інакше лишаться незакриті канали процесу.
        await proc.wait()
        # На Windows канали закриваються в наступному кроці циклу подій.
        await asyncio.sleep(0.1)

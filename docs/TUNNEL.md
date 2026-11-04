# Тунель: як Mini App на GitHub Pages дістається до API на ноуті

На беті (до сервера в M8) API і бот працюють на ноуті. Mini App — статична сторінка на GitHub Pages — ходить до API через **ngrok зі сталим безкоштовним доменом**. Підхід той самий, що у spender.

```
Telegram ── Mini App (dramorr.github.io/domovyk/app/) ──fetch──▶ https://<домен>.ngrok-free.dev ──▶ ngrok ──▶ 127.0.0.1:8000 (FastAPI)
```

Код: [`adapters/tunnel/ngrok.py`](../src/domovyk/adapters/tunnel/ngrok.py), запуск — [`domovyk/dev.py`](../src/domovyk/dev.py).

## 1. Разово

1. ngrok уже встановлено (`winget install --id Ngrok.Ngrok -e`).
2. [dashboard.ngrok.com](https://dashboard.ngrok.com):
   - **Your Authtoken** → скопіюй у `.env` як `NGROK_AUTHTOKEN` (це секрет);
   - **Domains** → твій безкоштовний домен `xxx.ngrok-free.dev` → у `.env` як `NGROK_DOMAIN`.
3. Адреса API = `https://` + домен. Її вписуєш у змінну репозиторію **`API_URL`**: GitHub → репозиторій → Settings → Secrets and variables → Actions → вкладка **Variables** → `API_URL` → Edit. Там же **`BOT_USERNAME`** — нік бота без `@`.
4. Після зміни змінних перезбери Pages: Actions → **pages** → Run workflow (або `gh workflow run pages.yml`). Значення вшиваються в Mini App під час збірки.

> **Один акаунт ngrok = один домен і один одночасний агент.** Якщо spender тримає тунель, Домовик з тим самим акаунтом отримає `ERR_NGROK_108`. Варіанти: зупинити spender, окремий безкоштовний акаунт ngrok для Домовика (зручніше — свій сталий домен), або платний план.

## 2. Запуск

```bash
uv run python -m domovyk.dev
```

Піднімає API на `127.0.0.1:API_PORT`, відкриває тунель, запускає бота. Ctrl+C зупиняє все разом із ngrok. Без Docker/Redis постав у `.env` `BOT_FSM_STORAGE=memory`.

Перевірка: `curl https://<домен>/health` → `{"status":"ok",…}`; `curl https://<домен>/api/v1/me` → 401.

## 3. Чому так

- **Адреса API вшита в збірку, а не передається параметром посилання.** У spender бот передавав хост через `?api=`. Для Домовика це небезпечно: будь-хто міг би зробити посилання на Mini App зі своїм `?api=` і отримати `initData` користувача (з ним 24 год можна ходити до нашого API від його імені). Сталий домен ngrok робить параметр непотрібним.
- **API слухає лише `127.0.0.1`**, назовні — тільки через тунель.
- **Кожен запит Mini App** несе `Authorization: tma <initData>` (бекенд перевіряє підпис і свіжість) і `ngrok-skip-browser-warning: 1` (інакше безкоштовний ngrok віддає HTML-попередження).
- **CORS** — лише origin `https://dramorr.github.io` (`CORS_ORIGINS`).
- **Ноут має бути увімкнений**, інакше Mini App покаже «Немає зв'язку із сервером».

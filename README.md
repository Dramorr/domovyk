# Домовик

Оренда житла у Львові напряму між власником і орендарем — без ріелторів. Від пошуку до договору через Дію і щомісячних оплат.

Статус: **бета, у розробці** — етап M0 (каркас) готовий.

- Сторінка проєкту: https://dramorr.github.io/domovyk/
- Mini App: https://dramorr.github.io/domovyk/app/ (відкривається з бота)

## Що тут є

| Файл | Що це |
|---|---|
| `CLAUDE.md` | Правила проєкту для Claude Code: архітектура, межі модулів, конвенції, бренд |
| `docs/SPEC.md` | Технічне завдання: вимоги `FR-…`, статуси, етапи M0–M8, відкриті питання |
| `docs/BOT_FLOWS.md` | Сценарії інтерфейсу (Mini App і бот): екрани, кнопки, стани, каталог сповіщень |
| `docs/decisions/` | Рішення (ADR), напр. [0001 — Mini App як основний інтерфейс](docs/decisions/0001-telegram-mini-app-first.md) |
| `db/schema.sql` | Цільова схема PostgreSQL 16 + PostGIS (реальні зміни — міграціями Alembic) |
| `assets/brand/` | Знак (ключ-домовик), іконка застосунку, маскот (з ключем і з ліхтариком) |
| `src/domovyk/` | Бекенд: конфіг, core, домен, модулі, адаптери, бот, API, воркер |
| `web/` | Telegram Mini App (React + TypeScript + Vite) |
| `migrations/` | Міграції Alembic |
| `site/` | Сторінка проєкту на GitHub Pages (деплой — `.github/workflows/pages.yml`) |
| `SECURITY.md` | Як повідомити про вразливість і як ми не пускаємо секрети в git |

## Секрети

Жодних токенів, ключів і паролів у репозиторії. Усе — у `.env` (ігнорується git), шаблон — `.env.example`.
Перевірки: gitleaks у CI та локально (`uvx pre-commit install`), GitHub secret scanning з push protection.

## Локальний запуск

Потрібні: [uv](https://docs.astral.sh/uv/), Node 22 + pnpm, Docker Desktop.

```bash
cp .env.example .env                # заповни TELEGRAM_BOT_TOKEN, HMAC_SECRET, FERNET_KEY
docker compose up -d db redis
uv sync
uv run alembic upgrade head
uv run python -m domovyk.interfaces.api        # API
uv run python -m domovyk.interfaces.telegram   # бот
uv run arq domovyk.workers.WorkerSettings      # воркер
pnpm -C web install && pnpm -C web dev         # Mini App
```

Або все одразу: `docker compose up`.

Telegram відкриває Mini App лише за HTTPS. Щоб відкрити локальну версію з бота, прокинь тунель (наприклад `cloudflared tunnel --url http://localhost:5173`) і вкажи його адресу в `MINI_APP_URL`. Для API — так само, адресу API передай у `VITE_API_URL` (`web/.env`).

Перевірки: `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, `pnpm -C web lint`.

## Відкритий код і права

Код відкритий для перегляду, щоб кожен міг перевірити, як Домовик поводиться з даними й грошима.
Ліцензії поки немає: **усі права захищені**, копіювати чи запускати власну копію без дозволу автора не можна.
Назва «Домовик», логотип і маскот — бренд проєкту.

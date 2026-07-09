# AI Tamagotchi Telegram Mini App

Telegram Mini App с локальным питомцем, AI-диалогами, путешествиями и фоновыми
Telegram-историями.

## Как работает приложение

1. Пользователь описывает питомца.
2. Backend создаёт асинхронную job: генерирует вертикальную сцену с персонажем,
   затем короткое видео и сохраняет ассеты в `/static/generated`.
3. Frontend хранит питомца, параметры, чат и память в `localStorage`.
4. Mini App синхронизирует компактный snapshot с backend для Telegram push и
   фоновых историй.
5. Bot и backend совместно используют JSON registry с межпроцессной блокировкой
   и атомарной записью.

Прогресс привязан к origin и устройству браузера: стабильный production-домен
нельзя менять без миграции `localStorage`.

## Стек

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS, Radix UI, Vitest.
- Backend: FastAPI, Pydantic, OpenAI/OpenRouter clients, Pillow.
- Runtime storage: frontend `localStorage`, backend JSON push registry,
  Docker volumes для сгенерированных ассетов и push state.
- Bot: Telegram Bot API long polling.
- Production: Docker Compose, Caddy или внешний reverse proxy.

База данных в активном приложении не используется.

## Локальный запуск

Нужны Python 3.12+, Node.js и npm. Менеджер frontend-зависимостей определяется
по `package-lock.json`: используйте npm.

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev -- --port 3000
```

Или управляйте обоими процессами из корня:

```bash
./scripts/local-dev.sh start
./scripts/local-dev.sh status
./scripts/local-dev.sh logs
./scripts/local-dev.sh stop
```

Runner перед стартом завершает старые backend/frontend процессы на выбранных
портах. Логи и PID-файлы лежат в `.local-dev/`.

Адреса:

- Mini App: http://localhost:3000
- Backend health: http://localhost:8000/health
- OpenAPI: http://localhost:8000/docs

## Локальная авторизация без Telegram

```env
# backend/.env
ALLOW_DEV_TMA_AUTH=true

# frontend/.env.local
NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK=true
BACKEND_URL=http://127.0.0.1:8000
NEXT_PUBLIC_API_URL=
```

В production `ALLOW_DEV_TMA_AUTH` и `NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK`
должны быть `false`.

Для реальной генерации заполните в `backend/.env` как минимум
`OPENROUTER_API_KEY` (или `OPENAI_API_KEY` при `AI_PROVIDER=openai`),
`BOT_TOKEN`, `WEBAPP_URL` и `BACKEND_PUBLIC_URL`. Полный список и defaults
находятся в `backend/.env.example`.

## Telegram tunnel

Для проверки внутри Telegram поднимите один HTTPS tunnel к frontend. Backend
остаётся локальным, а Next проксирует same-origin `/api` и `/static`:

```bash
cd frontend
NEXT_PUBLIC_API_URL= npm run dev -- --hostname 0.0.0.0
cloudflared tunnel --url http://localhost:3000 --no-autoupdate
```

Укажите выданный URL в `WEBAPP_URL`, `BACKEND_PUBLIC_URL`, BotFather и
`CORS_ORIGINS`. Не задавайте клиенту `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`:
в Telegram WebView это адрес устройства пользователя.

## API

Пользовательские endpoints требуют валидный Telegram `initData`:

- `POST /api/generate-pet` и `GET /api/generate-pet/jobs/{job_id}`
- `POST /api/chat`, `/api/chat/ambient`, `/api/chat/proactive`
- `POST /api/travel`
- `POST /api/push/snapshot`
- legacy memory/lite endpoints под `/api/chat/*`

Frontend-типы генерируются из FastAPI OpenAPI. После изменения Pydantic schema
или route выполните:

```bash
cd backend
.venv/bin/python scripts/export_openapi.py ../frontend/openapi.json
cd ../frontend
npm run contracts
```

`make check` и CI отклоняют устаревшие contract-файлы.

## Telegram bot

```bash
cd backend
source .venv/bin/activate
python -m app.bot
```

Команды: `/start`, `/app`, `/help`, `/story`. Генерация `/story`
выполняется в ограниченном worker pool и не блокирует polling.

## Docker и production

Локальный compose:

```bash
docker compose up --build
```

Production-конфигурация: `docker-compose.prod.yml`, `deploy/Caddyfile` и
`deploy/HETZNER.md`. Persistent volumes: `generated_assets`, `push_data`,
`backend_logs`; container Caddy включается профилем `container-caddy`.

## Проверки

```bash
make check
cd frontend && npm run build
```

`make check` запускает Ruff, backend tests, OpenAPI drift check, ESLint,
TypeScript и Vitest.

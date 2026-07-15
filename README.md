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
5. Bot и backend совместно используют SQLite WAL registry и другие локальные SQLite stores
   на persistent volumes; транзакции сериализуют межпроцессные изменения общего state.

Прогресс привязан к origin и устройству браузера: стабильный production-домен
нельзя менять без миграции `localStorage`.

## Стек

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS, Radix UI, Vitest.
- Backend: FastAPI, Pydantic, OpenAI/OpenRouter clients, Pillow.
- Runtime storage: frontend `localStorage`; backend SQLite stores для push registry, jobs, quotas,
  idempotency и bot inbox; Docker volumes для media и durable state.
- Bot: Telegram Bot API long polling.
- Production: Docker Compose во внешней сети существующего Caddy.

Внешний PostgreSQL в активном приложении не используется. Локальные SQLite-файлы являются
частью production state и должны резервироваться вместе с generated media.

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

В чистом окружении без `data/push/telegram_push_state.json` выставьте в `backend/.env`
`TELEGRAM_PUSH_LEGACY_JSON_REQUIRED=false`. Для обновления существующей установки оставьте `true`:
отсутствие ожидаемого legacy registry тогда останавливает миграцию без создания пустого marker.

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

Для создания питомца `OPENAI_API_KEY` обязателен: primary-набор изображений всегда
строится через OpenAI. Дополнительно заполните ключи активных text/media-профилей
(например, `OPENROUTER_API_KEY` для видео профиля `legacy`), а также `BOT_TOKEN`,
`WEBAPP_URL` и `BACKEND_PUBLIC_URL`. Kandinsky-сравнение best-effort и требует
`KANDINSKY_API_KEY`. Полный список и defaults находятся в `backend/.env.example`.

## Маршрутизация текстовых моделей

Текстовые задачи маршрутизируются профилем `LLM_PROFILE` из
`backend/data/llm_runtime.json`:

```env
# Старое поведение текста: провайдер следует AI_PROVIDER.
LLM_PROFILE=legacy

# Весь текст через GigaChat, медиа выбираются независимо.
LLM_PROFILE=gigachat
GIGACHAT_BASE_URL=
GIGACHAT_USERNAME=
GIGACHAT_PASSWORD=

# Быстро вернуть текст на OpenAI Platform.
LLM_PROFILE=openai
OPENAI_API_KEY=
```

После смены профиля пересоздайте backend и bot: обычный `restart` не перечитывает
`backend/.env`, а маршрутизатор и клиенты кэшируются на время жизни процесса.

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml \
  up -d --no-build --force-recreate backend bot
```

`/health` станет `503`, если профиль, dependency или credentials не настроены.
После успешного health всё равно отправьте одну тестовую реплику: endpoint не
делает платный сетевой запрос и не может заранее проверить валидность ключа.

TLS-проверка GigaChat включена. Для своего центра сертификации положите CA-файл
в `deploy/ca/` и задайте контейнерный путь, например
`GIGACHAT_CA_BUNDLE=/app/ca/gigachat-ca.pem`, а не отключайте проверку.

Профиль может задавать один provider/model по умолчанию и переопределения по
задачам. Для модели, поддерживаемой LiteLLM, установите опциональный transport:

```bash
cd backend
pip install -e ".[litellm]"
```

Затем добавьте профиль с `"provider": "litellm"` и LiteLLM model id в
`backend/data/llm_runtime.json`. Выбранная модель должна поддерживать JSON Schema
и function tools: они нужны репликам, памяти и историям. Production-образ уже
устанавливает этот optional transport. Для собственного провайдера достаточно
реализовать нейтральный `LLMProvider.complete()` и зарегистрировать адаптер в
`backend/app/llm/runtime.py`; бизнес-сервисы менять не нужно.

## Маршрутизация изображений и видео

`MEDIA_PROFILE` выбирает профиль из `backend/data/media_runtime.json`. Изображения
и видео имеют отдельные маршруты и могут переопределяться для конкретной задачи:

```env
# Совместимость: изображения следуют AI_PROVIDER, видео — OpenRouter.
MEDIA_PROFILE=legacy

# t2i/i2i через Kandinsky 6.0, i2v через Kandinsky 5 HD.
MEDIA_PROFILE=kandinsky
KANDINSKY_API_KEY=
```

Профили `openai`, `openrouter` и `kandinsky` готовы к переключению. Kandinsky
использует `k6-image-t2i` без референсов и `k6-i2i` с одним или несколькими
референсами. Токен передаётся как Bearer; TLS-проверка не отключается.

Диагностический сравнительный набор питомца по умолчанию отключён. Включение
`PET_COMPARISON_ENABLED=true` создаёт вторую платную линейку Kandinsky для каждого
нового питомца; использовать только в контролируемом диагностическом окружении.

Новый провайдер реализует `ImageProvider` и/или `VideoProvider`, объявляет
capabilities (`text_to_image`, `image_to_image`, `image_to_video`) и регистрируется
в `backend/app/media/runtime.py`. Бизнес-сервисы продолжают вызывать общий media
gateway.

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
- `POST /api/travel/interactive/*`
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

Production-конфигурация: `docker-compose.prod.yml`, `deploy/Caddyfile.host.example` и
`deploy/HETZNER.md`. Persistent volumes: `generated_assets`, `push_data`,
`backend_logs`; backend и frontend подключаются к внешней сети reverse proxy. `push_data` и
`generated_assets` резервируются и восстанавливаются только вместе через
`deploy/backup-volumes.sh` / `deploy/restore-volumes.sh`; runbook — в `deploy/HETZNER.md`.

## Проверки

```bash
make check-fast
make check
cd frontend && npm run build
```

`make check-fast` запускает dependency/OpenAPI drift checks, Ruff, ESLint и TypeScript без
unit-тестов. Это обычная проверка во время разработки. `make check` дополнительно запускает все
backend tests и Vitest; используйте её перед рискованными изменениями и при подготовке финальной
версии. GitHub Actions по-прежнему запускает полный набор после каждого push в `main`.

## Push и deploy одним shortcut

После локальной проверки фичи:

```bash
./scripts/publish.sh "Короткое описание изменения"
```

Скрипт выполняет быстрые проверки, создаёт commit из текущих изменений, отправляет `main`,
обновляет Hetzner через `git pull --ff-only`, пересобирает контейнеры и проверяет production
health. Pull request и `gh` для этого не нужны.

Для рискованного изменения запустите полный набор тестов перед публикацией:

```bash
./scripts/publish.sh "Короткое описание изменения" full
```

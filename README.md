# AI Tamagotchi Telegram Mini App MVP

Telegram Mini App MVP for the AI Tamagotchi core loop:

1. Open the app inside Telegram.
2. Describe a virtual pet.
3. Generate a consistent 4 x 3 sprite sheet through the backend.
4. Store pet progress and chat history in the frontend `localStorage`.
5. Feed, play, quick chat, and full chat with the pet.
6. Reopen the Mini App on the same device without losing local progress.

## Stack

- Frontend: Next.js, React, TypeScript, Tailwind CSS
- Backend: FastAPI, SQLAlchemy, Alembic
- Persistence: frontend `localStorage` for MVP
- Database: PostgreSQL remains for legacy routes and post-MVP persistence
- AI: OpenRouter for chat/image model routing, with optional OpenAI fallback

## MVP Notes

- `localStorage` is the source of truth for pet progress and local chat history.
- Backend does not store Telegram user progress in the MVP flow.
- AI endpoints are `/api/generate-pet` and `/api/chat`.
- Production AI endpoints require valid Telegram Mini App `initData`.
- `initDataUnsafe` is never trusted by the backend.
- Generated assets are served from `/static/generated/...`.
- Use a stable production domain before launch because `localStorage` is origin-bound.

## Local Setup

One-command local runner:

```bash
./scripts/local-dev.sh start
./scripts/local-dev.sh stop
./scripts/local-dev.sh status
./scripts/local-dev.sh logs
```

It starts PostgreSQL, backend, and frontend together. Logs and PID files live in
`.local-dev/`.

Start PostgreSQL through Docker if you need the legacy DB routes:

```bash
docker compose up -d postgres
```

Or use a local PostgreSQL 16 server with:

```txt
POSTGRES_USER=tamagotchi
POSTGRES_PASSWORD=tamagotchi
POSTGRES_DB=tamagotchi
```

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Fill `backend/.env` with keys before real AI/Telegram checks:

```env
BOT_TOKEN=
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_CHAT_MODEL=~openai/gpt-latest
OPENROUTER_IMAGE_MODEL=bytedance-seed/seedream-4.5
WEBAPP_URL=http://localhost:3000
BACKEND_PUBLIC_URL=http://localhost:3000
ALLOW_DEV_TMA_AUTH=true
```

To temporarily use direct OpenAI again, set `AI_PROVIDER=openai` and fill `OPENAI_API_KEY`,
`OPENAI_CHAT_MODEL`, and `OPENAI_IMAGE_MODEL`.

For production, set `ALLOW_DEV_TMA_AUTH=false`.

Frontend:

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev -- --port 3000
```

For browser-only local development without Telegram auth, set both:

```env
# backend/.env
ALLOW_DEV_TMA_AUTH=true

# frontend/.env.local
NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK=true
```

Open:

- Frontend: http://localhost:3000
- Backend health: http://localhost:8000/health

Telegram Mini App local launch uses one HTTPS frontend tunnel. Keep backend local and let Next
proxy same-origin requests:

```bash
cd frontend
NEXT_PUBLIC_API_URL= npm run dev -- --hostname 0.0.0.0

cloudflared tunnel --url http://localhost:3000 --no-autoupdate
```

Put the generated `https://*.trycloudflare.com` URL in:

```env
# backend/.env
WEBAPP_URL=https://your-tunnel.trycloudflare.com
BACKEND_PUBLIC_URL=https://your-tunnel.trycloudflare.com
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000","https://your-tunnel.trycloudflare.com"]

# frontend/.env.local
NEXT_PUBLIC_API_URL=
```

Do not point `NEXT_PUBLIC_API_URL` at `127.0.0.1` for Telegram testing: inside Telegram that would
mean the user's device, not your backend.

## Telegram Bot

Run the minimal polling bot after `BOT_TOKEN` and `WEBAPP_URL` are set:

```bash
cd backend
source .venv/bin/activate
python -m app.bot
```

Commands:

- `/start` sends the Mini App button.
- `/app` sends the Mini App button again.
- `/help` sends a short help message.

For Telegram WebView development without a domain, expose the frontend through the temporary HTTPS
tunnel above and put that URL in `WEBAPP_URL`, BotFather, and/or the bot menu button.

## Docker Compose

```bash
docker compose up --build
```

The compose setup includes frontend, backend, PostgreSQL, and a persistent `generated_assets`
volume for `/app/static/generated`.

## Hetzner Production Deploy

The selected production proxy is Caddy. Production deployment files live in:

- `docker-compose.prod.yml`
- `deploy/Caddyfile`
- `deploy/Caddyfile.host.example`
- `deploy/HETZNER.md`
- `deploy/backend.env.production.example`
- `deploy/compose.env.production.example`

For the current Hetzner server, use `gigagochi.serega.works` as the stable production origin.
Caddy terminates HTTPS and routes `/api/*`, `/static/*`, and `/health` to the backend, with the
remaining traffic going to the frontend. If the server already has host Caddy, use
`deploy/Caddyfile.host.example`; the compose Caddy service is opt-in through the `container-caddy`
profile. Keep the production origin stable because local pet progress is tied to the browser origin.

## Verification

Backend:

```bash
cd backend
source .venv/bin/activate
pytest
ruff check app tests
ruff format app tests --check
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

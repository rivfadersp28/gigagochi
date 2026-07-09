# Frontend

Next.js 16 / React 19 клиент Telegram Mini App.

## Команды

```bash
npm install
cp .env.example .env.local
npm run dev -- --port 3000
npm run check
npm run build
```

`npm run check` проверяет сгенерированный OpenAPI contract, ESLint,
TypeScript и Vitest.

## Переменные окружения

- `BACKEND_URL` — backend для Next same-origin proxy.
- `NEXT_PUBLIC_API_URL` — публичный backend URL; оставьте пустым для proxy.
- `NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK=true` — только локальная разработка
  вместе с backend `ALLOW_DEV_TMA_AUTH=true`.

API wire-типы находятся в `src/lib/generated/openapi.d.ts`. Не редактируйте
их вручную; используйте `npm run contracts` после экспорта `openapi.json`.

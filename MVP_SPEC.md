# AI Tamagotchi MVP: подробное техническое задание

Дата фиксации: 2026-06-30

## 1. Цель проекта

Нужно разработать локальный MVP веб-приложения AI Tamagotchi.

Пользователь создает виртуального питомца по текстовому описанию. Приложение генерирует визуальный набор персонажа в единой стилистике, сохраняет питомца в базе данных, показывает его на главном экране и позволяет общаться с ним в чате.

MVP должен быть простым, понятным и расширяемым. Главная цель - быстро проверить core loop:

1. Пользователь описывает питомца.
2. Приложение генерирует консистентный визуальный набор.
3. Питомец имеет Hunger и Mood.
4. Пользователь кормит питомца и общается с ним.
5. Питомец меняет состояние, стадию развития и стиль общения.
6. Питомец помнит важные факты из прошлых разговоров.

## 2. Стек

Frontend:

- Next.js
- React
- TypeScript
- Tailwind CSS

Backend:

- Python
- FastAPI
- SQLAlchemy
- Alembic

Database:

- PostgreSQL

LLM:

- OpenAI API

Image generation:

- OpenAI Image API
- Модель задается через `OPENAI_IMAGE_MODEL`
- Kandinsky API не используется в MVP, но архитектура должна позволять заменить image provider позже

## 3. Ключевое архитектурное решение

Frontend должен быть тонким клиентом.

На frontend находятся только:

- экраны;
- формы;
- отображение данных;
- вызовы backend API;
- локальное хранение anonymous `user_id`.

На backend находятся:

- OpenAI API key;
- генерация изображений;
- chat completion;
- игровая логика;
- пересчет Hunger и Mood;
- эволюция;
- память;
- работа с PostgreSQL;
- хранение ссылок на изображения.

API-ключ OpenAI никогда не должен попадать во frontend, клиентский bundle, логи, UI или git.

## 4. Экраны приложения

Приложение состоит из трех экранов.

### 4.1. Создание питомца

Route:

```txt
/
```

Содержимое:

- поле ввода описания персонажа;
- кнопка `Create`;
- состояние загрузки во время генерации;
- отображение ошибки, если генерация не удалась.

Пользовательский сценарий:

1. Пользователь вводит описание.
2. Нажимает `Create`.
3. Frontend отправляет запрос `POST /pets`.
4. Backend создает запись питомца со статусом `generating`.
5. Backend запускает генерацию изображений в фоне.
6. Frontend переходит на `/pet/{pet_id}` или опрашивает статус.
7. Когда питомец готов, отображается главный экран.

Пример пользовательского prompt:

```txt
Маленький добрый дракон с листьями вместо крыльев.
```

Ограничения:

- prompt не должен быть пустым;
- максимальная длина prompt - 300 символов;
- известные персонажи и бренды должны мягко переписываться в безопасные описания без прямого копирования IP.

### 4.2. Главный экран

Route:

```txt
/pet/[id]
```

Содержимое:

- крупное изображение питомца;
- текущая стадия: `Baby`, `Teen`, `Adult`;
- уровень `Hunger`;
- уровень `Mood`;
- кнопка `Feed`;
- кнопка `Chat`.

Дизайн:

- максимально простой;
- без сложной графики;
- без декоративных элементов;
- без анимаций;
- стандартные кнопки, progress bars и layout.

### 4.3. Чат

Route:

```txt
/pet/[id]/chat
```

Содержимое:

- список сообщений;
- поле ввода;
- кнопка отправки;
- ссылка назад на главный экран.

Поведение:

- пользователь пишет сообщение;
- backend формирует контекст питомца;
- LLM отвечает от лица питомца;
- Mood увеличивается после успешного ответа;
- важные факты из сообщения пользователя сохраняются в долговременную память.

## 5. Игровые характеристики

У питомца есть две основные характеристики:

```txt
Hunger: 0..100
Mood:   0..100
```

При создании:

```txt
Hunger = 80
Mood = 80
```

Обе характеристики постепенно уменьшаются со временем.

Пересчет выполняется на backend при каждом действии с питомцем:

- `GET /pets/{pet_id}`;
- `POST /pets/{pet_id}/feed`;
- `POST /pets/{pet_id}/chat`.

Базовая формула:

```txt
elapsed_minutes = now - last_tick_at
hunger = max(0, hunger - elapsed_minutes * HUNGER_DECAY_PER_MIN)
mood = max(0, mood - elapsed_minutes * MOOD_DECAY_PER_MIN)
last_tick_at = now
```

Рекомендуемые dev-значения:

```txt
HUNGER_DECAY_PER_MIN = 0.25
MOOD_DECAY_PER_MIN = 0.15
```

Для MVP эти значения можно вынести в env.

## 6. Действия пользователя

### 6.1. Feed

Endpoint:

```txt
POST /pets/{pet_id}/feed
```

Поведение:

```txt
hunger = min(100, hunger + 25)
```

После действия backend возвращает обновленное состояние питомца.

### 6.2. Chat

Endpoint:

```txt
POST /pets/{pet_id}/chat
```

Поведение:

1. Backend пересчитывает Hunger и Mood.
2. Backend сохраняет сообщение пользователя.
3. Backend собирает LLM context.
4. OpenAI возвращает ответ питомца и список memories для сохранения.
5. Backend сохраняет ответ питомца.
6. Backend сохраняет важные memories.
7. Backend увеличивает Mood.

```txt
mood = min(100, mood + 10)
```

## 7. Состояния изображения

Для каждой стадии развития есть четыре состояния:

- `Idle`;
- `Happy`;
- `Sad`;
- `Hungry`.

Отображаемое состояние выбирается автоматически:

```txt
if hunger < 30:
    state = "hungry"
elif mood < 30:
    state = "sad"
elif hunger > 70 and mood > 70:
    state = "happy"
else:
    state = "idle"
```

Приоритет Hungry выше Sad, потому что голод является более критичным состоянием.

## 8. Эволюция

Стадии:

```txt
Baby -> Teen -> Adult
```

Стадия зависит от времени жизни питомца.

Рекомендуемая логика:

```txt
age_hours = now - pet.created_at

if age_hours < BABY_DURATION_HOURS:
    stage = "baby"
elif age_hours < BABY_DURATION_HOURS + TEEN_DURATION_HOURS:
    stage = "teen"
else:
    stage = "adult"
```

Рекомендуемые env-настройки:

```env
BABY_DURATION_HOURS=24
TEEN_DURATION_HOURS=72
```

Для разработки можно поставить короткие значения, например:

```env
BABY_DURATION_HOURS=0.05
TEEN_DURATION_HOURS=0.1
```

При смене стадии:

- меняется набор изображений;
- меняется стиль общения в чате.

## 9. Генерация изображений

Питомец имеет 3 стадии и 4 состояния.

Итого нужно 12 изображений:

```txt
3 stages * 4 states = 12 images
```

Стадии:

- Baby;
- Teen;
- Adult.

Состояния:

- Idle;
- Happy;
- Sad;
- Hungry.

### 9.1. Основной подход MVP

Для MVP рекомендуется генерировать один sprite sheet:

```txt
4 columns x 3 rows
```

Columns:

```txt
Idle | Happy | Sad | Hungry
```

Rows:

```txt
Baby
Teen
Adult
```

После генерации backend программно нарезает sprite sheet на 12 PNG-файлов.

Причины:

- выше консистентность персонажа;
- дешевле, чем 12 отдельных генераций;
- быстрее;
- проще контролировать единый стиль;
- проще сохранять пропорции, цвета и аксессуары.

### 9.2. Альтернативный подход после MVP

Если качества sprite sheet будет недостаточно, можно перейти на схему:

1. Сгенерировать базовый reference image.
2. Для каждого состояния делать image edit с `input_fidelity=high`.
3. Генерировать 12 отдельных изображений на основе reference.

Этот подход дороже и сложнее, но может дать лучшее качество отдельных кадров.

## 10. Prompt strategy

В новом проекте prompt helpers сохранены здесь:

```txt
backend/app/prompts/pet_image_prompts.py
```

Файл содержит:

- `STYLE_FRAME`;
- `rewrite_known_character_references`;
- `build_character_bible_prompt`;
- `build_pet_sprite_sheet_prompt`.

### 10.1. Style frame

Текущая стилистическая рамка взята из предыдущего проекта:

```txt
Create a cute stylized 3D mascot character for a virtual pet mobile application.

The overall visual style should resemble a polished, iconic, premium family-friendly console game aesthetic: soft, charming, highly stylized, colorful, timeless, and collectible. The character should look like it belongs in a high-quality first-party game universe, with a toy-like mascot appearance.

The rendering should be clean stylized 3D with smooth geometry, rounded forms, matte materials, subtle gradients, soft ambient lighting, and minimal texture detail. Avoid realism. The character should feel handcrafted, polished, and instantly recognizable.
Lighting and gradients must be applied only to the character surface, never to a background or surrounding halo.

The visual style should prioritize simplicity, readability, and a strong silhouette over realism or excessive detail.

Maintain a consistent visual language across every generation so that every character feels like it belongs to the same game universe, regardless of the user's concept.

Do not imitate or reference any specific existing character, franchise, studio, brand, or game.
```

### 10.2. Character bible

Перед генерацией sprite sheet backend должен создать character bible.

Назначение:

- структурировать описание пользователя;
- зафиксировать основные признаки персонажа;
- повысить консистентность изображений;
- отделить пользовательский prompt от финального image prompt.

Ожидаемый JSON:

```json
{
  "species": "small dragon",
  "personality": "kind, curious, gentle",
  "main_colors": ["soft green", "warm yellow"],
  "signature_features": ["leaf-like wings", "round face", "tiny horns"],
  "materials": ["soft toy-like skin", "leaf texture wings"],
  "proportions": "large head, small body, short legs",
  "baby_design": "smaller and rounder version with tiny leaf wings",
  "teen_design": "slightly taller, energetic version with clearer wing shape",
  "adult_design": "fully developed version with confident posture",
  "do_not_change": ["leaf wings", "green/yellow palette", "friendly expression"]
}
```

### 10.3. Sprite sheet prompt

Финальный image prompt должен включать:

- `STYLE_FRAME`;
- безопасно переписанный пользовательский prompt;
- `character_bible`;
- описание сетки;
- правила консистентности;
- output requirements.

Ключевые требования:

- один персонаж во всех ячейках;
- одинаковые вид, цвета, аксессуары, силуэт и материалы;
- меняются только возраст, поза, выражение и эмоциональное состояние;
- прозрачный фон;
- без текста;
- без UI;
- без логотипов;
- без watermark;
- без scene background;
- ровная сетка 4 x 3.

## 11. OpenAI configuration

Секреты хранятся в:

```txt
backend/.env
```

Файл уже добавлен в `.gitignore`.

Пример:

```env
OPENAI_API_KEY=
OPENAI_CHAT_MODEL=gpt-5.5
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_QUALITY=medium
```

Для локальной настройки:

```bash
cd backend
cp .env.example .env
```

После этого нужно заполнить:

```env
OPENAI_API_KEY=sk-...
```

Важно:

- не выводить ключ в логи;
- не отправлять ключ на frontend;
- не коммитить `.env`;
- при ошибке отсутствующего ключа возвращать безопасное сообщение без значения ключа.

## 12. Хранение изображений

Для локального MVP изображения можно хранить на файловой системе backend:

```txt
backend/static/generated/{pet_id}/
  sprite-sheet.png
  baby-idle.png
  baby-happy.png
  baby-sad.png
  baby-hungry.png
  teen-idle.png
  teen-happy.png
  teen-sad.png
  teen-hungry.png
  adult-idle.png
  adult-happy.png
  adult-sad.png
  adult-hungry.png
```

FastAPI должен раздавать static files:

```txt
/static/generated/{pet_id}/{file_name}
```

Для production позже заменить на:

- S3;
- Cloudflare R2;
- Supabase Storage;
- Vercel Blob;
- другой object storage.

## 13. Статусы питомца

У питомца должен быть статус генерации:

```txt
generating
ready
failed
```

Поведение:

- `generating` - запись создана, изображения еще генерируются;
- `ready` - все 12 изображений готовы;
- `failed` - генерация не удалась.

Frontend должен уметь показывать:

- loading state;
- ready state;
- error state.

## 14. Database schema

### 14.1. users

```txt
id UUID primary key
created_at timestamp
```

Для MVP используется anonymous user.

Frontend при первом запуске вызывает:

```txt
POST /users/anonymous
```

и сохраняет `user_id` в `localStorage`.

### 14.2. pets

```txt
id UUID primary key
user_id UUID foreign key users.id
original_description text
character_profile_json jsonb
current_stage text
hunger int
mood int
status text
created_at timestamp
last_tick_at timestamp
generation_error text nullable
```

`current_stage`:

```txt
baby
teen
adult
```

`status`:

```txt
generating
ready
failed
```

### 14.3. pet_images

```txt
id UUID primary key
pet_id UUID foreign key pets.id
stage text
state text
image_url text
generation_prompt text
created_at timestamp
```

`stage`:

```txt
baby
teen
adult
```

`state`:

```txt
idle
happy
sad
hungry
```

Unique constraint:

```txt
unique(pet_id, stage, state)
```

### 14.4. messages

```txt
id UUID primary key
pet_id UUID foreign key pets.id
role text
content text
created_at timestamp
```

`role`:

```txt
user
assistant
```

### 14.5. memories

```txt
id UUID primary key
pet_id UUID foreign key pets.id
fact text
importance float
source_message_id UUID nullable
created_at timestamp
last_referenced_at timestamp nullable
```

Назначение:

- хранить важные факты из прошлых разговоров;
- использовать их в будущих chat prompts.

## 15. Backend API

Base URL для локальной разработки:

```txt
http://localhost:8000
```

### 15.1. Create anonymous user

```txt
POST /users/anonymous
```

Response:

```json
{
  "id": "uuid",
  "created_at": "2026-06-30T12:00:00Z"
}
```

### 15.2. Create pet

```txt
POST /pets
```

Request:

```json
{
  "user_id": "uuid",
  "description": "Маленький добрый дракон с листьями вместо крыльев."
}
```

Response:

```json
{
  "id": "uuid",
  "status": "generating"
}
```

Backend behavior:

1. Validate prompt.
2. Create pet row.
3. Start background generation.
4. Return `pet_id` immediately.

### 15.3. Get pet

```txt
GET /pets/{pet_id}
```

Response:

```json
{
  "id": "uuid",
  "status": "ready",
  "current_stage": "baby",
  "current_state": "idle",
  "hunger": 80,
  "mood": 75,
  "image_url": "/static/generated/{pet_id}/baby-idle.png",
  "images": [
    {
      "stage": "baby",
      "state": "idle",
      "image_url": "/static/generated/{pet_id}/baby-idle.png"
    }
  ],
  "created_at": "2026-06-30T12:00:00Z"
}
```

Backend behavior:

1. Recalculate Hunger and Mood.
2. Recalculate stage.
3. Select current visual state.
4. Return current image URL.

### 15.4. Feed pet

```txt
POST /pets/{pet_id}/feed
```

Response:

```json
{
  "id": "uuid",
  "hunger": 100,
  "mood": 75,
  "current_stage": "baby",
  "current_state": "happy",
  "image_url": "/static/generated/{pet_id}/baby-happy.png"
}
```

### 15.5. List messages

```txt
GET /pets/{pet_id}/messages
```

Response:

```json
{
  "messages": [
    {
      "id": "uuid",
      "role": "user",
      "content": "У меня завтра экзамен",
      "created_at": "2026-06-30T12:00:00Z"
    }
  ]
}
```

### 15.6. Chat

```txt
POST /pets/{pet_id}/chat
```

Request:

```json
{
  "message": "У меня завтра экзамен"
}
```

Response:

```json
{
  "reply": "Я буду держать за тебя кулачки! Потом расскажешь, как все прошло?",
  "mood": 85,
  "hunger": 70,
  "current_stage": "baby",
  "current_state": "happy",
  "image_url": "/static/generated/{pet_id}/baby-happy.png"
}
```

## 16. Chat LLM behavior

LLM должна учитывать:

- возраст питомца;
- Hunger;
- Mood;
- характер питомца;
- историю сообщений;
- долговременную память.

### 16.1. Stage-specific tone

Baby:

- короткие простые фразы;
- наивность;
- больше эмоций;
- может говорить чуть детски.

Teen:

- более энергичный тон;
- больше любопытства;
- больше реакций;
- легкий юмор.

Adult:

- более спокойный;
- заботливый;
- лучше связывает факты из памяти;
- может мягко поддерживать пользователя.

### 16.2. Hunger and Mood influence

Если Hunger низкий:

- питомец может ненавязчиво упоминать, что проголодался;
- не должен полностью игнорировать сообщение пользователя.

Если Mood низкий:

- питомец отвечает менее энергично;
- может просить внимания;
- не должен быть токсичным или обвиняющим.

Если Hunger и Mood высокие:

- питомец более радостный и активный.

### 16.3. Memory extraction

LLM response для backend должен быть структурированным:

```json
{
  "reply": "текст ответа питомца",
  "memories_to_save": [
    {
      "fact": "У пользователя завтра экзамен",
      "importance": 0.8
    }
  ]
}
```

Сохранять нужно только важные факты:

- планы;
- события;
- предпочтения;
- отношения;
- цели;
- важные переживания;
- факты, к которым уместно вернуться позже.

Не сохранять:

- случайный small talk;
- одноразовые команды;
- неважные фразы;
- слишком чувствительные данные без явной пользы для общения.

## 17. Frontend structure

Рекомендуемая структура:

```txt
frontend/
  src/
    app/
      page.tsx
      pet/
        [id]/
          page.tsx
          chat/
            page.tsx
    components/
      PetImage.tsx
      StatBar.tsx
      ChatView.tsx
      CreatePetForm.tsx
    lib/
      api.ts
      types.ts
```

### 17.1. Frontend env

```txt
frontend/.env.local
```

Example:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 17.2. UI requirements

General:

- desktop-first;
- mobile version is not required;
- simple layout;
- standard controls;
- no complex design system required.

Create screen:

- textarea;
- character counter;
- create button;
- loading state;
- error message.

Main screen:

- image;
- Hunger bar;
- Mood bar;
- Feed button;
- Chat button.

Chat screen:

- messages;
- input;
- send button;
- loading state while waiting for reply.

## 18. Backend structure

Рекомендуемая структура:

```txt
backend/
  app/
    main.py
    config.py
    db.py
    models.py
    schemas.py
    services/
      game_service.py
      pet_service.py
      image_service.py
      chat_service.py
      memory_service.py
    prompts/
      pet_image_prompts.py
      chat_prompts.py
    routers/
      users.py
      pets.py
      chat.py
  alembic/
  static/
    generated/
  .env
  .env.example
  pyproject.toml
```

## 19. Image generation service

Responsibilities:

- build character bible prompt;
- call OpenAI chat/text model for character bible;
- build sprite sheet prompt;
- call OpenAI Image API;
- save original sprite sheet;
- crop sprite sheet into 12 images;
- create `pet_images` records;
- update pet status.

Pseudo-flow:

```txt
generate_pet_assets(pet_id):
    pet = load pet
    character_bible = create_character_bible(pet.original_description)
    save character_bible to pet.character_profile_json

    sprite_sheet_prompt = build_pet_sprite_sheet_prompt(
        pet.original_description,
        character_bible
    )

    sprite_sheet = openai.images.generate(...)
    save sprite-sheet.png

    crop into 12 files
    save pet_images rows

    pet.status = "ready"
```

Failure behavior:

```txt
pet.status = "failed"
pet.generation_error = safe internal error code
```

Do not save raw exception text if it may contain sensitive provider details.

## 20. Error handling

Public error codes:

```txt
EMPTY_PROMPT
PROMPT_TOO_LONG
MISSING_OPENAI_API_KEY
PET_NOT_FOUND
PET_NOT_READY
GENERATION_FAILED
IMAGE_SAVE_FAILED
CHAT_FAILED
DATABASE_ERROR
```

Public messages should be safe and user-readable.

Examples:

```txt
Опишите персонажа перед генерацией.
Описание слишком длинное. Сократите его до 300 символов.
На сервере не настроен OpenAI API key.
Не удалось сгенерировать персонажа. Попробуйте еще раз.
Питомец еще создается. Подождите немного.
```

## 21. Local development

### 21.1. PostgreSQL

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: tamagotchi
      POSTGRES_PASSWORD: tamagotchi
      POSTGRES_DB: tamagotchi
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### 21.2. Backend commands

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### 21.3. Frontend commands

```bash
cd frontend
npm install
npm run dev
```

Frontend:

```txt
http://localhost:3000
```

Backend:

```txt
http://localhost:8000
```

## 22. Tests

Минимальные backend tests:

- prompt validation;
- known character rewrite;
- Hunger/Mood decay;
- Feed increases Hunger;
- stage transition;
- image state selection;
- memory extraction parsing;
- pet creation status flow.

Минимальные frontend checks:

- create form validation;
- loading state;
- main screen renders pet stats;
- Feed button calls API;
- chat sends message and renders reply.

## 23. Что не реализовывать в MVP

Не делать:

- магазин;
- предметы;
- одежду;
- кастомизацию после создания;
- достижения;
- ежедневные задания;
- мини-игры;
- здоровье;
- энергию;
- смерть питомца;
- push-уведомления;
- мобильную версию;
- сложную архитектуру;
- auth через email/social login;
- оплату;
- production object storage;
- очереди Celery/RQ;
- админку.

## 24. Критерии готовности MVP

MVP считается готовым, если:

1. Пользователь может открыть приложение.
2. Пользователь может создать anonymous user.
3. Пользователь может ввести описание питомца.
4. Backend создает питомца в PostgreSQL.
5. Backend генерирует character bible.
6. Backend генерирует sprite sheet через OpenAI Image API.
7. Backend нарезает sprite sheet на 12 изображений.
8. Backend сохраняет ссылки на изображения в `pet_images`.
9. Frontend показывает текущую картинку питомца.
10. Hunger и Mood уменьшаются со временем.
11. Feed увеличивает Hunger.
12. Chat увеличивает Mood.
13. Chat отвечает с учетом стадии, Hunger, Mood, характера, истории и memory.
14. Важные факты сохраняются в долговременную память.
15. При низком Hunger отображается Hungry image.
16. При низком Mood отображается Sad image.
17. При высоких Hunger и Mood отображается Happy image.
18. Иначе отображается Idle image.
19. Стадия меняется Baby -> Teen -> Adult.
20. `.env` не коммитится и API key не попадает во frontend.

## 25. Приоритет реализации

### Phase 1: Skeleton

1. Создать backend FastAPI app.
2. Создать frontend Next.js app.
3. Поднять PostgreSQL через Docker.
4. Настроить env.
5. Настроить CORS.

### Phase 2: Database and game state

1. Описать SQLAlchemy models.
2. Создать Alembic migrations.
3. Реализовать anonymous users.
4. Реализовать pets CRUD.
5. Реализовать Hunger/Mood decay.
6. Реализовать Feed.
7. Реализовать stage calculation.
8. Реализовать image state selection.

### Phase 3: Image generation

1. Подключить OpenAI SDK.
2. Реализовать character bible generation.
3. Реализовать sprite sheet generation.
4. Реализовать crop 4 x 3.
5. Реализовать static image serving.
6. Сохранять 12 image records.

### Phase 4: Frontend screens

1. Create pet screen.
2. Polling generation status.
3. Main pet screen.
4. Feed action.
5. Chat screen.

### Phase 5: Chat and memory

1. Messages table.
2. Memories table.
3. Chat prompt.
4. Structured LLM response.
5. Memory save.
6. Mood increase.

### Phase 6: Verification

1. Проверить полный сценарий create -> ready -> feed -> chat.
2. Проверить reload страницы.
3. Проверить отсутствие API key в frontend.
4. Проверить `.gitignore`.
5. Добавить минимальные tests.

## 26. Открытые вопросы

1. Нужно ли показывать пользователю прогресс генерации детальнее, чем `generating`?
2. Нужно ли давать пользователю возможность удалить питомца?
3. Нужно ли ограничивать количество созданных питомцев в локальном MVP?
4. Нужно ли делать retry генерации из failed state?
5. Нужно ли хранить raw sprite sheet prompt в базе для дебага?

Для MVP ответы по умолчанию:

- detailed progress не нужен;
- delete не нужен;
- лимиты не нужны;
- retry можно добавить, если быстро;
- generation prompt сохранять можно, но без секретов.

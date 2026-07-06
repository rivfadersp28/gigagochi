# ТЗ: интеграция memory-механик Shelldon в Lite-режим

Дата: 2026-07-06

Локальная копия референса: `shelldon-reference/`

Источник: `https://github.com/elliotboney/shelldon.git`

Зафиксированный commit локальной копии: `fa87e3a377d19d776d718e6fec902596431d3b6b`

## 1. Цель

Вернуть персонажам память, не откатываясь к старой тяжелой реализации.

Lite остается основным режимом разговора: короткая ролевая рамка, органичные ответы,
минимум постоянных правил. Поверх Lite добавляется отдельная memory-система,
которая умеет:

- запоминать устойчивые факты о пользователе;
- вытаскивать релевантные факты в будущих диалогах;
- консолидировать сырые наблюдения в долговременную память;
- раз в день инициировать персональное обращение к пользователю;
- в MVP работать локально через `localStorage`;
- позже перейти на backend-хранилище и настоящий Telegram push.

Ключевой пример:

Пользователь пишет: `У меня завтра экзамен`.

Система должна сохранить факт с датой. На следующий день персонаж может сам
вспомнить это и написать: `Ну что, как прошел экзамен? Я с утра про него думал.`

## 2. Что берем из Shelldon

Берем не код приложения целиком, а пять механик.

### 2.1. Hybrid memory

В Shelldon память разделена на:

- журнал сообщений в SQLite с поиском;
- curated memory в markdown-файлах;
- pending learnings для временных наблюдений;
- summary для сжатого контекста.

Для нашего MVP:

- SQLite и markdown не берем;
- все храним в `localStorage`;
- структуру оставляем похожей: `chat history`, `learnings`, `memories`, `summary`,
  `userProfile`.

### 2.2. Релевантная память вместо полного промпта

Shelldon не обязан каждый раз подмешивать всю память. Он собирает prompt из:

- persona;
- короткой summary;
- recent window;
- recall;
- curated facts.

Для нас:

- Lite prompt остается коротким;
- перед запросом в модель frontend/backend выбирает 3-5 релевантных фактов;
- память передается отдельным компактным блоком `memoryContext`;
- все факты пользователя не отправляются в каждый запрос.

### 2.3. Dreams / consolidation

В Shelldon `dream` - это фоновая рефлексия без пользовательского сообщения.
Она берет `pending learnings`, решает, что сохранить, что выбросить, и обновляет
summary/persona.

Для нас:

- `dream` называем `memory consolidation`;
- запускаем не обязательно ночью, а по простому триггеру;
- в MVP триггер: не чаще одного раза в день при открытии приложения или после
  очередного диалога;
- consolidation продвигает `pending` learnings в `memories` или помечает их
  `pruned`.

### 2.4. Закрытые операции памяти

В Shelldon модель возвращает фиксированные memory ops:

- `remember`;
- `capture_learning`;
- `resolve_learning`;
- `rewrite_summary`;
- `rewrite_user`;
- `rewrite_soul`;
- `rewrite_identity`.

Для нас:

- fenced `ops` внутри текста ответа не используем;
- память обновляем через structured JSON от отдельного extractor/consolidator;
- набор операций фиксированный и валидируется на клиенте.

### 2.5. Proactive check-in

В Shelldon есть self-initiated turns: питомец может говорить сам, с бюджетом,
cooldown и quiet hours.

Для нас:

- MVP: раз в день при первом открытии чата/главного экрана персонаж может написать
  персональную реплику на основе памяти;
- production: backend scheduler отправляет Telegram-сообщение пользователю сам,
  даже если TMA не открыт.

### 2.6. Persona contract из BOT_INSTRUCTIONS/SOUL

Из Shelldon берем не конкретного персонажа `shelldon`, а хороший behavioral
contract:

- отвечать владельцу естественно, кратко и своим голосом;
- всегда сначала давать живой ответ пользователю, а не начинать с служебных
  операций памяти;
- писать простым естественным языком;
- не добавлять generic robotic sound effects вроде `beep boop`, `whirr`,
  `Beep!`;
- не добавлять `*stage directions*`, если пользователь явно не просил
  roleplay-формат;
- позволять настроению окрашивать ответ;
- персонаж не должен быть blank assistant: у него есть свои вкусы, реакции,
  маленькая самость и право меняться со временем;
- персонаж постепенно узнает пользователя и себя, оставляя то, что стало
  устойчивым, и отпуская то, что больше не подходит.

Адаптация для нашего проекта:

- `THOUGHT:` не должен попадать в видимый текст ответа. Если нужен внутренний
  короткий thought, возвращать его отдельным скрытым полем `innerThought`.
- `FACE:` не должен попадать в видимый текст ответа. Если нужен эмоциональный
  face hint, возвращать его отдельным скрытым полем `faceHint`.
- Fenced ```ops blocks не используем в видимом ответе. Memory ops идут только
  через structured JSON от extractor/consolidator.
- `read_file`, `list_dir`, `python_eval`, `propose_tool` не переносим в chat
  runtime. Для нас релевантны только безопасные app-tools: чтение
  `characterBible`/`liteOverlay`, получение текущего времени и memory
  extraction/consolidation.
- Self-knowledge files Shelldon мапятся не на markdown-файлы, а на наши структуры:
  `characterBible`, `characterBible.extensions.lite_overlay`, `userProfile`,
  `summary`, `memories`.
- Переписывать self-knowledge лучше во время consolidation/dreams, а не на каждой
  реплике.

## 3. Важное ограничение localStorage

`localStorage` не может запустить код, когда пользователь не открыл приложение.

Поэтому в MVP невозможен настоящий push в Telegram "самостоятельно раз в день".
В MVP делаем имитацию правильного поведения:

1. Пользователь открывает приложение или чат.
2. Frontend проверяет `lastProactiveAt`.
3. Если сегодня еще не было proactive-реплики, выбирает актуальную память.
4. Персонаж сам добавляет первое сообщение в чат.

Настоящий Telegram push выносится во вторую фазу:

- память переносится или дублируется на backend;
- backend хранит `telegram_user_id`;
- scheduler раз в день выбирает кандидата;
- backend вызывает Telegram Bot API и отправляет сообщение.

## 4. Термины

| Термин | Определение |
| --- | --- |
| `Learning` | Сырое наблюдение из диалога. Еще не долговременная память. |
| `Memory` | Долговременный факт, который можно использовать в будущих ответах. |
| `Dream / consolidation` | Периодическая обработка `pending` learnings: promote/prune/merge. |
| `Recall` | Выбор релевантных memories под текущую реплику. |
| `Proactive` | Самостоятельная реплика персонажа без нового сообщения пользователя. |
| `User profile` | Сжатое описание пользователя и его предпочтений. |
| `Lite overlay` | Текущий слой фактов о персонаже/мире в `characterBible`. Не равен памяти пользователя. |

## 5. Объем MVP

### Входит

- Добавить localStorage-память пользователя.
- Добавить extractor после каждого ответа персонажа.
- Сохранять сырые `learnings`.
- Делать lightweight promotion важных фактов сразу, если они очевидно устойчивые.
- Добавить daily consolidation, не чаще одного раза в день.
- Добавить recall перед Lite-ответом.
- Передавать в `/api/chat` компактный `memoryContext`.
- Добавить daily proactive message при первом открытии/чате за день.
- Добавить debug-вывод memory context в браузерные логи, если включен debug.
- Покрыть нормализацию и merge памяти тестами.

### Не входит в MVP

- Backend DB для памяти.
- Настоящий Telegram push без открытого приложения.
- Векторная база.
- Embeddings.
- UI ручного редактирования памяти.
- Сложная privacy-модель.
- Графовая память.
- Перенос Shelldon actor bus / fork worker / hardware runtime.
- Возврат старого full prompt engine.

## 6. Что именно запоминаем

Запоминать нужно факты, которые могут сделать будущий диалог личным.

### Категории памяти

```ts
type UserMemoryKind =
  | "user_fact"
  | "preference"
  | "event"
  | "deadline"
  | "relationship"
  | "routine"
  | "goal"
  | "promise"
  | "emotion"
  | "boundary";
```

Примеры:

- `deadline`: "У пользователя завтра экзамен по математике."
- `preference`: "Пользователь любит короткие ответы."
- `relationship`: "У пользователя есть сестра Аня."
- `routine`: "Пользователь часто занимается вечером."
- `goal`: "Пользователь хочет научиться рисовать."
- `boundary`: "Пользователь не хочет, чтобы его называли по имени."

### Что не запоминаем

- Одноразовые команды интерфейса.
- Технические ошибки.
- Случайные фразы без будущей ценности.
- Секреты, токены, пароли.
- Медицинские/финансовые/юридические выводы как факт без явного подтверждения.
- Агрессивные или интимные детали, если они не нужны для безопасного общения.

## 7. localStorage data model

Новый ключ:

```ts
const PET_MEMORY_STORAGE_KEY = `tamagochi:v1:pet-memory:${petId}`;
```

Если в приложении одновременно поддерживается только один питомец, все равно
используем `petId` в ключе, чтобы не закладывать миграционный долг.

### Типы

```ts
type LocalPetMemoryStateV1 = {
  version: 1;
  petId: string;
  createdAt: string;
  updatedAt: string;
  lastExtractionAt?: string;
  lastConsolidationAt?: string;
  lastProactiveAt?: string;
  userProfile?: string;
  summary?: string;
  learnings: LocalPetLearning[];
  memories: LocalPetUserMemory[];
  proactiveLog: LocalPetProactiveLogItem[];
};

type LocalPetLearningStatus = "pending" | "promoted" | "pruned";

type LocalPetLearning = {
  id: string;
  status: LocalPetLearningStatus;
  observation: string;
  patternKey?: string;
  kind?: UserMemoryKind;
  confidence: number;
  importance: number;
  recurrenceCount: number;
  firstSeenAt: string;
  lastSeenAt: string;
  sourceMessageIds: string[];
  dueAt?: string;
};

type LocalPetUserMemory = {
  id: string;
  kind: UserMemoryKind;
  text: string;
  normalizedKey: string;
  confidence: number;
  importance: number;
  createdAt: string;
  updatedAt: string;
  lastMentionedAt?: string;
  mentionCount: number;
  sourceLearningIds: string[];
  dueAt?: string;
  expiresAt?: string;
  tags: string[];
};

type LocalPetProactiveLogItem = {
  id: string;
  createdAt: string;
  memoryIds: string[];
  text: string;
  deliveredVia: "local_open" | "telegram_push";
};
```

### Ограничения хранения

- `learnings`: максимум 100 штук.
- `memories`: максимум 80 штук.
- `proactiveLog`: максимум 30 штук.
- `text`: максимум 500 символов.
- `observation`: максимум 500 символов.
- `summary`: максимум 1000 символов.
- При переполнении удалять сначала `pruned`, затем старые `pending` с низкой
  importance, затем старые memories с низкой importance и без `dueAt`.

## 8. Memory extractor

Extractor запускается после того, как пользователь уже получил ответ персонажа.
Он не должен задерживать вывод ответа.

Текущий endpoint `/api/chat/lite-facts` можно расширить или добавить новый:

```text
POST /api/chat/memory-extract
```

В MVP можно использовать существующий `extractLocalLiteFacts`, но лучше отделить
память пользователя от `liteOverlay`, потому что `liteOverlay` описывает персонажа
и мир, а memory описывает пользователя.

### Вход

```json
{
  "message": "у меня завтра экзамен",
  "reply": "Ох. Я буду рядом камушком удачи.",
  "history": [],
  "pet": {},
  "nowIso": "2026-07-06T12:00:00+03:00",
  "timezone": "Europe/Moscow",
  "existingMemoryBrief": "..."
}
```

### Выход

```ts
type MemoryExtractionResponse = {
  operations: MemoryOperation[];
  debug?: {
    promptDebug?: ChatPromptDebug[];
  };
};
```

### Операции

```ts
type MemoryOperation =
  | {
      type: "capture_learning";
      observation: string;
      patternKey?: string;
      kind?: UserMemoryKind;
      confidence: number;
      importance: number;
      dueAt?: string;
    }
  | {
      type: "remember_user_fact";
      kind: UserMemoryKind;
      text: string;
      normalizedKey: string;
      confidence: number;
      importance: number;
      dueAt?: string;
      expiresAt?: string;
      tags?: string[];
    };
```

### Правила extractor

- Извлекать только то, что сказал или явно подтвердил пользователь.
- Не превращать догадки персонажа в факты о пользователе.
- Если пользователь говорит "завтра", "через неделю", "в пятницу", нормализовать
  дату через `nowIso` и `timezone`.
- Если факт одноразовый и привязан к дате, ставить `dueAt`.
- Если факт перестанет быть актуален после даты, ставить `expiresAt`.
- Если факт важный и конкретный, можно сразу вернуть `remember_user_fact`.
- Если факт слабый, повторяющийся или требует проверки, вернуть `capture_learning`.

## 9. Применение memory operations на клиенте

Добавить в `frontend/src/lib/localPetStorage.ts` функции:

```ts
readLocalPetMemory(petId: string): LocalPetMemoryStateV1;
writeLocalPetMemory(memory: LocalPetMemoryStateV1): void;
applyMemoryOperations(
  memory: LocalPetMemoryStateV1,
  operations: MemoryOperation[],
  sourceMessageIds: string[],
): LocalPetMemoryStateV1;
```

Правила merge:

- `patternKey` dedupe для `learnings`.
- `normalizedKey` dedupe для `memories`.
- При повторном факте увеличивать `recurrenceCount` или `mentionCount`.
- Более свежий `dueAt` может обновлять старый, если `normalizedKey` тот же.
- Более низкая confidence не должна перетирать более высокую.
- Пустые/слишком длинные/невалидные операции игнорировать.

## 10. Recall перед Lite-ответом

Перед отправкой `/api/chat` frontend выбирает memory context.

Функция:

```ts
buildMemoryContextForMessage(
  memory: LocalPetMemoryStateV1,
  message: string,
  now: Date,
): LocalPetMemoryContext
```

Тип:

```ts
type LocalPetMemoryContext = {
  summary?: string;
  userProfile?: string;
  relevantMemories: {
    id: string;
    kind: UserMemoryKind;
    text: string;
    dueAt?: string;
  }[];
  proactiveCandidate?: {
    memoryIds: string[];
    reason: string;
  };
};
```

Алгоритм MVP без embeddings:

1. Всегда учитывать memories с `dueAt` сегодня или в ближайшие 24 часа.
2. Учитывать memories, где слова пересекаются с текущим сообщением.
3. Учитывать high-importance memories, если давно не упоминались.
4. Исключать expired memories.
5. Вернуть максимум 5 memories.

Backend получает `memoryContext` в `LocalChatRequest`.

В Lite prompt добавлять короткий блок только если есть память:

```text
Ты помнишь о пользователе:
- У него сегодня экзамен.
- Он любит короткие ответы.

Используй это только если уместно. Не пересказывай память списком.
```

## 11. Lite prompt после интеграции памяти

Базовая рамка не меняется:

```text
Отвечай мне как {short_character_description}.
```

К ней добавляется короткий persona-contract, адаптированный из Shelldon:

```text
Отвечай владельцу естественно, кратко и своим голосом.
Сначала всегда скажи что-то живое пользователю.
Пиши простым естественным языком.
Не используй generic robotic sounds вроде beep boop, whirr, Beep!.
Не используй *stage directions*, если пользователь сам не просит roleplay-формат.
У тебя есть свои вкусы, реакции и настроение; они могут мягко окрашивать ответ.
Ты не blank assistant: ты постепенно узнаешь пользователя и себя.
```

Это не должно превращаться в длинную систему запретов. Если поведение уже
получается из описания персонажа и memory/persona state, этот блок можно держать
коротким или частично опускать.

Дополнительный блок памяти должен быть коротким и не превращаться в систему
ограничений.

Пример полного смыслового prompt:

```text
Отвечай мне как Громм, взрослый каменный великан.
Ты сейчас голодный.

Ты помнишь о пользователе:
- У него сегодня экзамен по математике.

Ответь естественно от лица персонажа. До 300 символов.
```

### Скрытые thought/face поля

Shelldon просит модель дописывать `THOUGHT:` и `FACE:` строками после ответа. В
нашем UI это нельзя показывать пользователю как часть сообщения.

Если эту механику добавляем, backend должен возвращать:

```ts
type LocalChatResponse = {
  reply: string;
  moodHint?: PetMood;
  innerThought?: string;
  faceHint?: "happy" | "excited" | "curious" | "content" | "grumpy" | "sleepy";
  debug?: LocalChatDebug;
};
```

Правила:

- `reply` - единственный видимый текст чата;
- `innerThought` можно использовать для debug/logs или будущей экранной подписи;
- `faceHint` можно мапить на настроение/спрайт;
- если модель случайно вернула строки `THOUGHT:` или `FACE:` в `reply`, backend
  должен вырезать их до отправки на frontend;
- `innerThought` максимум 6 слов;
- `faceHint` только из закрытого enum.

Важно:

- память не должна ломать голос персонажа;
- персонаж может вспомнить факт сам, но не обязан упоминать его в каждой реплике;
- если память не релевантна, ее можно игнорировать;
- нельзя говорить "я нашел в памяти", "в memoryContext указано".

## 12. Memory consolidation / dreams

Consolidation запускается:

- при открытии приложения, если `lastConsolidationAt` не сегодня;
- после extractor, если pending learnings накопилось больше 10;
- вручную в debug-режиме, если понадобится.

### Вход consolidator

```json
{
  "pendingLearnings": [],
  "existingMemories": [],
  "summary": "...",
  "userProfile": "...",
  "nowIso": "..."
}
```

### Выход

```ts
type MemoryConsolidationResponse = {
  operations: MemoryConsolidationOperation[];
};

type MemoryConsolidationOperation =
  | {
      type: "promote_learning";
      learningId: string;
      memory: {
        kind: UserMemoryKind;
        text: string;
        normalizedKey: string;
        confidence: number;
        importance: number;
        dueAt?: string;
        expiresAt?: string;
        tags?: string[];
      };
    }
  | {
      type: "prune_learning";
      learningId: string;
      reason?: string;
    }
  | {
      type: "rewrite_summary";
      content: string;
    }
  | {
      type: "rewrite_user_profile";
      content: string;
    };
```

### Правила consolidation

- Durable facts about user -> promote.
- Одноразовый факт после истечения `expiresAt` -> prune или оставить только в summary,
  если он важен для отношений.
- Повторяющиеся слабые learnings -> promote, если recurrenceCount высокий.
- Summary должна быть компактной, не больше 1000 символов.
- User profile не должен становиться биографией; только устойчивые предпочтения,
  стиль общения, важные отношения.

## 13. Daily proactive в MVP

Цель: персонаж раз в день может первым написать что-то персональное.

### Триггер

На frontend:

```ts
maybeCreateDailyProactiveMessage(pet, memory, now)
```

Запускать:

- после загрузки `useLocalPetState`;
- при открытии ChatView;
- не чаще одного раза в локальный календарный день;
- не раньше чем через 6 часов после создания питомца;
- не если в чате уже есть сообщение питомца за последние 30 минут.

### Выбор темы

Приоритет:

1. Memory с `dueAt` сегодня.
2. Memory с `dueAt` завтра.
3. Important memory, которую давно не упоминали.
4. Preference/relationship memory, если нет событий.

Примеры:

- `У пользователя сегодня экзамен.`
- `Пользователь вчера говорил, что волнуется перед собеседованием.`
- `Пользователь любит, когда ответы короткие.`

### Генерация proactive-реплики

Новый endpoint:

```text
POST /api/chat/proactive
```

Вход:

```json
{
  "pet": {},
  "memoryContext": {
    "relevantMemories": [
      {
        "id": "m1",
        "kind": "deadline",
        "text": "У пользователя сегодня экзамен.",
        "dueAt": "2026-07-07T09:00:00+03:00"
      }
    ]
  },
  "nowIso": "2026-07-07T08:00:00+03:00"
}
```

Prompt:

```text
Отвечай мне как {short_character_description}.

Ты сам решил написать пользователю первым.
Повод: у пользователя сегодня экзамен.

Напиши одну живую реплику до 300 символов. Не объясняй, что это напоминание.
```

Результат добавляется в локальную историю чата как сообщение `pet`.

### UX

- Proactive-сообщение выглядит как обычная реплика персонажа.
- Не показывать системный баннер "memory triggered".
- В debug console можно логировать selected memory ids.

## 14. Production Telegram push

Эта фаза нужна, чтобы персонаж писал в Telegram без открытия TMA.

### Требования

- Backend хранит memory state на пользователя/питомца.
- Backend знает Telegram `chat_id` или может отправить через bot API текущему
  пользователю.
- Scheduler запускается минимум раз в час.
- Для каждого пользователя проверяется:
  - был ли proactive сегодня;
  - не quiet hours ли сейчас;
  - есть ли подходящая память;
  - не было ли недавно активного диалога.

### Поведение

Если кандидат найден:

1. Backend генерирует proactive-реплику через Lite model.
2. Отправляет ее через Telegram Bot API.
3. Записывает `proactiveLog`.
4. Обновляет `lastProactiveAt` и `lastMentionedAt` у memories.

### Quiet hours

По умолчанию не писать с 22:00 до 09:00 в timezone пользователя.

Если timezone неизвестен, использовать timezone из Telegram WebApp session или
fallback `Europe/Moscow`.

## 15. Интеграция с текущими файлами

### Frontend

Добавить:

- `frontend/src/lib/localPetMemoryStorage.ts`
- `frontend/src/lib/localPetMemoryRecall.ts`
- `frontend/src/lib/localPetMemoryTypes.ts`

Изменить:

- `frontend/src/lib/types.ts`
  - добавить `LocalPetMemoryContext`;
  - добавить `innerThought` и `faceHint` в `LocalChatResponse`, если включаем
    Shelldon-style hidden reaction fields;
  - добавить response-типы extractor/consolidator/proactive.
- `frontend/src/lib/api.ts`
  - передавать `memoryContext` в `sendLocalChatMessage`;
  - добавить `extractLocalUserMemory`;
  - добавить `consolidateLocalUserMemory`;
  - добавить `generateLocalProactiveMessage`.
- `frontend/src/components/ChatView.tsx`
  - после ответа запускать memory extractor;
  - перед ответом строить recall context;
  - при открытии пытаться создать daily proactive.
- `frontend/src/components/PetQuickChat.tsx`
  - аналогично ChatView, если quick chat остается.
- `frontend/src/lib/useLocalPetState.ts`
  - при reset удалять memory state текущего питомца.

### Backend

Изменить:

- `backend/app/schemas.py`
  - добавить `LocalPetMemoryContext`;
  - добавить скрытые поля `innerThought` и `faceHint` в `LocalChatResponse`;
  - добавить schemas для extractor/consolidator/proactive.
- `backend/app/routers/tma.py`
  - добавить `/api/chat/memory-extract`;
  - добавить `/api/chat/memory-consolidate`;
  - добавить `/api/chat/proactive`.
- `backend/app/services/pet_reply_engine/lite_generator.py`
  - принимать `memoryContext`;
  - добавлять короткий memory block в prompt;
  - добавить адаптированный persona-contract из Shelldon;
  - парсить и вырезать случайно видимые `THOUGHT:`/`FACE:` строки из `reply`;
  - возвращать `innerThought`/`faceHint` отдельными полями, если модель их дала;
  - добавить функции extraction/consolidation/proactive generation.

Не возвращать старые:

- `pet_memory`;
- old memory resolver;
- old full prompt layers.

## 16. Debug и наблюдаемость

Если включен `includePromptDebug`:

- логировать в браузер:
  - selected memories для recall;
  - operations extractor;
  - operations consolidation;
  - proactive candidate;
  - prompt, который ушел в модель.

В response debug можно добавить:

```ts
type MemoryDebug = {
  selectedMemoryIds?: string[];
  extractionOperations?: MemoryOperation[];
  consolidationOperations?: MemoryConsolidationOperation[];
  proactiveReason?: string;
};
```

## 17. Acceptance criteria

### Запоминание события

1. Пользователь пишет: `У меня завтра экзамен`.
2. Ответ появляется сразу, без ожидания extractor.
3. Extractor сохраняет memory или learning с `dueAt`.
4. При следующем открытии на следующий день персонаж сам пишет о событии.

### Релевантный recall

1. Пользователь сказал: `Я люблю короткие ответы`.
2. Через несколько сообщений спрашивает обычный вопрос.
3. В `memoryContext` попадает preference.
4. Ответ становится короче, но персонаж не говорит "я помню, что...".

### Нерелевантная память не мешает

1. В памяти есть факт про экзамен.
2. Пользователь спрашивает про еду персонажа.
3. Память про экзамен не должна обязательно попадать в prompt.

### Consolidation

1. Накопилось несколько pending learnings.
2. Запускается daily consolidation.
3. Важные learnings становятся memories.
4. Слабые learnings получают `pruned`.
5. Summary обновляется и остается короткой.

### Proactive cooldown

1. Proactive уже был сегодня.
2. Пользователь заново открывает приложение.
3. Новое proactive-сообщение не появляется.

## 18. Тесты

### Unit tests frontend

- normalize memory state from broken localStorage.
- apply `capture_learning`.
- dedupe by `patternKey`.
- apply `remember_user_fact`.
- dedupe by `normalizedKey`.
- prune over storage limits.
- recall due memories.
- recall keyword-overlap memories.
- skip expired memories.
- daily proactive respects `lastProactiveAt`.

### Backend tests

- `/api/chat` accepts empty memory context.
- `/api/chat` adds memory block only when memories exist.
- extractor returns valid operations for "завтра экзамен".
- extractor does not save random small talk.
- consolidator promotes important pending learning.
- proactive endpoint returns <= 300 chars.

## 19. Порядок реализации

### Шаг 1. Local storage substrate

- Добавить memory types.
- Добавить read/write/normalize/apply functions.
- Добавить unit tests.

### Шаг 2. Extractor

- Добавить backend schema + endpoint.
- Добавить frontend API wrapper.
- Запускать extractor после ответа.
- Сохранять operations в localStorage.

### Шаг 3. Recall

- Реализовать keyword/dueAt recall.
- Передавать `memoryContext` в chat request.
- Добавить memory block в Lite prompt.

### Шаг 4. Consolidation

- Добавить endpoint.
- Запускать daily при открытии приложения.
- Promote/prune pending learnings.
- Обновлять summary/userProfile.

### Шаг 5. Daily proactive MVP

- Добавить endpoint генерации proactive.
- Добавить frontend trigger.
- Добавлять proactive-реплику в локальную историю.
- Обновлять proactive log.

### Шаг 6. Backend Telegram push

- Спроектировать persisted memory.
- Привязать memory к Telegram user.
- Добавить scheduler.
- Отправлять proactive через Telegram Bot API.

## 20. Решение по Shelldon-копии

Копия репозитория лежит в корне проекта как справочник:

```text
shelldon-reference/
```

Ее не нужно импортировать в runtime и не нужно подключать как пакет.
Используем ее как локальный reference при реализации:

- `shelldon/core/history.py` - модель history/learnings;
- `shelldon/core/memory.py` - curated memory и safe writes;
- `shelldon/worker/prompt.py` - порядок сборки prompt;
- `shelldon/core/proactive.py` - dream/proactive prompt builders;
- `shelldon/core/scheduler.py` - cadence, quiet hours, daily budget;
- `shelldon/persona/*.md` - persona/dream/heartbeat паттерны.

В наш код переносим идеи, а не архитектуру целиком.

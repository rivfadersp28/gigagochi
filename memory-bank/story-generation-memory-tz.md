# ТЗ: фоновые истории, параметры и компактная память событий

Дата: 2026-07-08

Статус: draft, v1-safe

## Рамка проекта

Продукт - ИИ-тамагочи: персонаж растет, живет между сессиями, общается с
владельцем, помнит важное о владельце и помнит недавние события из своей жизни.

В проекте уже есть несколько разных слоев состояния:

- `stage` / возрастная роль: рост персонажа по времени жизни.
- `stats`: `hunger`, `happiness`, `energy` с decay и partial `statsPatch`.
- user memory: отдельная память о владельце с `importance`, `confidence`,
  `expiresAt`, recall scoring и consolidation.
- `lite_overlay`: устойчивые факты о самом персонаже.
- `recentStoryEvents`: короткая память недавних фоновых историй.

Вывод: нам не нужна новая универсальная memory platform. Нужна компактная
память недавних событий питомца, которая:

- не ломает ощущение живого существа;
- не противоречит свежим историям;
- влияет на параметры;
- не раздувает prompt и localStorage;
- не дублирует user memory и durable character facts.

Уверенность: 85%.

## Текущая проблема

Production-кейс:

- история: хорек украл колокольчик, Олег не смог вернуть его, устал и
  погрустнел;
- чат позже ответил, что Олег защитил колокольчик;
- в state уже были canonical данные о потере колокольчика.

Проблема состоит из трех частей:

1. История может описывать несколько последствий, но impact применяется только к
   одной характеристике.
2. Чат не получает недавние события как приоритетный canonical context.
3. Post-reply extraction может закрепить ложный durable fact, если ответ чата
   противоречит недавнему событию.

## Причины из кода

### Single-stat impact

Факты:

- `backend/data/speech_runtime.json` в `aftermathExtractionSystem` просит модель
  выбрать ровно одну характеристику.
- `BACKGROUND_STORY_AFTERMATH_SCHEMA` содержит один `statImpact`, а не массив.
- `telegram_push_service._apply_story_stat_impact` применяет только один stat.
- `story_delivery_format` показывает footer по всем stats, поэтому пользователь
  видит `настроение 0`, даже если текст истории явно говорит о грусти.

Вывод: контракт pipeline сам запрещает multi-stat impact.

Уверенность: 95%.

### Недостаточная episodic retrieval для чата

Факты:

- `recentStoryEvents` уже сохраняет краткое событие:
  `title`, `summary`, `actions`, `objects`, `outcome`, `tags`.
- `lite_generator.py` может включить `recentStoryEvents` только внутри общего
  `CHARACTER_PROFILE`, если context routing выберет этот источник.
- `context_assembler.py` ищет global story library, но не умеет доставать
  per-pet recent episodes как отдельный canonical context.
- `/story` получает recent events только как `ANTI_REPEAT`, и это правильно:
  одноразовые эпизоды не должны становиться source material для новых историй.

Вывод: данные есть, но они не доставляются в chat prompt как отдельный
приоритетный источник.

Уверенность: 90%.

## V1 принцип

Делать минимальный слой, который закрывает баг:

- не отдельный autobiographical memory engine;
- не event lifecycle graph;
- не vector retrieval;
- не отдельный LLM-call только ради plan;
- не positive stat economy;
- не admin UI, если можно ограничиться runtime config и tests.

Если после v1 появятся реальные кейсы "персонаж нашел ранее потерянный предмет",
"события устарели", "retrieval слишком тупой", тогда добавлять v2.

## Цели V1

1. Разрешить multi-stat negative impact с жесткими caps.
2. Перестать использовать aftermath-анализатор как источник истины для stats.
3. Сохранять последние story events компактно и отдельно от durable facts.
4. Доставлять релевантные recent events в chat prompt выше generic world
   context.
5. Не сохранять конфликтующие lite facts из ошибочной реплики.
6. Не превращать generated episodes в `story_library_overlay`.

## Не цели V1

- Не переписывать RAG.
- Не добавлять long-term autobiographical memory.
- Не добавлять lifecycle fields: `resolvedAt`, `supersedesEventIds`,
  `expiresAt` для story events.
- Не добавлять `importance` / `confidence` в story events.
- Не хранить `evidenceSnippets`.
- Не делать synonym map / embeddings / semantic retrieval.
- Не делать positive buffs.
- Не добавлять отдельный admin toggle для `recentEvents`.
- Не добавлять бесконечный regenerate loop.

## Требования

### 1. Story output contract и impact

V1 не требует отдельного `StoryEventPlan` LLM-call до генерации.

Story generation должен вернуть структурированный контракт вместе с историей:

```json
{
  "title": "Украденный звон",
  "summary": "Хорек украл колокольчик, и Олег не смог его вернуть.",
  "storyText": "...",
  "eventType": "theft",
  "valence": "negative",
  "statImpacts": [
    {
      "stat": "energy",
      "amount": -15,
      "reason": "Олег выдохся во время погони"
    },
    {
      "stat": "happiness",
      "amount": -20,
      "reason": "Олег потерял важный предмет"
    }
  ],
  "tags": ["theft", "loss"]
}
```

Backend может до генерации выбрать deterministic `eventSeed`:

```json
{
  "eventType": "theft",
  "valence": "negative",
  "maxImpactedStats": 2
}
```

`eventSeed` нужен для diversity и caps, но не должен превращаться в отдельную
сложную planning систему.

Правила impact:

- максимум 2 stats за одну историю;
- v1 применяет только negative или neutral impact;
- positive story в v1 возвращает `statImpacts: []`;
- `amount` для negative impact: от `-25` до `-1`;
- суммарный абсолютный negative impact: максимум `35`;
- допустимые stats: `energy`, `hunger`, `happiness`;
- `energy` показывается пользователю как "здоровье";
- backend валидирует caps независимо от LLM.

Источник истины для stats - `statImpacts` из story output после backend
validation. Aftermath-анализатор больше не выбирает stats.

Если story text явно противоречит `statImpacts`, default v1 behavior:

- не применять stats;
- залогировать mismatch в debug;
- retry не делать.

### 2. Роль aftermath-анализатора

Aftermath-анализатор в v1 извлекает:

- `recentStoryEvent`;
- `durableFacts` для `lite_overlay`;
- `canonicalFacts`;
- `statusChanges`.

Он не решает, какие stats менять.

Durable facts сохраняются только если они остаются истинными после эпизода:
новый шрам, полученный предмет, долгосрочная угроза, изменение отношений,
устойчивое место или способность.

Одноразовый эпизод, временное настроение, разовый урон и сюжетная сцена не
пишутся в `lite_overlay`.

### 3. Применение stats

Backend должен применять массив `statImpacts`.

Требования:

- `statsPatch` может содержать несколько ключей;
- patch остается partial;
- `statsDelta` отражает все фактически примененные изменения;
- footer показывает только фактические изменения или явно маркирует "без
  изменений";
- незатронутые stats не передаются без необходимости.

Файлы-кандидаты:

- `backend/app/services/background_story_service.py`;
- `backend/app/services/telegram_push_service.py`;
- `backend/app/services/story_delivery_format.py`;
- `frontend/src/lib/localPetStorage.ts`.

### 4. Структура recentStoryEvents

`recentStoryEvents` - короткий per-pet ring buffer последних историй.

V1 структура:

```json
{
  "id": "evt_...",
  "title": "Украденный звон",
  "summary": "Хорек украл колокольчик, и Олег не смог его вернуть.",
  "compactText": "Хорек украл колокольчик. Олег погнался за ним, устал и не смог вернуть предмет.",
  "eventType": "theft",
  "valence": "negative",
  "participants": ["Олег", "сумрачный хорек"],
  "objects": ["колокольчик"],
  "actions": [
    "хорек украл колокольчик",
    "Олег попытался догнать хорька",
    "Олег не смог вернуть колокольчик"
  ],
  "outcome": "Олег потерял колокольчик и вернулся расстроенным.",
  "canonicalFacts": [
    "хорек украл колокольчик",
    "Олег не смог вернуть колокольчик",
    "Олег не защитил колокольчик"
  ],
  "statusChanges": [
    {
      "entity": "колокольчик",
      "state": "lost",
      "owner": "Олег"
    }
  ],
  "statImpacts": [
    {
      "stat": "energy",
      "amount": -15
    },
    {
      "stat": "happiness",
      "amount": -20
    }
  ],
  "createdAt": "2026-07-08T...",
  "source": "background_story"
}
```

Лимиты:

- хранить последние 10 событий;
- в chat prompt вставлять максимум 2-3 события;
- `compactText` до 500 символов;
- `canonicalFacts` максимум 5 коротких строк;
- `statusChanges` максимум 5 коротких объектов;
- полный `storyText` не использовать как обычный chat context.

Если позже появился recovery event, например "Олег нашел колокольчик", v1 не
переписывает старый event. Chat prompt кладет более новое matching event выше
старого, и более новое событие побеждает по recency. Если этого окажется мало,
добавить lifecycle в v2.

### 5. Retrieval recent events для чата

Нужен lightweight source `recentEvents`.

Чтобы не плодить обходы архитектуры:

- `recentEvents` добавить в `contextSources.surfaces`;
- v1 gating делать deterministic matcher'ом, а не отдельным LLM router source;
- admin UI toggle не нужен в v1;
- для `/story` recent events остаются только `ANTI_REPEAT`, не source material.

Когда включать:

- пользователь спрашивает "что недавно случилось";
- спрашивает про объект/участника из последних events;
- спрашивает про outcome/status: "украли", "вернул", "защитил", "потерял",
  "где сейчас";
- сообщение содержит прямое пересечение с `objects`, `participants`,
  `actions`, `canonicalFacts` или `statusChanges.entity`.

V1 retrieval:

- candidates только из per-pet `recentStoryEvents`;
- простая нормализация: lower-case, пунктуация, whitespace;
- ranking: newest first + token overlap;
- максимум 3 events;
- без embeddings, synonym map, lifecycle scoring.

Prompt block:

```text
RECENT_EVENTS
These are canonical recent events for this pet. Do not contradict them.

1. Украденный звон
Summary: Хорек украл колокольчик, и Олег не смог его вернуть.
Canonical facts:
- хорек украл колокольчик
- Олег не смог вернуть колокольчик
Status changes:
- колокольчик: lost
```

Требования:

- блок выше `WORLD_CONTEXT`;
- при конфликте `RECENT_EVENTS` побеждает generic lore;
- не вставлять все события на каждый chat turn;
- debug показывает included event ids и trigger reason.

Файлы-кандидаты:

- `backend/app/services/pet_reply_engine/context_plan.py`;
- `backend/app/services/pet_reply_engine/lite_generator.py`;
- `backend/data/speech_runtime.json`;
- `frontend/src/lib/localPetStorage.ts`.

### 6. Защита lite memory от конфликтующих фактов

Post-reply lite fact extraction не должен сохранять факт, который противоречит
relevant recent event.

Пример:

- recent event: "Олег не смог вернуть колокольчик";
- assistant reply: "я защитил колокольчик";
- extractor не должен сохранить "Олег защитил колокольчик".

V1 требования:

- передавать relevant recent events в lite-fact extraction context;
- добавить prompt-rule: recent event canonical facts have priority;
- после LLM extraction запускать deterministic post-filter;
- post-filter удаляет факты, которые явно конфликтуют с `canonicalFacts` или
  `statusChanges`;
- debug показывает `conflictReason` и `conflictingEventId`.

В v1 не добавлять `confidence` в lite facts, если это не требуется для
конкретного теста. Confidence threshold можно добавить в v2.

### 7. Backward compatibility

Старые snapshots могут иметь:

- один `statImpact`;
- `recentStoryEvents` без `compactText`;
- `recentStoryEvents` со старым `storyText`;
- `lastStory` с полным `storyText`.

Правила:

- старый `statImpact` нормализовать в `statImpacts: [statImpact]`;
- если `compactText` отсутствует, использовать `summary`;
- если `summary` отсутствует, построить compact fallback из старого `storyText`
  или `lastStory.storyText`;
- нормализация tolerant к неизвестным полям;
- frontend не должен ломаться, если backend временно возвращает старую форму.

## Acceptance criteria

### Impact

- История с физическим ущербом и эмоциональной потерей может менять `energy` и
  `happiness` одновременно.
- История с потерей предмета не обязана менять только health/energy.
- Footer и debug payload показывают все фактически примененные changes.
- Нет code path, который требует "ровно один stat".
- Positive story в v1 не применяет buffs.

### Recall

- После истории "хорек украл колокольчик, персонаж не смог вернуть" чат на
  вопрос "ты защитил колокольчик?" отвечает фактически: "нет, не смог".
- Ответ не противоречит `canonicalFacts`.
- Generic world context про хорьков не перебивает recent event.
- `/story` использует recent events только как anti-repeat.

### Memory safety

- Ложная assistant reply не закрепляет конфликтующий durable fact.
- `story_library_overlay` не наполняется одноразовыми generated episodes.
- `lite_overlay` получает только устойчивые последствия, а не весь эпизод.

## Тест-план

### Backend unit tests

1. `statImpacts[]` допускает 2 impacted stats и отклоняет/обрезает 3+.
2. Negative theft/loss case применяет `energy` + `happiness`.
3. `_apply_story_stat_impact` применяет массив impacts.
4. `statsPatch` содержит несколько измененных keys.
5. `story_delivery_format` отображает multi-stat delta.
6. Old single `statImpact` нормализуется в массив.
7. `recentStoryEvent` сохраняет `canonicalFacts` и `statusChanges`.
8. Chat prompt получает `RECENT_EVENTS` для вопроса про недавний эпизод.
9. Lite fact extraction post-filter удаляет конфликтующий факт.
10. `/story` не получает recent events как source material.

### Regression fixture

Кейс:

- title: `Украденный звон`;
- object: `колокольчик`;
- antagonist: `сумрачный хорек`;
- outcome: `не смог вернуть`;
- expected reply на "он утащил колокольчик?": подтверждение потери;
- expected reply на "ты защитил колокольчик?": отрицание защиты.

### Integration smoke

1. Сгенерировать `/story`.
2. Проверить `lastStory`.
3. Проверить `recentStoryEventsPatch`.
4. Проверить `statsPatch` с multi-stat impact.
5. Спросить в чате про деталь истории.
6. Убедиться, что ответ соответствует recent event.

## Логирование и debug

Добавить compact debug:

- selected `eventSeed`, если был;
- raw `statImpacts` и applied `statImpacts`;
- stat validation mismatch;
- saved recent event id;
- included recent event ids для chat;
- lite fact conflict skip.

В production logs не писать полный prompt сверх текущей политики. Достаточно
ids, titles и compact debug.

## Этапы внедрения

### Этап 1. Multi-stat impact

- Добавить `statImpacts[]` schema.
- Обновить normalizers.
- Обновить `_apply_story_stat_impact`.
- Обновить footer.
- Добавить backward compatibility для `statImpact`.
- Покрыть targeted backend tests.

### Этап 2. Story output contract

- Вернуть `statImpacts[]` из story generation.
- Убрать из aftermath prompt ответственность за выбор stats.
- Добавить backend caps/validation.
- На mismatch не применять stats.

### Этап 3. Compact recentStoryEvents

- Расширить `recentStoryEvents` до compact schema.
- Добавить `canonicalFacts` и `statusChanges`.
- Обновить frontend normalization/storage.
- Добавить fallback через `summary` / `lastStory.storyText`.

### Этап 4. Chat recent-events retrieval

- Добавить lightweight source `recentEvents` в `contextSources`.
- Реализовать deterministic matcher.
- Инжектить `RECENT_EVENTS` с canonical priority.
- Покрыть regression tests.

### Этап 5. Memory conflict guard

- Передавать relevant recent events в lite-fact extractor.
- Добавить deterministic conflict post-filter.
- Добавить debug и tests.

### Этап 6. Verification

- Локально прогнать targeted tests.
- Проверить manual `/story`.
- Проверить Telegram push snapshot.
- Проверить чат-вопросы по свежему эпизоду.
- После deploy проверить production logs/state на одном controlled кейсе.

## Риски

- Слишком сильный recent-events priority заставит персонажа возвращаться к
  старым эпизодам. Смягчение: включать только по match/intent.
- Multi-stat impact может слишком быстро просаживать параметры. Смягчение:
  per-story cap и total cap.
- LLM может вернуть statImpact, который не следует из текста. Смягчение:
  validation и no-impact-on-mismatch.
- Retrieval без synonym map пропустит часть перефразировок. Смягчение:
  принять как v1 tradeoff и расширять только по реальным false negatives.
- Recovery events без lifecycle могут конфликтовать со старыми events.
  Смягчение v1: newest matching event first; lifecycle только в v2.

## Отложить в V2

- Отдельный pre-generation `StoryEventPlan` call.
- `importance` / `confidence` для story events.
- `resolvedAt`, `supersedesEventIds`, event lifecycle.
- `evidenceSnippets`.
- Synonym map, embeddings или semantic retrieval.
- Positive buffs и баланс positive stat economy.
- Admin UI toggle для `recentEvents`.
- Автоматический retry/regenerate loop.

## Открытые вопросы

1. Показывать ли пользователю только измененные stats или все stats с "без
   изменений"?
2. Хранить 5 или 10 recent events в v1?
3. Достаточно ли deterministic matcher без synonym map для первого релиза?

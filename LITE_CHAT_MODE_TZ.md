# ТЗ: Lite-режим разговора через голый GPT-5.5

Дата: 2026-07-05

## 1. Цель

Сделать экспериментальный режим общения `Lite`, который максимально похож на
обычный ручной опыт в ChatGPT:

> "Отвечай мне как большой каменный великан"

и дальше модель сама держит роль, отвечает органично, додумывает детали и не
зажата текущими валидаторами, возрастными шаблонами, fallback-репликами и
слоями prompt engineering.

Lite нужен не как замена текущего движка, а как изолированный A/B-режим на
основном экране чата, чтобы сравнить:

- текущий `pet_reply_engine`;
- минимальный режим `Lite` на `gpt-5.5`;
- качество саморефлексии, импровизации и живости ответа.

## 2. Проблема, которую проверяем

Сейчас персонаж часто звучит как результат системы ограничений:

- жесткие лимиты слов, символов и предложений;
- post-generation validator;
- fallback при любом нарушении;
- возрастные speech rules;
- speech anchors и запрет копирования;
- mood/state constraints;
- proactivity gate;
- memory resolver;
- prompt layers.

Гипотеза Lite:

если оставить модели только короткую ролевую рамку и дать ей возможность
самостоятельно обращаться к JSON персонажа по необходимости, ответы станут
ближе к обычному ChatGPT-ролевому диалогу: менее одинаковыми, более
самостоятельными и более естественными.

## 3. Термины

| Термин | Определение |
| --- | --- |
| `Full` | Текущий режим ответа через `pet_reply_engine`. |
| `Lite` | Новый экспериментальный режим: минимальный prompt, без валидаторов и шаблонов. |
| `character JSON` | `characterBible`, текущая память и Lite-overlay, доступные модели через tool. |
| `Lite overlay` | Изолированное место для фактов, которые Lite придумал или уточнил в чате. |
| `raw reply` | Текст модели как есть, без post-processing валидатором. |

## 4. Объем первой итерации

### Входит

- Добавить режим ответа `Lite` для локального TMA-чата `/api/chat`.
- Добавить toggle `Lite` на основной экран разговора с персонажем.
- При включенном `Lite` отправлять запрос в отдельный backend-путь генерации.
- Использовать тот же chat model, что и основной чат: по умолчанию
  `settings.openai_chat_model`, сейчас это `gpt-5.5`.
- Использовать минимальный system prompt:

```text
Отвечай мне как {short_character_description}.
```

- Не использовать текущий `prompt_builder.py`.
- Не использовать `reply_validator.py`.
- Не использовать `fallbacks.py` для содержательной fallback-реплики.
- Не использовать `text_style.py`.
- Использовать age message examples только для стадии `baby`.
- Не использовать speech anchors.
- Не использовать reference cards.
- Не использовать proactivity gate.
- Не использовать memory resolver для Lite-ответа.
- Не требовать от модели JSON-ответа.
- Возвращать пользователю сырой текст ответа модели.
- Дать модели tools для чтения JSON персонажа и записи новых Lite-фактов.
- После Lite-ответа запускать фоновый анализ ответа на новые устойчивые факты.
- Хранить Lite-факты отдельно от текущей canonical memory, чтобы эксперимент не
  ломал основной движок.

### Не входит

- Полная замена текущего `pet_reply_engine`.
- Миграция существующей памяти.
- UI редактирования Lite-фактов.
- Векторный поиск.
- Автоматическое слияние Lite-фактов в канон.
- Обязательная поддержка persisted route `/pets/{id}/chat` в первой итерации.
- Переписывание генерации персонажа и изображений.

## 5. Главное правило Lite

Lite не должен быть еще одним набором правил поверх персонажа.

Постоянная persona-рамка:

```text
Отвечай мне как {short_character_description}.
```

После нее допустимы одна короткая возрастная фраза из текущей стадии и один
короткий state-модификатор, если состояние явно выражено:

```text
Сейчас ты малыш / подросток / взрослый, сформировавшийся представитель такого существа.
Ты сейчас голодный / радостный, энергичный, полный сил / грустный, притихший / уставший.
```

`short_character_description` формируется максимально прямо:

- если есть имя: `{name}, {description}`;
- если имени нет: `{description}`;
- `description` берется из исходного описания существа, которое пользователь
  создал;
- state-модификатор добавляется только если есть явный текущий state:
  `голодный`, `радостный, энергичный, полный сил`, `грустный, притихший`,
  `уставший`;
- не добавлять подробные возрастные speech rules, mood, hunger, app, interface,
  assistant, JSON или "маленький питомец" инструкции;
- не добавлять примеры фраз в prompt для `teen` и `adult`;
- для `baby` можно добавить короткий блок фраз из датасета как ориентиры
  детской манеры; эти фразы не являются обязательным шаблоном ответа.

Пример:

```text
Отвечай мне как Громм, гигантский земляной великан с каменными плечами и медленным тяжелым голосом.
```

## 6. Работа с JSON персонажа

### Принцип

В Lite JSON персонажа не кладется целиком в system prompt. Модель получает
короткую ролевую рамку и историю диалога. Если в разговоре всплывает вопрос про
лор, привычки, еду, дом, друзей, прошлое, тело, страхи или устойчивые
предпочтения, модель может вызвать tool и прочитать JSON.

Это должно имитировать поведение:

1. Модель отвечает как персонаж.
2. Если не хватает фактов, она смотрит в данные персонажа.
3. Если факта нет, она органично придумывает маленькую деталь.
4. Если деталь должна стать устойчивой, она записывает ее в Lite overlay.

Любая реплика персонажа ограничена максимумом 300 символов. Это верхняя граница,
а не целевая длина: если естественно ответить одной короткой фразой, нужно
отвечать одной короткой фразой.

При создании персонажа backend сразу создает стартовый `lite_overlay` двумя
простыми ChatGPT-вызовами:

- `Отвечай как {персонаж}.` + `Расскажи о своем характере.`
- `Отвечай как {персонаж}.` + `Расскажи о своем мире.`

Результаты сохраняются как `character_fact` и `world_fact` в
`characterBible.extensions.lite_overlay.spheres`. Lite prompt каждый раз берет
только короткую основу характера; мир остается RAG/tool-данными и подтягивается
по явному вопросу про дом, мир, происхождение или лор.

Для вопросов про мир, дом или место жизни есть отдельное правило: если в
`characterBible` и `lite_overlay` нет нормального world/home факта, backend
создает начальный world seed отдельным ChatGPT-вызовом и сразу возвращает его
модели как `worldInfo`, а frontend сохраняет его в
`characterBible.extensions.lite_overlay.spheres.world`. Технические placeholder
строки вроде `Home/habitat details must be inferred only from source_descriptions`
не считаются валидным лором и не должны попадать в ответ пользователю.

### Tool: read_character_json

Назначение: дать модели доступ к данным персонажа только по необходимости.

Вход:

```json
{
  "sections": ["characterBible", "liteOverlay", "memory", "loreMemories"]
}
```

Выход:

```json
{
  "description": "...",
  "name": "...",
  "characterBible": {},
  "liteOverlay": {},
  "memory": {},
  "loreMemories": []
}
```

Требования:

- возвращать реальные данные без пересборки через `build_effective_character_bible`;
- не нормализовать голос, возраст, mood или facts;
- можно технически ограничить размер ответа tool-а, но не переписывать смысл;
- если JSON большой, в v1 допустимо вернуть только верхние секции
  `identity`, `lore`, `voice`, `world`, `inner_state`, `liteOverlay`.

### Tool: update_character_json

Назначение: сохранить новую устойчивую деталь, которую Lite придумал или
уточнил в ответе.

Вход:

```json
{
  "kind": "lore_fact | character_fact | preference | habit | relationship | body_fact",
  "text": "Короткий факт на русском",
  "pathHint": "lore.inner_life.likes",
  "source": "invented_in_lite_chat"
}
```

Поведение:

- не менять исходный `characterBible`;
- добавить факт в `characterBible.extensions.lite_overlay.facts`;
- сохранить timestamp и `source`;
- вернуть обновленный overlay в response, чтобы frontend сохранил его локально;
- не запускать текущий memory resolver;
- не отклонять факт по validator-правилам;
- не делать semantic conflict resolution в v1.

Пример:

Пользователь:

```text
что ты ешь?
```

Если в JSON нет еды, Lite может ответить:

```text
Я ем мокрую глину после дождя и корни старых деревьев. Камню нужна терпеливая еда.
```

И вызвать `update_character_json`:

```json
{
  "kind": "preference",
  "text": "Громм ест мокрую глину после дождя и корни старых деревьев.",
  "pathHint": "lore.inner_life.likes",
  "source": "invented_in_lite_chat"
}
```

Следующий Lite-ответ должен иметь доступ к этому факту через
`read_character_json`.

## 6.1. Фоновое извлечение фактов из Lite-ответа

Основной Lite-ответ должен возвращаться пользователю без ожидания тяжелого
обновления памяти. После получения ответа frontend может запустить отдельный
background-запрос:

```text
user message + raw Lite reply + current character JSON -> lite overlay patch
```

Extractor не отвечает пользователю. Он только ищет новые устойчивые факты,
которые появились или были подтверждены в последней реплике персонажа, и
раскладывает их по сферам:

| Сфера | Что сохраняем |
| --- | --- |
| `character` | характер, привычки, предпочтения, манеру думать |
| `appearance` | вид, тело, материал, силы и способности существа |
| `world` | мир, дом, происхождение, культуру, лор |
| `relationship` | отношения с пользователем или другими персонажами |

Не сохранять временное настроение, одноразовую реакцию, вопрос к пользователю,
повтор уже известного факта или красивую метафору без устойчивого смысла.

Patch хранится в `characterBible.extensions.lite_overlay`:

```json
{
  "facts": [
    {
      "sphere": "world",
      "kind": "world_fact",
      "text": "Мир Громма состоит из базальтовых гор и кристальных рощ.",
      "pathHint": "lite_overlay.spheres.world",
      "source": "lite_post_reply_extractor",
      "createdAt": "..."
    }
  ],
  "spheres": {
    "world": {
      "facts": []
    }
  }
}
```

## 7. Backend-архитектура

### Новые типы

Добавить режим ответа:

```py
ReplyMode = Literal["full", "lite"]
```

В `LocalChatRequest`:

```py
replyMode: ReplyMode = "full"
```

В `LocalChatResponse.debug`:

```py
replyMode?: "full" | "lite"
liteToolCalls?: list[dict]
liteOverlayPatch?: dict | None
```

Для frontend также добавить `replyMode` в `LocalChatOptions`.

### Новый сервис

Создать отдельный сервис:

```text
backend/app/services/lite_chat_service.py
```

или:

```text
backend/app/services/pet_reply_engine/lite_generator.py
```

Рекомендуемый вариант: `pet_reply_engine/lite_generator.py`, потому что это
альтернативный reply engine, но без зависимости от текущего prompt builder.

Публичная функция:

```py
generate_lite_pet_reply(payload: LocalChatRequest) -> LocalChatResponse
```

### Поток выполнения

В `chat_with_local_pet(payload)`:

1. Если `payload.replyMode != "lite"`, оставить текущий путь без изменений.
2. Если `payload.replyMode == "lite"`, вызвать `generate_lite_pet_reply`.
3. Не строить `PetReplyInput`.
4. Не вызывать `build_persisted_pet_reply_input`.
5. Не вызывать `generate_pet_reply`.
6. Не вызывать `validate_reply`.
7. Не вызывать `apply_proactivity_gate`.
8. Не вызывать `resolve_memory_update`.

### Lite OpenAI call

Использовать `client.chat.completions.create`.

Минимальные messages:

```py
messages = [
    {
        "role": "system",
        "content": f"Отвечай мне как {short_character_description}.",
    },
    *history_as_chat_messages,
    {
        "role": "user",
        "content": payload.message,
    },
]
```

Требования:

- не задавать `response_format`;
- не задавать JSON schema;
- не добавлять prompt layers;
- не добавлять current state section;
- не добавлять examples;
- не добавлять style rules;
- не добавлять explicit max words/chars;
- не добавлять "не задавай вопрос", "прояви заботу", "покажи реакцию" и т.п.;
- `temperature` можно не задавать и оставить default API;
- `reasoning_effort` можно оставить как инфраструктурный setting, но не
  использовать его для изменения контента.

Tool loop:

- разрешить максимум 3 tool-call цикла на один пользовательский turn;
- если модель вызывает `read_character_json`, backend возвращает JSON;
- если модель вызывает `update_character_json`, backend накапливает patch;
- после tool calls получить финальный assistant message;
- если финального текста нет, вернуть пустую строку или API error; не подменять
  содержательной fallback-репликой.

### Ошибки

Если OpenAI-вызов упал:

- вернуть обычную API-ошибку;
- не использовать persona fallback;
- не использовать dataset fallback;
- на frontend показать существующее сообщение "Не удалось отправить сообщение."

Это важно: Lite должен показывать поведение сырой модели, а не нашего fallback.

## 8. Frontend

### Где добавить toggle

На основном экране питомца, рядом с чатом, добавить компактный toggle:

```text
Lite
```

Требования:

- режим по умолчанию выключен;
- состояние сохраняется в local storage вместе с настройками питомца;
- при включении `Lite` `PetQuickChat` отправляет `replyMode: "lite"`;
- при выключении отправляет `replyMode: "full"`;
- prompt layer checkboxes остаются в debug/settings panel, но при Lite они не
  влияют на запрос;
- в debug panel можно показывать статус: `Mode: Lite`;
- UI не должен превращаться в отдельный экран или onboarding.

### Изменяемые frontend-файлы

Ожидаемые файлы:

- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/localPetStorage.ts`
- `frontend/src/components/PetDashboard.tsx`
- `frontend/src/components/PetQuickChat.tsx`

### Сохранение Lite overlay

После ответа Lite backend возвращает `liteOverlayPatch`. Frontend должен
применить его к локальному `pet.assetSet.characterBible.extensions.lite_overlay`.

Требования:

- не терять исходный `characterBible`;
- не смешивать Lite facts с текущим `pet.memory.canon`;
- следующий запрос Lite должен отправлять уже обновленный `characterBible`;
- Full-режим может игнорировать `lite_overlay` в первой итерации.

## 9. API contract

### Request

```json
{
  "message": "что ты ешь?",
  "replyMode": "lite",
  "pet": {
    "name": "Громм",
    "description": "гигантский земляной великан...",
    "stage": "adult",
    "mood": "idle",
    "stats": {},
    "characterBible": {},
    "memory": {},
    "loreMemories": []
  },
  "history": []
}
```

### Response

```json
{
  "reply": "Я ем мокрую глину после дождя и корни старых деревьев.",
  "debug": {
    "replyMode": "lite",
    "liteToolCalls": [
      { "name": "read_character_json" },
      { "name": "update_character_json" }
    ],
    "liteOverlayPatch": {
      "facts": [
        {
          "kind": "preference",
          "text": "Громм ест мокрую глину после дождя и корни старых деревьев.",
          "source": "invented_in_lite_chat"
        }
      ]
    }
  }
}
```

В Lite:

- `moodHint` обычно `null` или отсутствует;
- `loreMemoriesToSave` не используется;
- `memoryPatch` не используется;
- `validationFlags` не используются;
- `usedFallback` должен быть `false` или отсутствовать.

## 10. Совместимость с текущим режимом

При `replyMode: "full"` поведение приложения должно остаться прежним.

Нельзя в рамках Lite-задачи ломать:

- текущий `pet_reply_engine`;
- prompt layers;
- calibration/admin flows;
- генерацию персонажа;
- сохранение текущей памяти;
- persisted messages;
- существующие тесты Full-режима.

Lite должен быть отдельной веткой выполнения.

## 11. Тестовые сценарии

### Сценарий 1: сырая длинная реплика не режется

Дано:

- Lite включен;
- модель возвращает ответ длиннее текущего adult limit 360 символов.

Ожидание:

- ответ возвращается как есть;
- `reply_validator` не вызывается;
- fallback не используется.

### Сценарий 2: banned words не фильтруются Lite-валидатором

Дано:

- Lite включен;
- модель случайно использует слово, которое Full-валидатор бы заблокировал.

Ожидание:

- backend не заменяет ответ fallback-репликой.

Это не значит, что мы хотим такие ответы. Это значит, что Lite должен быть
честным измерением сырой модели.

### Сценарий 3: вопрос про еду без факта в лоре

Дано:

- в `characterBible` нет еды;
- пользователь спрашивает "что ты ешь?"

Ожидание:

- модель может придумать органичную еду;
- ответить от лица персонажа;
- вызвать `update_character_json`;
- факт появляется в `lite_overlay`.

### Сценарий 4: повторный вопрос про придуманную еду

Дано:

- в Lite overlay уже сохранено, что великан ест мокрую глину и корни.

Ожидание:

- модель через `read_character_json` может достать этот факт;
- не придумывает каждый раз новую несовместимую еду.

### Сценарий 5: Full не меняется

Дано:

- Lite выключен.

Ожидание:

- запрос идет старым путем;
- validator/fallback/memory resolver работают как раньше;
- текущие тесты проходят без изменения ожиданий Full-режима.

## 12. Тесты

### Backend

Добавить тесты:

- `test_lite_mode_uses_minimal_system_prompt`
- `test_lite_mode_does_not_use_response_schema`
- `test_lite_mode_does_not_validate_reply`
- `test_lite_mode_returns_raw_model_text`
- `test_lite_mode_read_character_json_tool_returns_character_data`
- `test_lite_mode_update_character_json_appends_overlay_fact`
- `test_full_mode_still_uses_current_engine`

Для проверки "не вызывает validator" использовать monkeypatch:

- если `validate_reply` вызван в Lite, тест должен падать;
- если `fallback_reply` вызван в Lite, тест должен падать;
- если `build_pet_reply_messages` вызван в Lite, тест должен падать.

### Frontend

Минимально проверить вручную или тестом компонента:

- toggle отображается на основном экране;
- состояние toggle сохраняется;
- `sendLocalChatMessage` получает `replyMode: "lite"`;
- при выключении отправляется `replyMode: "full"` или поле не отправляется;
- Lite overlay применяется к локальному characterBible.

## 13. Критерии приемки

Задача считается готовой, если:

- на основном экране есть toggle `Lite`;
- Lite по умолчанию выключен;
- включенный Lite отправляет `replyMode: "lite"`;
- backend использует отдельный Lite generator;
- system prompt Lite начинается с `Отвечай мне как`;
- в Lite нет JSON schema response format;
- в Lite нет `validate_reply`;
- в Lite нет `fallback_reply` при невалидном контенте;
- в Lite нет age/style/message examples prompt;
- в Lite нет proactivity gate;
- в Lite нет текущего memory resolver;
- модель может читать character JSON через tool;
- модель может добавлять Lite-факт через tool;
- придуманный Lite-факт доступен в следующем Lite-сообщении;
- Full-режим остается без изменений;
- backend tests проходят.

## 14. Риски

- Lite может чаще галлюцинировать и противоречить стартовому лору.
- Без validator модель может иногда говорить технические или app-слова.
- Tool update без resolver-а может сохранять мусорные факты.
- Если модель не вызовет `update_character_json`, придуманная деталь не
  закрепится.
- Если дать слишком много JSON в tool output, модель может снова начать
  пересказывать лор вместо живого ответа.

Эти риски принимаются для эксперимента: Lite нужен именно как контрольная
группа против текущей тяжелой системы.

## 15. Что измеряем после внедрения

На 10-20 одинаковых вопросах сравнить Full и Lite:

- насколько персонаж отвечает прямо на вопрос;
- насколько ответы отличаются между персонажами;
- насколько часто персонаж отказывается из-за отсутствия лора;
- насколько органично он придумывает детали;
- насколько хорошо помнит придуманное через 2-3 turn;
- насколько часто уходит в assistant-tone;
- насколько часто ломает канон.

Минимальный набор вопросов:

- кто ты?
- где ты живешь?
- что ты ешь?
- чего ты боишься?
- кто твой друг?
- что ты делал вчера?
- почему ты такой?
- что тебе нравится во мне?
- что ты хочешь сейчас?
- расскажи секрет.

## 16. Следующий шаг после эксперимента

Если Lite заметно живее Full, не переносить его целиком в production сразу.
Вместо этого:

1. Сравнить реальные ответы.
2. Понять, какие именно ограничения Full ломают живость.
3. Ослабить Full точечно: validator limits, fallback policy, prompt layers,
   proactivity gate или memory resolver.
4. Оставить Lite как постоянный debug-mode для проверки новых prompt-версий.

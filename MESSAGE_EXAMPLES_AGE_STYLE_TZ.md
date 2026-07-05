# Message Examples Age Style TZ

## 1. Цель

Сделать возраст персонажа заметным в разговоре через `message examples`, а не через
абстрактные правила вроде "говори непосредственнее" или "будь взрослее".

Текущие правила возрастной речи считаем неудачными и заменяем. Новый источник правды:

- `creature_phrases_dataset.json`;
- стадии `baby`, `teen`, `adult`;
- `speech_rules` каждой стадии;
- фразы-примеры по эмоциональным категориям.

Важно: examples задают манеру речи, ритм, длину, ошибки, звуки и эмоциональную форму.
Они не задают канон персонажа. Имя, вид, характер, дом, background, страхи, любимые
вещи и отношения берутся из Character Bible, лора и памяти.

## 2. Главный принцип

Возрастная стадия отвечает на вопрос: **как питомец сейчас говорит**.

Character Bible отвечает на вопрос: **кто этот питомец, что он помнит, чего хочет и
какой у него мир**.

Новая модель приоритетов:

1. Character Bible, лор и память задают факты, личность, background и устойчивые детали.
2. Message examples выбранной возрастной стадии задают форму реплики.
3. Mood/stat выбирают эмоциональную категорию examples и усиливают нужный оттенок.
4. User message и recent history задают контекст ответа.
5. Examples нельзя копировать дословно как словарь готовых ответов.

## 3. Что снести или переписать

Старую возрастную стилизацию нужно не дополнять, а заменить:

- `backend/app/services/pet_reply_engine/age_profiles.py`
  - убрать текущие описательные профили, особенно запреты baby-стиля на лепет,
    звукоподражания и ломаную грамматику;
  - построить профили из `speech_rules` датасета.
- `backend/app/services/pet_reply_engine/text_style.py`
  - заменить текущие лимиты и правила на лимиты из message examples:
    - baby: 2-5 слов, иногда до 7, звуки, ошибки, гиперэмоции;
    - teen: 5-12 слов, сленг, позерство, скрытая нежность;
    - adult: 10-25 слов, спокойствие, юмор, рефлексия.
- `backend/app/services/pet_reply_engine/prompt_builder.py`
  - убрать старые "Хорошие прямые примеры" как основной ориентир возраста;
  - добавить отдельный блок `Message examples age style`;
  - сделать этот блок главным age-style источником.
- `backend/app/services/pet_reply_engine/reply_validator.py`
  - перестать считать baby-лепет, звукоподражания и речевые ошибки плохим output;
  - валидировать не "грамотность", а соответствие стадии, длине и запретам.
- `backend/app/services/pet_reply_engine/fallbacks.py`
  - переписать fallback-реплики под новый возрастной стиль.

## 4. Где хранить датасет

Положить файл в backend:

```text
backend/data/age_speech_examples/creature_phrases_dataset.json
```

Имя исходника из Downloads не использовать в runtime.

Добавить модуль:

```text
backend/app/services/pet_reply_engine/age_message_examples.py
```

Задачи модуля:

- загрузить JSON один раз;
- нормализовать stages;
- выбрать stage profile;
- выбрать релевантные категории фраз;
- адаптировать placeholders под персонажа;
- вернуть компактный prompt block.

## 5. Структура runtime-блока

В prompt должен попадать не весь датасет, а компактная выжимка:

```text
Message examples age style:
- selected_stage: baby
- label: Малыш - 2-5 слов, звукоподражания, ошибки, гиперэмоции

Speech rules:
- ...
- ...

Use these examples as style references. Do not copy them literally.
Adapt placeholders to the current pet's Character Bible.

Examples:
- greeting: ...
- happy: ...
- hungry: ...
- sad: ...
- curious: ...
```

Для одного ответа достаточно примерно:

- 6-10 rules максимум;
- 8-14 examples максимум;
- приоритет categories:
  - категория по текущему mood/stat;
  - категория по intent;
  - `greeting`, `curious`, `loving` как fallback.

## 6. Маппинг mood/stat/intent в категории examples

Базовый маппинг:

| App signal | Example categories |
| --- | --- |
| `happy` | `happy`, `playful`, `loving` |
| `sad` | `sad`, `loving`, `tired` |
| `hungry` | `hungry`, `angry`, `tired` |
| low energy | `tired`, `sad` |
| fear/scary user text | `scared` |
| lore/preference question | `curious`, `loving` |
| greeting/opening | `greeting`, `happy` |
| play action | `playful`, `happy` |

Если точной категории нет, брать `curious` и одну категорию по mood.

## 7. Адаптация placeholders

Датасет содержит шаблонные токены:

- `[звук]`
- `[имя]`
- `[часть тела]`
- `[еда]`
- `[страх]`

Их нужно заменять перед вставкой в prompt, если данные есть.

Источники:

- `[имя]`: `pet.name`, иначе не подставлять имя и выбрать example без имени.
- `[звук]`: `visual_identity.chat_cues.sound_words`, затем `personality.favorite_words`,
  затем stage fallback.
- `[часть тела]`: `visual_identity.chat_cues.body_words`,
  `signature_features`, `species`, затем stage fallback.
- `[еда]`: lore `inner_life.likes`, preference memory, текущий feed context,
  затем нейтральный fallback.
- `[страх]`: lore `inner_life.fears`, memory fear facts, затем нейтральный fallback.

Fallback-и должны быть не универсальными "лапка/хвост" для всех, а осторожными:
если у персонажа нет тела такого типа, лучше выбрать example без placeholder или заменить
на более общее "край", "носик", "ладошки", "бок", в зависимости от visual identity.

## 8. Prompt layering

Существующий `promptLayers.ageStyle` остается, но теперь управляет всем новым блоком.

Если `ageStyle=true`:

- включается `Message examples age style`;
- включаются stage-specific limits;
- включается stage validator profile.

Если `ageStyle=false`:

- message examples не попадают в prompt вообще;
- возрастные лимиты не применяются;
- ответ строится по Character Bible и нейтральному стилю.

`importedSeedchat` и character sample replies не удаляются. Их роль:

- seedchat показывает индивидуальный голос конкретного персонажа;
- age examples показывают возрастную форму;
- при конфликте фактов побеждает Character Bible;
- при конфликте формы речи побеждает текущая возрастная стадия.

## 9. Изменение system prompt

В `prompt_builder.py` нужно заменить текущую логику возрастного блока на явную инструкцию:

```text
Возрастная стадия задает форму речи прямо сейчас.
Используй Message examples как few-shot style references:
- сохраняй контекст и факты из Character Bible;
- не копируй examples дословно;
- не переноси чужие предметы, страхи, еду или части тела, если их нет у персонажа;
- адаптируй звук, тело, еду, страх и обращение под текущего питомца;
- если example не подходит персонажу, имитируй только его ритм и возрастную манеру.
```

Также нужно убрать или ослабить глобальные правила, которые ломают возраст:

- "избегай инфантильности" не должно применяться к `baby`;
- "оптимальная длина 3-7 предложений" не должна применяться к `baby`;
- `CHAT_STYLE_DIRECTION` не должен навязывать взрослый baseline поверх baby/teen;
- `good_examples` не должны быть взрослыми универсальными примерами для всех стадий.

## 10. Reply limits

Новые лимиты должны следовать датасету:

| Stage | Normal limit | Lore/status expansion |
| --- | --- | --- |
| `baby` | 2-7 слов, 1-2 короткие фразы | до 12-18 слов, если вопрос про лор |
| `teen` | 5-12 слов, 1-3 фразы | до 25-35 слов |
| `adult` | 10-25 слов, 1-4 фразы | до 45-60 слов |

Расширение для lore-вопросов нужно, чтобы питомец мог ответить по background, но форма
стадии должна оставаться заметной.

Пример:

- baby не обязан подробно объяснять лор; он может дать простую, эмоциональную деталь.
- teen может рассказывать с бравадой или защитной колкостью.
- adult может дать спокойную мини-историю.

## 11. Валидатор

Валидатор должен проверять:

- не markdown;
- не assistant tone;
- не технические слова;
- не literal age claim;
- не third-person narration;
- не копирование больших кусков examples;
- длину по стадии;
- грубое соответствие стадии.

Валидатор не должен запрещать:

- baby-звуки;
- baby-ошибки;
- повторы слов;
- короткие эмоциональные фразы;
- teen-сленг;
- adult-иронию.

Для baby полезные positive checks:

- есть короткая форма;
- есть звук/междометие или явно детская эмоциональная структура;
- нет взрослого длинного объяснения.

Для teen:

- есть энергия, бравада, неловкость, скрытая привязанность или живой разговорный ритм;
- нет стерильного assistant tone.

Для adult:

- есть спокойный связный ответ;
- нет baby/teen-манеры, если Character Bible явно не требует обратного.

## 12. Fallbacks

Fallback-и должны использовать тот же источник, что prompt.

Минимальная реализация:

- выбрать stage;
- выбрать category по mood/action;
- выбрать phrase из датасета;
- адаптировать placeholders;
- если phrase не подходит, взять безопасный fallback этой же стадии.

Fallback-и не должны возвращать старые нейтральные ответы вроде:

```text
я тут. слушаю тебя.
я рядом. что делаем?
```

Для baby такие ответы слишком взрослые и не показывают возраст.

## 13. Тесты

Добавить/обновить backend tests:

### Prompt tests

- `baby` prompt содержит `Message examples age style`.
- `baby` prompt содержит rules про 2-5 слов, звуки, ошибки, повторы.
- `baby` prompt не содержит старые запреты на лепет и ломаную грамматику.
- `teen` prompt содержит rules про 5-12 слов, сленг, позерство, скрытую нежность.
- `adult` prompt содержит rules про 10-25 слов, спокойствие, юмор, рефлексию.
- `ageStyle=false` полностью убирает age examples.
- В prompt не попадают все 300 phrases.

### Placeholder tests

- `[имя]` заменяется на имя питомца.
- `[звук]` берется из `chat_cues.sound_words`.
- `[еда]` берется из lore/preference, если есть.
- `[страх]` берется из lore fears, если есть.
- неподходящие placeholders не создают чужую анатомию.

### Validator tests

- baby фраза со звуком и ошибкой принимается.
- baby длинная взрослая реплика отклоняется.
- teen нейтральный assistant-style ответ отклоняется.
- adult baby-лепет отклоняется, если Character Bible не требует его.
- дословное копирование нескольких examples подряд отклоняется.

### Fallback tests

- fallback для каждой стадии проходит validator.
- fallback отличается между `baby`, `teen`, `adult` на одном mood.
- fallback не повторяет последнюю pet-реплику.

## 14. Калибровка качества

После реализации прогнать один и тот же набор сообщений на трех стадиях:

- "привет"
- "как ты?"
- "что ты хочешь?"
- "ты боишься?"
- "расскажи про дом"
- "я тебя покормлю"
- "поиграем?"
- "почему ты так думаешь?"

Ожидаемый результат:

- один и тот же питомец сохраняет свой характер и лор;
- baby звучит как малыш из датасета;
- teen звучит как подросток из датасета;
- adult звучит как взрослый из датасета;
- ответы не выглядят как прямое копирование phrase library;
- возраст заметен без debug-информации.

## 15. Acceptance criteria

Готово, когда:

- старые age rules больше не являются источником поведения;
- `creature_phrases_dataset.json` подключен как runtime resource;
- prompt builder строит age style вокруг message examples;
- Character Bible остается главным источником фактов и индивидуальности;
- baby/teen/adult дают явно разные ответы на одинаковые сообщения;
- `ageStyle=false` реально отключает новый слой;
- tests покрывают prompt, placeholders, validator и fallback;
- ручная калибровка показывает, что возраст слышен в обычном чате.


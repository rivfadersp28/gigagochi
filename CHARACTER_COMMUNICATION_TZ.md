# ТЗ: естественная модель общения персонажа

Дата: 2026-07-05

## 1. Цель

Сделать так, чтобы персонаж в Tamagochi воспринимался как живое существо с
узнаваемым характером, а не как один общий "уютный питомец" с разными
описаниями.

Целевой эффект:

- разные персонажи отвечают на один и тот же вопрос разной структурой, ритмом и
  мотивацией;
- возраст, настроение и игровые статы слегка окрашивают речь, но не переписывают
  личность;
- персонаж отвечает прямо на вопрос пользователя, а не уходит в одинаковые
  лоровые паттерны;
- внешние датасеты и character cards используются как источник реального
  контента, seed-dialogues и речевых паттернов;
- синтетические 38 карточек перестают быть основным источником голоса.

## 2. Диагноз текущей проблемы

В текущем коде уже есть `pet_reply_engine`, `Character Profile V2`, память,
reference cards и импорт Character Card. Проблема не в отсутствии слоев, а в том,
что общие слои слишком сильные и попадают в каждый ответ.

Найденные источники усреднения:

- `backend/app/services/pet_reply_engine/prompt_builder.py` содержит глобальное
  "Ты - маленький цифровой питомец внутри игры". Это полезно как fallback, но
  плохо как верхний слой для любого персонажа.
- `_baby_voice_block` прямо разрешает `шур-шур`, `мр-мр`, `пику-пику`. Если
  модель однажды подхватила "шур", она начинает тащить это как стиль.
- `state_interpreter.py` и `text_style.py` задают возраст/настроение как
  полноценный речевой режим. В итоге "малышовый голос", "тише", "бодрее" часто
  сильнее, чем character profile.
- `profile_v2.py` содержит одинаковые defaults: "стать ближе к собеседнику через
  маленькие общие дела", "хочет внимания, но боится звучать навязчиво",
  "маленький бытовой юмор через предметы и привычки". Эти defaults нужны для
  пустых профилей, но они создают общий характер всем персонажам.
- `backend/data/reference_cards/ru_pet_reply_cards.jsonl` формально содержит
  структурные карточки, но примеры все равно синтетические и повторяют один
  класс мотивов: край, бирка, ящик, крышка, нижняя полка. Даже если не копировать
  examples, модель видит повторяемую фактуру.

Вывод: нельзя решать это словарным баном. Нужно управлять слоями промпта,
заменить синтетическую фактуру внешним контентом и ввести проверку повторяемых
мотивов.

## 3. Изученные источники и что берем

### Character Card v2/v3, Chub, CCEditor

Источники:

- https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md
- https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md
- https://chub.ai/characters
- https://docs.chub.ai/docs/the-basics/character-creation
- https://docs.chub.ai/docs/advanced-setups/lorebooks
- https://github.com/lenML/CCEditor

Что берем:

- импорт `name`, `description`, `personality`, `scenario`, `first_mes`,
  `mes_example`;
- `system_prompt` и `post_history_instructions` как character-owned instructions,
  но с debug-возможностью выключить их влияние;
- `alternate_greetings` как источник первых сообщений и разных входов в
  персонажа;
- `character_book` / lorebook entries: `keys`, `secondary_keys`, `content`,
  `priority`, `constant`, `selective`, `scan_depth`, `token_budget`;
- из v3: `assets`, `nickname`, `source`, `group_only_greetings`,
  `creation_date`, `modification_date`;
- из Chub: разделение metadata и character definition, а также модель prompt
  structure: system prompt -> character definitions -> chat history -> post
  history instructions -> prompt note.

Как использовать:

- Character Card v2/v3 должен стать основным импортным форматом для эталонных
  персонажей.
- Chub-карточки использовать в прототипе как реальные character examples, но
  хранить `source_url` и `license_note`.
- CCEditor использовать как референс совместимости форматов, код не копировать
  без отдельного решения из-за AGPL-3.0.

### a16z companion-app

Источник:

- https://github.com/a16z-infra/companion-app

Главный вывод: это лучший быстрый референс для нашего прототипа.

Что берем:

- формат персонажа: `preamble` -> `seedchat` -> `backstory`;
- `preamble` всегда входит в промпт как короткое ядро личности;
- `seedchat` задает голос через реальные реплики, а не через абстрактные правила;
- `backstory` индексируется и достается через similarity search только когда
  релевантен;
- recent chat history хранится отдельно от character definition.

Что адаптировать:

- для каждого питомца хранить 5-12 seed exchanges на русском;
- seedchat должен быть сильнее возраста/настроения;
- backstory/lore не должен попадать целиком в каждый ответ.

### mem0, SoulForge, OpenHer, Open Character AI

Источники:

- https://github.com/mem0ai/mem0
- https://github.com/Ahmed-KHI/soulforge-framework
- https://github.com/kellyvv/OpenHer
- https://github.com/Anil-matcha/open-character-ai

Что берем:

- mem0: multi-level memory: user, session, agent/persona; память должна быть
  релевантной, с provenance и контролем записи;
- SoulForge/OpenHer: внутренние drives и emotion state должны жить как числа и
  динамика, а не как готовые фразы в промпте;
- OpenHer: "чувство сначала, текст потом" как архитектурный принцип: сначала
  вычислить внутреннюю реакцию, затем коротко выразить ее в голосе персонажа;
- Open Character AI: debug/tuning panel с per-chat настройками `temperature`,
  `max tokens`, prompt override.

Что не брать:

- не переносить большие emotion-подсистемы сразу;
- не делать настроение отдельным речевым шаблоном, который перебивает character
  voice.

### LIGHT, CRD3, VideoGameDialogueCorpus

Источники:

- https://github.com/facebookresearch/LIGHT
- https://github.com/RevanthRameshkumar/CRD3
- https://github.com/seannyD/VideoGameDialogueCorpusPublic

Что берем:

- LIGHT: персонаж должен быть grounded in world: места, предметы, действия,
  роли, привычки;
- CRD3: связка `summary -> aligned dialogue turns` полезна для open threads,
  recap и продолжения темы;
- VideoGameDialogueCorpus: структура `ACTION` и `CHOICE` полезна для маленьких
  игровых диалоговых ходов: реакция, действие, выбор, ветвление.

Как использовать:

- не копировать случайные длинные диалоги;
- извлекать dialogue moves: `answer`, `react`, `offer choice`, `continue thread`,
  `small action`, `refuse/boundary`;
- использовать action/choice как структуру реплики питомца, а не как UI-меню.

### Русские диалоговые датасеты

Источники:

- https://huggingface.co/datasets/kukunechka/russian-everyday-dialogues
- https://huggingface.co/datasets/Den4ikAI/russian_dialogues
- https://huggingface.co/datasets/inkoziev/Conversations
- https://huggingface.co/datasets/d0rj/dialogsum-ru

Приоритет:

1. `kukunechka/russian-everyday-dialogues`: маленький чистый набор из 20
   бытовых русских диалогов, CC BY 4.0. Использовать сразу как эталон
   естественного turn-taking: короткий вопрос, прямой ответ, конкретная бытовая
   деталь.
2. `d0rj/dialogsum-ru`: 14.5k русских dialogue-summary примеров, MIT.
   Использовать для open-thread summary и recap, не как голос персонажа.
3. `inkoziev/Conversations`: большой русскоязычный корпус 1M-10M,
   machine-generated, CC BY 4.0. Использовать только после фильтрации как
   статистику длины, turn-taking и negative sampling.
4. `Den4ikAI/russian_dialogues`: 2.47M Telegram Q/A с `relevance`, MIT.
   Полезен как большой корпус для ranker/negative examples, но как позитивный
   голос опасен из-за шума, токсичности и телеграмной манеры.

### Сказки, детские истории, детские диалоги

Источники:

- https://huggingface.co/datasets/merve/folk-mythology-tales
- https://huggingface.co/datasets/roneneldan/TinyStories
- https://huggingface.co/datasets/GEM/FairytaleQA
- https://www.kaggle.com/datasets/bond005/russian-child-tales
- https://huggingface.co/datasets/ayakiri/children-conversations-dataset

Что берем:

- FairytaleQA: story QA axes: character, setting, action, feeling, causal
  relationship, outcome resolution. Это полезно как источник ручных вопросов
  "почему?", "что случилось?", "что ты чувствуешь?";
- TinyStories: синтетические короткие истории с простой лексикой. Использовать
  осторожно как story grammar, не как стиль русского чата;
- folk-mythology-tales: фольклорная структура желания, запрета, испытания,
  последствий. Использовать для story seeds;
- ayakiri children conversations: 274 коротких input/response примера на
  английском, можно использовать как мягкие child-friendly response acts;
- Kaggle Russian_Child_Tales: сначала скачать и проверить структуру; страница
  описывает набор как nursery rhymes / Russian child tales, но без локальной
  проверки не делать его основным источником.

### Creature/lore источники

Источники:

- https://pokeapi.co/
- https://github.com/5ecompendium/bestiary
- https://github.com/foundryvtt/pf2e
- https://clayadavis.gitlab.io/osr-bestiary/
- https://dr-eigenvalue.github.io/bestiary/

Что берем:

- структуру creature profile: species, habitat, abilities, senses, limitations,
  behavior, routine, weakness;
- способности должны превращаться в поведение в диалоге, а не в список статов;
- у каждого существа должны быть ограничения, чтобы оно не звучало всемогущим;
- DnD/PF2E/Pokemon IP-термины не использовать в проде, но в прототипе можно
  использовать как structural/content reference с provenance.

## 4. Новая модель prompt layers

Нужно добавить объект `promptLayers`, который передается с запросом и управляет
тем, какие блоки реально входят в итоговый prompt.

Минимальная схема:

```json
{
  "ageStyle": true,
  "moodStyle": true,
  "statNeeds": true,
  "visualBodyCues": true,
  "babySounds": true,
  "characterCore": true,
  "importedSeedchat": true,
  "lore": true,
  "characterBook": true,
  "memory": true,
  "referenceCards": true,
  "dialogueMoves": true,
  "proactivity": true,
  "postHistoryInstructions": true
}
```

Правила:

- `characterCore` нельзя выключить в обычном режиме, но можно в dev/debug только
  для диагностики.
- "Чистый персонаж" выключает все ситуативные модификаторы:
  `ageStyle=false`, `moodStyle=false`, `statNeeds=false`,
  `visualBodyCues=false`, `babySounds=false`, `proactivity=false`.
- "Чистый imported card" оставляет только `characterCore`, `importedSeedchat`,
  recent history и прямой ответ на пользователя.
- Каждый выключенный слой не должен попадать в prompt вообще. Не писать
  "ignore mood"; просто не добавлять mood block.
- Debug должен показывать `includedLayers`, `excludedLayers`, selected cards,
  selected memory, token budget и финальные messages.

Файлы:

- `backend/app/schemas.py`: добавить `PromptLayers`.
- `backend/app/services/pet_reply_engine/models.py`: добавить
  `PetPromptLayers`.
- `backend/app/services/pet_reply_engine/prompt_builder.py`: сделать сборку
  секционной и layer-aware.
- `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`: передавать
  `promptLayers`.
- `frontend/src/components/PetDashboard.tsx`: в меню шестеренки добавить блок
  "Prompt" с переключателями.
- `frontend/src/components/ChatView.tsx`: использовать те же настройки при
  полном чате.

## 5. Правила силы слоев

Приоритет слоев сверху вниз:

1. Прямой смысл сообщения пользователя.
2. Imported character core / Character Profile V2.
3. Seedchat персонажа.
4. Recent dialogue history.
5. Релевантная память и character book.
6. Dialogue moves под intent.
7. State/drives как слабая окраска.
8. Визуальные/body cues только если вопрос связан с телом/внешностью или
   персонаж сам естественно реагирует телом.

Возраст и настроение не должны задавать лексику. Они задают только:

- лимит длины;
- энергию фразы;
- допустимость вопроса в конце;
- вероятность телесной реакции.

Запрещено:

- добавлять fixed sound examples вроде `шур-шур`, если они не пришли из профиля;
- заставлять baby-стадию говорить одинаковыми звуками;
- делать `mood=sad` причиной одинаковых "мне хочется рядом";
- использовать global defaults как character identity.

## 6. Замена синтетических 38 карточек

Текущий формат `ReferenceCard` хороший, но текущий набор должен стать
bootstrap/fallback, а не основным источником речи.

Требуется:

- пометить текущие карточки `source_family=bootstrap_synthetic`;
- снизить их вес в selector;
- добавить импортированные карточки из внешних источников;
- хранить не только abstract pattern, но и реальные короткие source snippets для
  прототипа, с `source_url`, `source_id`, `license_note`;
- добавить `motif_fingerprint`, чтобы не выбирать пять карточек про один и тот
  же мотив: край, ящик, ниша, кромка, бирка, полка.

Новая схема фрагмента:

```json
{
  "id": "string",
  "source_family": "character_card|ru_dialogue|game_dialogue|story|creature",
  "source_url": "string",
  "license_note": "string",
  "locale": "ru",
  "raw_text": "string",
  "speaker": "string|null",
  "context": "string|null",
  "dialogue_act": "answer_preference|why|care|status|choice|recap",
  "tags": ["string"],
  "safety_flags": ["string"],
  "motif_fingerprint": ["object_edge", "small_home", "ticket"],
  "use_for": ["seedchat", "reply_prompt", "manual_check"]
}
```

Импортированные source snippets можно использовать прямо в прототипе. В
промпте при этом по умолчанию подмешивать коротко и релевантно: 2-4 snippets или
3-6 distilled cards, а не весь корпус.

## 7. Импорт данных

Добавить source registry:

- `backend/data/external_sources/sources.json`

Для каждого источника хранить:

- `id`
- `url`
- `kind`
- `license`
- `status`: `planned|downloaded|normalized|filtered|active`
- `use_policy`: `direct_prototype|structure_only|manual_check|disabled`

Добавить pipeline:

- `backend/scripts/import_external_character_sources.py`
- `backend/scripts/normalize_dialogue_datasets.py`
- `backend/scripts/build_reference_cards_from_sources.py`

Минимальные импортеры:

- Character Card v2/v3 JSON/PNG/CHARX: в `Character Profile V2`.
- a16z companion `.txt`: `preamble`, `seedchat`, `backstory`.
- HF JSONL/Parquet: русские пары `user/assistant`, `question/answer`,
  `dialogue/summary`.
- VideoGameDialogue: `ACTION`, character lines, `CHOICE`.
- Creature sources: `species`, `habitat`, `abilities`, `limitations`,
  `routines`.

Фильтрация:

- language=ru для reply examples, если не нужен перевод;
- длина reply: 3-280 символов;
- удалить service/meta фразы;
- удалить repeated motifs выше лимита;
- грубо отфильтровать явно мусорные/токсичные строки offline, без runtime
  проверок через модель;
- для Telegram-корпусов использовать только записи с высокой релевантностью и
  после safety-фильтра.

## 8. Prompt builder: требуемое поведение

Prompt builder должен собирать секции явно:

- `task`: что нужно сделать сейчас;
- `user_turn`: последнее сообщение пользователя;
- `character_core`: identity, role, desire, conflict, boundaries;
- `voice_seed`: 3-5 seed replies или imported examples;
- `recent_history`: последние 6-12 turn;
- `memory`: только релевантные факты;
- `lorebook`: только сработавшие entries;
- `dialogue_move`: один ближайший ход под intent;
- `state`: компактные числа/labels, если слой включен;
- `runtime_constraints`: только короткие технические ограничения, если они
  реально нужны для JSON/длины;
- `output_schema`.

Требования:

- один ответ должен начинаться с прямого ответа на пользователя;
- если question intent `why`, ответ обязан объяснить причину предыдущей мысли,
  а не придумать новый лор;
- если intent `answer_lore`, можно добавить 1-2 детали, но нельзя менять дом,
  роль, вид и уже сохраненный канон;
- если intent `smalltalk`, лор и память почти не использовать;
- если пользователь просит "не задавай вопросы", `proactivity` выключается для
  этого ответа независимо от UI.

## 9. Memory model

Память должна не усиливать галлюцинации.

Нужно разделить:

- `user_fact`: пользователь сам сказал;
- `relationship_event`: произошло между пользователем и питомцем;
- `pet_canon_fact`: устойчивый факт о мире питомца;
- `pet_generated_fact`: модель придумала, еще не подтверждено;
- `open_thread`: незакрытая тема;
- `style_memory`: привычка речи, подтвержденная многими ответами;
- `rejected_pattern`: мотив или формулировка, которую нужно избегать.

Правила:

- model-generated canon не становится `pet_canon_fact` сразу, если это крупное
  изменение мира;
- repeated bad motif, например "все живут в норах/нишах", должен попадать в
  `rejected_pattern`;
- retrieval должен учитывать recency, confidence, entity overlap и intent.

## 10. Проверка без лишних прогонов

Автоматический eval живости, отчеты по 10-20 персонажам и отдельные
assistant-like quality gates сейчас не нужны. Они уже проходили на 100% и не
ловили реальную проблему, зато добавляют разработку, шум и потенциальные токены.

Оставить только механические проверки, которые не требуют дополнительных
модельных вызовов:

- выключенный `promptLayer` не попадает в итоговые messages;
- режим "Чистый персонаж" реально выключает age/mood/stat/visual/proactivity
  слои;
- fixed examples вроде `шур-шур` не захардкожены в baby layer;
- JSON/schema ответа не ломается;
- debug показывает `includedLayers` и `excludedLayers`.

Все качество голоса проверяется вручную владельцем приложения в реальном чате.
Для этого достаточно, чтобы UI позволял быстро переключать слои и создавать
персонажей без ожидания batch-отчетов.

## 11. UI требования

В меню шестеренки на главном экране добавить разделы:

- `Preview`: существующий выбор sprite stage/state.
- `Prompt`: переключатели prompt layers.
- `Debug`: включить показ prompt debug в ответе/API.

Команды:

- `Чистый персонаж`: выключает state/age/stat/visual/proactivity layers.
- `Все слои`: возвращает defaults.
- `Только профиль + seedchat`: оставляет character core, seedchat, recent
  history.
- `Скопировать debug`: dev-only, копирует финальные messages/sections.

Состояние настроек хранить локально в `tamagochi:v1:settings`.

## 12. План внедрения

### Фаза 0: диагностика, 0.5-1 день

- добавить `promptLayers`;
- добавить UI-переключатели в шестеренку;
- добавить prompt debug с included/excluded layers;
- убрать fixed examples `шур-шур` из baby layer, оставить только звуки из
  character profile.

Definition of Done:

- можно отправить одно сообщение с выключенным возрастом/настроением;
- debug показывает, что эти блоки не попали в prompt;
- баг со скриншота можно воспроизвести и сравнить с чистым режимом.

### Фаза 1: импорт эталонного контента, 1-3 дня

- добавить source registry;
- импортировать a16z companion files как `preamble/seedchat/backstory`;
- импортировать Character Card v2/v3;
- загрузить `kukunechka/russian-everyday-dialogues`;
- подготовить фильтр для `Den4ikAI` и `inkoziev`, но не включать их в основной
  prompt без фильтра.

Definition of Done:

- можно создать Character Profile V2 из внешней карточки;
- у персонажа есть реальные seed replies;
- selector выбирает external cards выше bootstrap synthetic.

### Фаза 2: переприоритизация prompt builder, 2-4 дня

- сделать секционную сборку prompt;
- state/drives перевести в слабые сигналы;
- добавить motif guard;
- заменить defaults в `profile_v2.py` на нейтральные пустые поля или
  `fallback_only` defaults.

Definition of Done:

- возраст/настроение перестают задавать общий голос;
- "расскажи о себе" у разных персонажей дает разную композицию ответа;
- synthetic edge/box/niche мотивы не повторяются сериями.

### Фаза 3: память и long-running качество, позже

- добавить provenance/confidence для generated facts;
- добавить rejected patterns;
- добавить hybrid retrieval по memory/lore/reference;
- добавить summary compression для open threads.

## 13. Первые файлы для изменения

Backend:

- `backend/app/schemas.py`
- `backend/app/services/pet_reply_engine/models.py`
- `backend/app/services/pet_reply_engine/prompt_builder.py`
- `backend/app/services/pet_reply_engine/state_interpreter.py`
- `backend/app/services/pet_reply_engine/text_style.py`
- `backend/app/services/reference_cards/selector.py`
- `backend/app/services/character_cards/importer.py`
- `backend/scripts/import_external_character_sources.py`

Frontend:

- `frontend/src/components/PetDashboard.tsx`
- `frontend/src/components/ChatView.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/localPetStorage.ts`

Data:

- `backend/data/external_sources/sources.json`
- `backend/data/external_character_sources/fragments.jsonl`
- `backend/data/reference_cards/*.jsonl`

## 14. Главное решение

Не добавлять еще один общий "ультра стиль" поверх всех персонажей.

Сначала нужно получить управляемую систему:

1. Выключаемые prompt layers.
2. Реальные imported seed dialogues.
3. Слабое, измеримое влияние возраста/настроения.
4. Motif-level guard вместо словарных банов.
5. Ручное тестирование в чате вместо автоматического eval.

Только после этого имеет смысл расширять память, lorebook и calibration lab.

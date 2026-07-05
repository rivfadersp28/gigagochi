# ТЗ: Calibration Lab для лора и диалогов

## 1. Цель

Сделать внутренний инструмент, который помогает калибровать генерацию `characterBible`, лора и реплик питомца через человеческие предпочтения.

Проблема: автоматическая оценка может ловить только явный мусор, но не умеет надежно оценивать вкус, живость, привязанность к персонажу и ощущение связного мира. Поэтому главный сигнал качества - ручное сравнение вариантов.

Инструмент должен:
- генерировать несколько вариантов лора и тестовых диалогов для одного описания питомца;
- показывать варианты в простом A/B или A/B/C интерфейсе;
- сохранять выбор, причины выбора, исходные промпты, модель, seed и auto-score;
- экспортировать размеченные пары для настройки промптов, evals, rerank-логики и будущего fine-tuning/preference-tuning.

## 2. Контекст текущего проекта

Уже есть:
- `backend/app/routers/admin_generation_lab.py` - локальный admin endpoint с `ENABLE_ADMIN_GENERATION_LAB`;
- `backend/app/services/admin_generation_lab_service.py` - генерация профиля и benchmark-диалога;
- `backend/app/prompts/pet_image_prompts.py` - генерация `characterBible`;
- `backend/app/services/pet_reply_engine/prompt_builder.py` - prompt для ответа питомца;
- `backend/app/services/pet_reply_engine/quality.py` - автоматические quality flags;
- `frontend/src/app/admin/generation-lab/page.tsx` - существующая админская страница генерации.

Новую фичу делать рядом с этим контуром:
- backend route: `/admin/calibration-lab`;
- frontend route: `/admin/calibration-lab`;
- доступ такой же, как у generation lab: только localhost, опциональный `X-Admin-Token`.

## 3. Внешние корпуса

### TinyStories

Источник: https://huggingface.co/datasets/roneneldan/TinyStories

Роль:
- использовать как источник структуры короткой причинно-следственной истории;
- извлекать story grammar cards: герой, место, желание, маленькая проблема, помощник, действие, результат, эмоция;
- не использовать как прямые русские диалоговые examples.

Ограничения:
- корпус английский;
- это рассказы, не chat dialogue;
- полезен для построения мира, но не должен напрямую задавать голос питомца.

### Video Game Dialogue Corpus

Источник: https://github.com/seannyD/VideoGameDialogueCorpusPublic

Роль:
- использовать как источник паттернов коротких реплик, сценических действий, реплик разных персонажей и ветвлений `CHOICE`;
- извлекать dialogue act cards: вопрос, реакция, уточнение, отказ, забота, приглашение, lore reveal, stage action;
- использовать для анализа структуры, а не для дословного копирования.

Ограничения:
- README описывает research corpus и сборку локальными скриптами;
- данные приходят в JSON `text`, где строки диалога идут списком, `ACTION` означает действие, `CHOICE` - ветку выбора;
- лицензия в репозитории явно не указана, есть открытый issue про missing `LICENSE.md`;
- нельзя включать дословные игровые реплики в продуктовые промпты, fine-tune датасеты или публичные артефакты без отдельной проверки прав.

## 4. Нефункциональные ограничения

- Фича только для локальной админки.
- Не показывать ее обычным пользователям.
- Не менять основной пользовательский flow создания питомца.
- Не запускать image generation в MVP.
- Не хранить секреты, API keys, auth headers или raw OpenAI request metadata.
- Все generated candidates и votes должны быть воспроизводимы по `runId`, `taskId`, `model`, `promptVersion`, `seed` и source description.
- MVP хранит данные append-only JSONL, без миграций Postgres. DB можно добавить позже, когда станет понятно, что формат стабилен.

## 5. Основные термины

| Термин | Определение |
| --- | --- |
| Calibration run | Запуск генерации пачки заданий для разметки. |
| Calibration task | Одна карточка сравнения: варианты A/B или A/B/C для одного описания и одного режима. |
| Candidate | Один сгенерированный вариант `characterBible` и/или набор реплик. |
| Vote | Ручная оценка: победитель, tie, reject all, skip, tags, заметка. |
| Prompt variant | Версия промпта или настроек генерации, которую сравниваем. |
| Reference card | Короткая структурная подсказка, извлеченная из TinyStories или VideoGameDialogueCorpus без дословного копирования. |
| Auto-score | Автоматическая оценка и flags из `quality.py` и новых проверок. |

## 6. Типы заданий

### 6.1. Lore Pairwise

Показывает 2-3 варианта `characterBible` для одного описания питомца.

Основной вопрос для ревьюера: какой мир хочется оставить как канон?

Показывать:
- исходное описание;
- `species`;
- `signature`;
- `personality`;
- `world.story`;
- `home.story`;
- `origin.story`;
- `relationships.story`;
- `core_want`;
- `inner_conflict`;
- `friends`;
- `story_seeds`;
- automatic flags.

Не показывать raw JSON по умолчанию. Raw JSON раскрывается через details.

### 6.2. Dialogue Pairwise

Показывает 2-3 набора ответов на одинаковые benchmark-вопросы при одном и том же `characterBible`.

Benchmark-вопросы MVP:
- `расскажи о себе`
- `где ты живешь?`
- `что ты любишь?`
- `почему?`
- `кто твой друг?`
- `чего ты боишься?`
- `что будем делать дальше?`

Основной вопрос для ревьюера: с каким вариантом реплик хочется продолжить общаться?

### 6.3. Full Character Pairwise

Показывает вариант целиком: краткий лор плюс benchmark-диалог.

Использовать как основной режим после MVP, потому что лор и диалог часто нельзя оценивать отдельно.

## 7. Критерии качества для ручной оценки

Хороший лор:
- имеет конкретное место, а не абстрактный уют;
- связан с визуальной идеей питомца;
- дает понятную бытовую или сказочную причинно-следственную логику;
- содержит повторяемые объекты, роли, привычки и социальные связи;
- объясняет желание и внутренний конфликт питомца;
- оставляет открытые крючки для будущего чата;
- не перегружен именами и одноразовыми событиями;
- не выглядит как template default.

Плохой лор:
- звучит как абстрактная поэзия;
- не связан с телом или видом питомца;
- перечисляет случайные факты без причины;
- слишком эпичный для маленького Tamagotchi;
- заранее закрывает все тайны;
- использует странную физику без объяснения;
- повторяет одни и те же уютные тепличные/полочные паттерны для разных существ.

Хороший диалог:
- прямо отвечает на вопрос;
- говорит от лица питомца, а не ассистента;
- звучит коротко, живо и по-русски;
- использует 1-2 конкретные детали мира, когда это уместно;
- продолжает предыдущую тему;
- показывает характер через выбор слов, маленькое действие или отношение;
- иногда предлагает конкретный следующий шаг;
- не превращает ответ в список или справку.

Плохой диалог:
- `я рядом`, `давай поговорим`, `что делаем` без детали;
- виден wrapper поверх AI;
- пересказывает весь лор;
- звучит как roleplay narration от третьего лица;
- задает слишком много вопросов;
- выдумывает крупный канон без необходимости;
- повторяет один catchphrase в каждом ответе.

## 8. Разметочные теги

Положительные:
- `живее`
- `связнее`
- `конкретнее`
- `лучше голос`
- `лучше мир`
- `лучше продолжает тему`
- `лучше дружба/отношения`
- `лучше крючки на будущее`
- `хочется продолжить`

Отрицательные:
- `слишком абстрактно`
- `звучит как ИИ`
- `нет характера`
- `нет мира`
- `нет причины`
- `рандомные факты`
- `перегружено именами`
- `слишком эпично`
- `слишком сухо`
- `противоречит описанию`
- `плохой русский`
- `слишком длинно`
- `не отвечает на вопрос`
- `повторяется`

Системные исходы:
- `tie`
- `reject_all`
- `skip`

## 9. UX страницы

### 9.1. Верхняя панель

Поля:
- `task type`: `lore_pairwise`, `dialogue_pairwise`, `full_character_pairwise`;
- `count`: 1-50;
- `candidates per task`: 2 или 3;
- `descriptions`: textarea, одно описание на строку;
- `prompt variants`: `current`, `tiny_story_cards`, `game_dialogue_cards`, `mixed_cards`;
- `include debug`: checkbox;
- `auto-filter bad candidates`: checkbox;
- `generate batch` button;
- `export votes` button;
- `export winning examples` button.

### 9.2. Review queue

Показывать:
- сколько заданий осталось;
- сколько оценено;
- текущий `taskId`;
- тип задания;
- source description;
- model/prompt version badges.

### 9.3. Candidate cards

Для A/B/C:
- компактный summary;
- benchmark replies;
- auto-score;
- flags;
- кнопка `Выбрать`;
- details с raw JSON и debug prompts.

Карточки должны быть сканируемыми. Не делать nested cards.

### 9.4. Панель оценки

Действия:
- `A лучше`
- `B лучше`
- `C лучше` если есть третий кандидат;
- `Одинаково`
- `Отклонить все`
- `Пропустить`

После выбора:
- показать tag toggles;
- optional note textarea;
- `Сохранить и дальше`;
- `Назад к прошлому заданию`.

### 9.5. Keyboard shortcuts

- `1`, `2`, `3` - выбрать candidate;
- `T` - tie;
- `X` - reject all;
- `S` - skip;
- `Enter` - сохранить и перейти дальше.

Shortcuts не должны срабатывать, когда focus внутри textarea/input.

## 10. Backend API MVP

### `GET /admin/calibration-lab/status`

Возвращает:

```json
{
  "status": "ready",
  "storage": "jsonl",
  "taskCount": 120,
  "voteCount": 64
}
```

### `POST /admin/calibration-lab/runs`

Создает batch задач.

Request:

```json
{
  "taskType": "full_character_pairwise",
  "descriptions": ["маленький дракон с мягкими крыльями"],
  "count": 10,
  "candidatesPerTask": 2,
  "promptVariants": ["current", "mixed_cards"],
  "includeDebug": true,
  "autoFilterBadCandidates": true
}
```

Response:

```json
{
  "runId": "cal_20260705_120000_abcd",
  "createdAt": "2026-07-05T12:00:00Z",
  "taskIds": ["task_001", "task_002"]
}
```

### `GET /admin/calibration-lab/tasks/next`

Возвращает следующее неразмеченное задание.

Query:
- `taskType` optional;
- `runId` optional.

### `GET /admin/calibration-lab/tasks/{taskId}`

Возвращает конкретное задание.

### `POST /admin/calibration-lab/votes`

Сохраняет vote append-only.

Request:

```json
{
  "taskId": "task_001",
  "winnerCandidateId": "cand_a",
  "outcome": "winner",
  "positiveTags": ["живее", "лучше мир"],
  "negativeTags": ["слишком абстрактно"],
  "note": "Вариант A лучше держит причину страха и дом.",
  "latencyMs": 18420
}
```

`outcome` values:
- `winner`
- `tie`
- `reject_all`
- `skip`

### `GET /admin/calibration-lab/export/votes`

Возвращает JSONL или `.json` со всеми votes.

### `GET /admin/calibration-lab/export/winners`

Возвращает dataset из winning candidates:
- для prompt examples;
- для offline анализа;
- для будущего supervised/preference training.

## 11. JSONL storage MVP

Создать директорию:

```text
backend/data/calibration/
  runs.jsonl
  tasks.jsonl
  votes.jsonl
  reference_cards.jsonl
```

Файлы append-only. Для локального MVP допускается чтение всего файла в память.

### Run record

```json
{
  "schemaVersion": 1,
  "runId": "cal_20260705_120000_abcd",
  "createdAt": "2026-07-05T12:00:00Z",
  "taskType": "full_character_pairwise",
  "descriptions": ["маленький дракон с мягкими крыльями"],
  "count": 10,
  "candidatesPerTask": 2,
  "promptVariants": ["current", "mixed_cards"],
  "model": "gpt-5.5",
  "status": "ready"
}
```

### Task record

```json
{
  "schemaVersion": 1,
  "taskId": "task_001",
  "runId": "cal_20260705_120000_abcd",
  "createdAt": "2026-07-05T12:00:02Z",
  "taskType": "full_character_pairwise",
  "description": "маленький дракон с мягкими крыльями",
  "benchmarkQuestions": ["расскажи о себе", "где ты живешь?"],
  "candidateIds": ["cand_a", "cand_b"],
  "candidates": [
    {
      "candidateId": "cand_a",
      "promptVariant": "current",
      "model": "gpt-5.5",
      "seed": "seed_a",
      "characterBible": {},
      "turns": [],
      "autoScore": 82,
      "qualityFlags": [],
      "debug": {}
    }
  ]
}
```

### Vote record

```json
{
  "schemaVersion": 1,
  "voteId": "vote_001",
  "taskId": "task_001",
  "runId": "cal_20260705_120000_abcd",
  "createdAt": "2026-07-05T12:04:10Z",
  "reviewerId": "local",
  "outcome": "winner",
  "winnerCandidateId": "cand_a",
  "positiveTags": ["живее"],
  "negativeTags": ["звучит как ИИ"],
  "note": "",
  "latencyMs": 18420
}
```

## 12. Генерация кандидатов

### Prompt variants MVP

`current`:
- текущий `build_character_bible_prompt`;
- текущий `build_pet_reply_messages`.

`tiny_story_cards`:
- добавляет 1-3 synthesized story grammar cards;
- требует причинно-следственную мини-структуру мира.

`game_dialogue_cards`:
- добавляет 1-3 synthesized dialogue act cards;
- требует короткие реплики с turn-taking, implied context и small reveal.

`mixed_cards`:
- комбинирует story grammar и dialogue act cards;
- основной экспериментальный вариант.

### Reference cards

Reference card не должен содержать длинную дословную цитату из корпуса.

Формат:

```json
{
  "cardId": "tiny_story_goal_helper_result",
  "source": "tinystories",
  "kind": "story_grammar",
  "pattern": "маленький герой хочет X, сталкивается с маленькой проблемой, получает помощь или пробует действие, после чего меняется чувство",
  "useFor": ["world", "origin", "inner_life", "story_seeds"]
}
```

```json
{
  "cardId": "game_dialogue_action_choice_reaction",
  "source": "video_game_dialogue_corpus",
  "kind": "dialogue_act",
  "pattern": "короткая реплика персонажа реагирует на действие, затем другой персонаж уточняет или предлагает выбор",
  "useFor": ["sample_replies", "benchmark_dialogues", "initiative_style"]
}
```

## 13. Auto-score

Auto-score не должен решать победителя. Он нужен для:
- фильтрации пустых/сломанных вариантов;
- подсветки проблем ревьюеру;
- последующего анализа корреляции с human votes.

Добавить проверки:
- `generic_world`
- `no_concrete_place`
- `no_causal_link`
- `visual_lore_mismatch`
- `too_many_proper_names`
- `event_log_lore`
- `no_open_story_seed`
- `assistant_like_reply`
- `reply_ignores_question`
- `reply_no_lore_anchor`
- `reply_too_long`
- `reply_repeats_catchphrase`

Auto-score должен сохраняться рядом с candidate, но UI не должен сортировать candidates по score до голосования, чтобы не подсказывать ревьюеру ответ.

## 14. Аналитика после разметки

Нужен offline summary endpoint или script:

```bash
python backend/scripts/analyze_calibration_votes.py
```

Вывод:
- win rate по prompt variant;
- win rate по task type;
- частота positive/negative tags;
- auto-score correlation with human winner;
- топ-10 winning candidates;
- топ-10 rejected candidates;
- suggested prompt changes.

На первом этапе достаточно JSON/Markdown отчета.

## 15. Использование результатов

После 100+ votes:
- выбрать prompt variants с лучшим win rate;
- обновить `build_character_bible_prompt`;
- расширить `quality.py` флагами, которые часто встречались в negative tags;
- добавить 10-20 winning examples как internal regression fixtures;
- обновить `evaluate_pet_dialogues.py`, чтобы прогонять новые benchmark descriptions.

После 500+ votes:
- собрать preference dataset;
- рассмотреть reranker: сгенерировать 3 варианта, выбрать лучший через lightweight scoring prompt или preference model;
- рассмотреть fine-tuning только если prompt/rerank перестали давать улучшения.

## 16. Acceptance criteria MVP

- Админ может открыть `/admin/calibration-lab` локально.
- Админ может создать batch минимум из 5 full-character задач.
- Для каждого задания показываются 2 candidates.
- Админ может выбрать победителя, tie, reject all или skip.
- Админ может добавить теги и note.
- Vote сохраняется в `backend/data/calibration/votes.jsonl`.
- После refresh страницы уже размеченные задачи не предлагаются как next.
- Export votes возвращает корректный JSONL.
- Export winners возвращает только candidates, которые победили в `outcome=winner`.
- Все endpoints защищены тем же localhost/token gate, что и generation lab.
- Тесты покрывают API create run, get next task, save vote, export votes.

## 17. Открытые вопросы

- Нужен ли один reviewerId `local` или сразу поле для нескольких ревьюеров?
- Сравниваем ли 2 или 3 candidates по умолчанию?
- Нужно ли показывать auto-score до выбора или только после сохранения vote?
- Делаем ли первый импорт TinyStories/VideoGameDialogueCorpus сразу или начинаем с hand-written reference cards?
- Храним ли generated tasks долго или чистим старые run директории вручную?
- Нужен ли отдельный режим разметки только одной реплики без полного characterBible?


# Story / Reply Generation Refactor Impact

Дата: 2026-07-08

Цель: упростить и стабилизировать генерацию историй и реплик так, чтобы модель меньше повторяла одни и те же факты, темы, истории и idle-реплики.

## Вывод

Текущую архитектуру не стоит ломать. В коде уже есть правильная база: источники контекста выбираются через routing/context assembly, а не все пулы всегда вставляются в prompt.

Основные повторы сейчас выглядят не как проблема "нужен новый RAG", а как сумма более простых причин:

- anti-repeat для idle есть в plumbing, но выключен конфигом;
- `/story` prompt/config склоняет модель к attack/robbery;
- retrieval story bricks детерминированный и не знает, что уже недавно использовалось;
- user memory имеет usage state, а world/story bricks не имеют;
- часть prompt templates смешивает задачу, стиль, контекст и ограничения в один текстовый блок.

Уверенность: 85%.

## Impact Score

Шкала:

- `10`: сильный эффект на повторы, малая/средняя сложность, низкий риск.
- `7-9`: заметный эффект, нужна аккуратная интеграция.
- `4-6`: полезно, но эффект вторичный или зависит от предыдущих правок.
- `1-3`: косметика или отложенная оптимизация.

## Приоритеты

| Rank | Правка | Impact | Confidence | Effort | Риск |
| --- | --- | ---: | ---: | --- | --- |
| 1 | Подключить `recentAmbientReplies` к idle prompt через `{recent_replies}` | 10 | 95% | S | Low |
| 2 | Убрать фиксированный уклон `/story` в `attack/robbery`, ввести event palette + cooldown | 9 | 90% | M | Medium |
| 3 | Добавить novelty/cooldown penalty для `story_library` bricks | 8 | 85% | M | Medium |
| 4 | Завести usage state для world/story bricks, аналогично user memory | 8 | 85% | M/L | Medium |
| 5 | Разделить anti-repeat events и source facts в prompt/context | 7 | 70% | M | Medium |
| 6 | Добавить confidence threshold для lite fact extraction | 7 | 75% | M | Medium |
| 7 | Нормализовать prompt templates: task/context/constraints/examples | 6 | 85% | S/M | Low |
| 8 | Добавить regression/eval набор на повторы | 6 | 80% | M | Low |
| 9 | Добавить controlled diversity среди близких retrieval scores | 5 | 80% | S/M | Medium |

## 1. Idle Anti-Repeat

Проблема: idle может повторять одну и ту же тему/формулировку.

Причина из кода:

- frontend передает `recentAmbientReplies`;
- backend умеет подставлять `{recent_replies}`;
- в `backend/data/speech_runtime.json` idle prompt сейчас не содержит `{recent_replies}`.

Правка:

- добавить `{recent_replies}` в `surfacePrompts.idle`;
- оставить recent replies только как anti-repeat, не как источник фактов.

Файлы:

- `backend/data/speech_runtime.json`
- `backend/tests/test_chat_service.py`

Impact: 10/10.

Почему высоко: plumbing уже есть, правка маленькая, эффект прямой.

## 2. Event Palette Для `/story`

Проблема: фоновые истории тяготеют к нападениям/ограблениям.

Причина из кода:

- `backgroundStory.defaultEventType` = `attack`;
- `backgroundStory.userTemplate` явно предлагает внезапное нападение или ограбление.

Правка:

- заменить фиксированный default на palette: `discovery`, `mishap`, `meeting`, `craft`, `dream`, `weather`, `threat`, `gift`, `choice`, `home`;
- выбирать event type до prompt;
- учитывать `recentStoryEvents` и не выбирать недавно использованный тип/паттерн;
- оставить `attack` как один из редких вариантов, не default.

Файлы:

- `backend/data/speech_runtime.json`
- `backend/app/services/background_story_service.py`
- `backend/tests/test_background_story_service.py`
- возможно `frontend/src/components/admin/SpeechAdmin.tsx`

Impact: 9/10.

Риск: нужно не сломать текущие тесты и admin UI, где ожидается `attack`.

## 3. Novelty/Cooldown Для Story Bricks

Проблема: один и тот же world/story brick может снова и снова побеждать в retrieval.

Причина из кода:

- `search_story_library` ранжирует по score/sort;
- нет recent usage penalty;
- нет cooldown;
- нет per-pet usage state для story bricks.

Правка:

- добавить optional `recent_brick_ids` / `used_brick_ids` в retrieval path;
- снижать score недавно использованных bricks;
- не запрещать полностью, а мягко штрафовать, чтобы важные факты могли вернуться при высокой релевантности.

Файлы:

- `backend/app/services/story_library.py`
- `backend/app/services/context_assembler.py`
- `backend/tests/test_chat_service.py`

Impact: 8/10.

Риск: слишком сильный penalty может ухудшить factual consistency. Нужен soft penalty, не hard ban.

## 4. Usage State Для World/Story RAG

Проблема: user memory уже знает `lastMentionedAt` / `mentionCount`, а story/world facts нет.

Причина из кода:

- `frontend/src/lib/localPetMemoryStorage.ts` хранит usage для user memories;
- story bricks не имеют аналогичного accounting;
- backend retrieval не получает "что уже использовали недавно".

Правка:

- завести компактный per-pet state:
  - `recentStoryBrickIds`;
  - `storyBrickUsage: { id, lastUsedAt, useCount }`;
- обновлять state после reply/story generation;
- передавать короткий recent список в backend.

Файлы:

- `frontend/src/lib/localPetMemoryTypes.ts`
- `frontend/src/lib/localPetMemoryStorage.ts`
- `frontend/src/components/PetDashboard.tsx`
- `backend/app/schemas.py`
- `backend/app/services/context_assembler.py`

Impact: 8/10.

Риск: больше surface area, чем у первой правки. Делать после prompt/config fixes.

## 5. Anti-Repeat Events Не Должны Становиться Source Facts

Проблема: список прошлых историй нужен как запрет на повтор, но модель может воспринять его как материал для продолжения.

Причина из кода:

- `/story` уже получает `recentStoryEvents` через `ANTI_REPEAT`;
- есть риск пересечения с character/profile context, если похожие события попадают в общий контекст.

Правка:

- держать generated episodes отдельно от durable facts;
- в prompt явно маркировать recent events как `DO_NOT_REPEAT`, не `CONTEXT`;
- не сохранять одноразовые эпизоды как RAG bricks.

Файлы:

- `backend/app/services/background_story_service.py`
- `backend/app/services/pet_reply_engine/lite_generator.py`
- `backend/app/services/telegram_push_service.py`

Impact: 7/10.

Уверенность ниже, потому что часть защиты уже есть в текущей реализации.

## 6. Confidence Threshold Для Lite Facts

Проблема: случайные детали из обычной реплики могут закрепиться как устойчивый факт.

Причина из кода:

- background aftermath extraction уже использует confidence threshold;
- lite fact extraction не выглядит симметрично защищенным confidence threshold.

Правка:

- добавить `confidence` в lite extraction schema;
- сохранять только факты выше порога;
- для низкой confidence фактов не писать overlay.

Файлы:

- `backend/app/services/pet_reply_engine/lite_generator.py`
- `backend/app/services/lite_overlay.py`
- `backend/tests/test_chat_service.py`

Impact: 7/10.

Риск: можно потерять часть полезной персонализации, если порог слишком высокий.

## 7. Prompt Template Cleanup

Проблема: часть шаблонов одновременно задает стиль, задачу, примеры событий и ограничения. Это усиливает mode collapse.

Рекомендованный формат:

```text
ROLE
{role}

TASK
{task}

STYLE
{voice}

CONTEXT
{selected_context}

DO_NOT_REPEAT
{recent_items}

RULES
- {rule_1}
- {rule_2}
```

Правка:

- привести idle/background/story prompts к одинаковой структуре;
- убрать из prompt examples, которые слишком сильно якорят модель на `attack/robbery`;
- negative constraints формулировать как запрет на повтор конкретных тем, а не как длинный список "не делай вообще".

Файлы:

- `backend/data/speech_runtime.json`
- `backend/app/services/pet_reply_engine/speech_runtime.py`

Impact: 6/10.

Почему не выше: без usage/cooldown state шаблон сам по себе не решит deterministic retrieval.

## 8. Regression/Eval На Повторы

Проблема: сейчас легко вернуть повторы обратно изменением prompt/config.

Правка:

- добавить тесты:
  - idle prompt содержит `recent_replies`;
  - background story prompt получает `ANTI_REPEAT`;
  - event palette не выбирает один и тот же тип N раз подряд;
  - story retrieval штрафует recent brick при близком score;
  - durable facts не сохраняют одноразовый episode.

Файлы:

- `backend/tests/test_chat_service.py`
- `backend/tests/test_background_story_service.py`

Impact: 6/10.

## 9. Controlled Diversity В Retrieval

Проблема: полностью детерминированный sort делает повторы стабильными.

Правка:

- среди bricks с близким score выбирать разнообразно;
- разнообразие должно быть seeded/stable per request, чтобы тесты были воспроизводимыми;
- не использовать random как замену relevance.

Файлы:

- `backend/app/services/story_library.py`

Impact: 5/10.

Риск: если сделать слишком рандомно, модель начнет получать менее релевантный lore.

## Best Practices Из Внешних Источников

Общий паттерн из OpenAI, Anthropic, Google Gemini, LangChain/LangGraph, LlamaIndex и Mem0:

- отделять stable memory от retrieved context;
- доставать контекст just-in-time, не вставлять все пулы;
- хранить structured memory, а не только prose summary;
- иметь usage/recency/importance/confidence;
- prompt templates делать короткими, явными и переиспользуемыми;
- anti-repeat держать как constraint, не как source facts;
- долговременную память писать через extraction + фильтры, а не напрямую из каждой генерации.

Ссылки:

- OpenAI prompt engineering: https://help.openai.com/en/articles/6654000-best-practices-for-prompt-engineering-with-the-openai-api
- OpenAI context personalization: https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization
- Anthropic prompt engineering: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Anthropic context engineering: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Google Gemini prompting: https://ai.google.dev/gemini-api/docs/prompting-strategies
- LangChain long-term memory: https://docs.langchain.com/oss/python/langchain/long-term-memory
- LlamaIndex memory blocks: https://developers.llamaindex.ai/typescript/framework/modules/data/memory/
- Mem0 AI companion cookbook: https://docs.mem0.ai/cookbooks/essentials/building-ai-companion
- Generative Agents paper: https://arxiv.org/abs/2304.03442

## Suggested Order

1. Fix idle prompt `{recent_replies}`.
2. Replace `/story` attack default with event palette.
3. Add soft novelty penalty for story library retrieval.
4. Add per-pet story/world usage state.
5. Add lite fact confidence threshold.
6. Normalize prompt templates.
7. Add repeat regression tests around each layer.


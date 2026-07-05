# ТЗ: речь питомца через Creature phrases dataset, retrieval и накопление канона

Дата: 2026-07-05

## 1. Цель

Сделать речь питомцев более естественной и управляемой:

- каждый ответ должен опираться на подходящие речевые примеры, а не только на
  абстрактную инструкцию "говори живо";
- питомец отвечает прямо на сообщение пользователя и не уходит в несвязанную
  отсебятину;
- питомец сохраняет инициативу: может добавить желание, наблюдение, маленькое
  действие, вопрос или новую деталь мира;
- новая деталь мира не должна сразу становиться каноном, если она может сломать
  Библию персонажа;
- удачные речевые находки и безопасные мелкие детали постепенно пополняют
  память, style-memory и world building.

Это не замена `MESSAGE_EXAMPLES_AGE_STYLE_TZ.md`, а следующий слой поверх него.
Существующее ТЗ уже описывает, как использовать `creature_phrases_dataset.json`
для возраста. Этот документ описывает, как выбирать, адаптировать, трассировать
и сохранять фразы/новые факты в runtime.

## 2. Диагноз текущего состояния

### Что уже есть

- Runtime dataset:
  `backend/data/age_speech_examples/creature_phrases_dataset.json`.
- В датасете 3 стадии:
  - `baby`: 10 категорий, 100 фраз, 10 speech rules;
  - `teen`: 10 категорий, 100 фраз, 9 speech rules;
  - `adult`: 10 категорий, 100 фраз, 11 speech rules.
- Категории одинаковые для всех стадий:
  `greeting`, `happy`, `sad`, `scared`, `curious`, `hungry`,
  `tired`, `playful`, `angry`, `loving`.
- Модуль `age_message_examples` уже:
  - грузит датасет;
  - выбирает категории по mood/action/intent;
  - подставляет placeholders;
  - вставляет до 12 examples в prompt;
  - использует фразы как fallback.
- Prompt builder уже включает:
  - Character Profile V2;
  - референсы голоса;
  - dialogue moves;
  - reference cards;
  - lore;
  - memory;
  - age message examples;
  - proactivity rules.
- Memory resolver уже умеет:
  - сохранять canon, relationship, threads, reflections, active goals;
  - отклонять технические, чувствительные и некоторые конфликтующие факты;
  - вытаскивать implicit friend fact из ответа на вопрос про друга;
  - хранить rejected candidates.

### Чего не хватает

- Нет phrase retrieval как отдельного шага. Сейчас examples выбираются грубо по
  категории, а не как "лучшие речевые якоря для этого вопроса".
- Нет `phraseTrace`: невозможно увидеть, какие фразы были выбраны, почему и как
  они повлияли на ответ.
- Нет intent-level разметки в самом датасете: category `curious` не говорит,
  подходит ли фраза для `why`, `status`, `answer_lore`, `care` или `boundary`.
- Нет понятия adaptation distance: модель может слишком далеко уйти от якоря,
  либо скопировать пример слишком буквально.
- Нет отдельной памяти для удачных речевых паттернов. `voice_fact` есть, но нет
  механизма "эта фразовая форма хорошо сработала для этого питомца".
- Нет статуса `pet_generated_fact` / `draft_fact` как промежуточного слоя.
  Сейчас `pet_canon_fact` в модели памяти фактически маппится в `world_fact`.
- Нет явного разделения:
  - сырое событие речи;
  - кандидат на новый факт;
  - подтвержденный canon fact;
  - style-memory;
  - rejected pattern.
- В system prompt есть сильная общая инструкция "прояви инициативу" даже тогда,
  когда нужно точнее контролировать, сколько нового может добавить питомец.

## 3. Ответ на вопрос про размер датасета

Для age-style слоя датасет достаточный для MVP: 300 фраз и 30 правил позволяют
заметно отличать `baby`, `teen` и `adult`.

Для fine-tuning или полноценного conversational retrieval датасет маленький:

- нет пар `user_turn -> pet_reply`;
- нет контекста предыдущей реплики;
- нет разметки dialogue act;
- нет привязки к конкретной Библии персонажа;
- нет negative examples;
- нет оценок качества адаптации;
- нет данных о том, какие ответы пользователь принял или отверг.

Решение: не fine-tune на этом наборе сейчас. Сначала сделать RAG/style-anchor
pipeline и начать собирать собственные принятые ответы с трассировкой. Fine-tune
имеет смысл только после накопления большого корпуса реальных turn-level
примеров, где для каждого ответа известны intent, выбранные anchors, Библия,
память, итоговый ответ и outcome.

## 4. Продуктовый принцип

Питомец не должен выбирать готовую фразу из датасета как canned response.

Правильная модель:

1. Понять, что пользователь сейчас делает: спрашивает статус, лор, причину,
   предпочтение, просит заботу, продолжает тему, ставит границу.
2. Найти 2-5 речевых anchors: фразы из dataset/reference/style-memory, которые
   совпадают по стадии, эмоции, intent и ритму.
3. Выбрать один основной anchor и 1-2 дополнительных style hints.
4. Сгенерировать прямой ответ на пользователя, используя anchor как форму:
   длина, темп, эмоциональный жест, тип инициативы.
5. Новые факты, которые появились в ответе, выделить отдельно.
6. Сохранить событие речи всегда, но продвигать новые факты в канон только по
   правилам.

## 5. Новая runtime-схема

### 5.1 PhraseExample

Единый нормализованный формат для фраз из `creature_phrases_dataset`,
reference cards, imported dialogues и style-memory:

```json
{
  "id": "creature:baby:greeting:000",
  "sourceFamily": "creature_phrases_dataset",
  "sourceUrl": "local://backend/data/age_speech_examples/creature_phrases_dataset.json",
  "licenseNote": "internal/generated dataset",
  "locale": "ru",
  "stage": "baby",
  "category": "greeting",
  "dialogueActs": ["smalltalk", "status"],
  "userIntents": ["greeting", "status"],
  "emotionTags": ["warm", "excited"],
  "proactivityKinds": ["ask_user", "share_observation"],
  "text": "Приветик! Ты пришёл!",
  "placeholders": [],
  "styleFeatures": {
    "wordCount": 3,
    "sentenceCount": 2,
    "hasQuestion": false,
    "hasSound": false,
    "register": "baby",
    "rhythm": "short_exclamatory"
  },
  "compatibilityTags": ["generic_body"],
  "forbiddenTransfers": ["new_home", "new_friend", "new_species"],
  "useFor": ["style_anchor", "fallback"],
  "quality": {
    "manualScore": null,
    "timesUsed": 0,
    "timesAccepted": 0,
    "lastUsedAt": null
  }
}
```

### 5.2 PhraseCandidate

Runtime-кандидат после retrieval:

```json
{
  "phraseId": "creature:baby:greeting:000",
  "score": 0.82,
  "scoreReasons": ["stage_match", "intent_match", "mood_match"],
  "rejectionReasons": [],
  "adaptationMode": "rhythm_only",
  "allowedTransfers": ["emotion", "length", "question_shape"],
  "blockedTransfers": ["facts", "body_parts", "home", "friend_names"]
}
```

### 5.3 PhraseTrace

Trace должен попадать в debug, но не в пользовательский ответ:

```json
{
  "selected": [
    {
      "phraseId": "creature:baby:greeting:000",
      "category": "greeting",
      "dialogueActs": ["smalltalk"],
      "adaptationMode": "rhythm_only",
      "score": 0.82
    }
  ],
  "rejected": [
    {
      "phraseId": "creature:adult:loving:004",
      "reason": "stage_mismatch"
    }
  ],
  "copyRisk": 0.12,
  "adaptationDistance": 0.58
}
```

### 5.4 GeneratedFactCandidate

Новые факты из ответа питомца:

```json
{
  "id": "draftfact-...",
  "type": "pet_generated_fact",
  "scope": "world|home|friend|habit|preference|voice|relationship|thread",
  "text": "У питомца есть друг Кап, капля росы, которая будит его утром.",
  "source": "model_reply",
  "sourceMessageId": "message-id",
  "sourceSpan": "мой друг Кап будит меня утром",
  "confidence": 0.58,
  "importance": 0.62,
  "status": "draft",
  "promotionPolicy": "user_confirmation_or_repetition",
  "conflictReasons": [],
  "createdAt": "..."
}
```

### 5.5 StyleMemory

Отдельная память о голосе конкретного питомца:

```json
{
  "id": "stylemem-...",
  "type": "style_memory",
  "text": "На заботу отвечает коротким шорохом, затем добавляет телесную деталь.",
  "sourcePhraseIds": ["creature:baby:loving:002"],
  "evidenceMessageIds": ["..."],
  "stage": "baby",
  "intent": "care",
  "confidence": 0.65,
  "useCount": 0,
  "createdAt": "..."
}
```

## 6. Retrieval pipeline

### 6.1 Входы

Для каждого ответа retrieval получает:

- `userText`;
- detected intent;
- current stage;
- mood/stat/action;
- recent messages;
- Character Bible / effective Bible;
- relevant memory;
- rejected patterns;
- последние использованные phrase ids;
- prompt layers.

### 6.2 Hard filters

Перед scoring отбрасывать:

- фразы другой stage, если `ageStyle=true`;
- фразы с неподходящим locale;
- фразы, которые требуют body part, отсутствующий у питомца;
- фразы, которые переносят чужой дом, друга, семью, вид или прошлое;
- фразы, недавно использованные тем же питомцем;
- фразы из rejected pattern;
- фразы, которые конфликтуют с user boundary, например "без вопросов".

Если `ageStyle=false`, stage filter выключается, но dataset-фразы не должны
вставлять baby/teen/adult манеру.

### 6.3 Scoring

MVP может быть без embeddings: BM25 + rule score. Embeddings можно добавить
позже как второй сигнал.

Рекомендуемая формула MVP:

```text
score =
  0.30 intent_match +
  0.20 category_match +
  0.15 mood_action_match +
  0.15 character_compatibility +
  0.10 lexical_overlap +
  0.10 diversity_bonus
  - repetition_penalty
  - conflict_penalty
```

Минимальные thresholds:

- `score >= 0.55`: можно использовать как style anchor;
- `score >= 0.72`: можно использовать как основной anchor;
- `score < 0.55`: не вставлять в prompt, только fallback при полном отсутствии
  кандидатов.

### 6.4 Количество anchors

В prompt не должен попадать весь набор examples.

Для одного ответа:

- 1 primary anchor;
- 1-2 secondary anchors;
- 1 negative/rejected pattern, если есть риск повторяемого мотива;
- максимум 5 фразовых строк суммарно.

Это заменяет текущую практику "до 12 examples" в prompt после внедрения phrase
retrieval. На ранней фазе можно оставить 8-12 examples, но debug должен показать,
что это legacy compact block.

## 7. Adaptation rules

### 7.1 Что можно переносить из фразы

Можно переносить:

- ритм;
- длину;
- эмоциональный жест;
- форму вопроса;
- степень инициативы;
- тип действия: принять заботу, предложить игру, попросить еду, удивиться.

Нельзя переносить без подтверждения Библией:

- дом;
- семью;
- друзей;
- имена;
- вид;
- части тела;
- травмы и крупные события;
- постоянные страхи;
- большие желания;
- "ты стал моей семьей" и похожие сильные отношения, если relationship memory
  это не подтверждает.

### 7.2 Adaptation modes

- `literal_safe`: можно использовать почти дословно; только для fallback и
  простых реакций без фактов.
- `slot_fill`: заменить placeholders из Character Bible.
- `rhythm_only`: взять только длину/темп/интонацию.
- `dialogue_act_only`: взять структуру ответа, текст полностью новый.
- `avoid`: не использовать, но передать как negative example.

По умолчанию для `creature_phrases_dataset` использовать `rhythm_only` или
`dialogue_act_only`. `literal_safe` разрешен только fallback-слою.

### 7.3 Copy guard

Validator должен отклонять:

- дословное копирование нескольких examples подряд;
- перенос чужих placeholders;
- повтор одной и той же фразы в последних 4 pet replies;
- совпадение normalized reply с primary anchor выше 0.85, если это не fallback.

## 8. Prompt builder: новая структура

Добавить секцию `Speech anchors` после Character Bible и перед runtime style:

```text
Speech anchors:
- primary: creature:baby:greeting:000
  mode: rhythm_only
  source_text_do_not_copy: "Приветик! Ты пришёл!"
  use: short excited greeting, no new facts
- secondary: creature:baby:curious:004
  mode: dialogue_act_only
  use: one tiny curious follow-up if proactivity is allowed

Rules:
- answer the user first;
- use anchors as form, not facts;
- do not copy source_text literally unless fallback is active;
- do not transfer body, home, friend, family, species or past from anchors;
- if you invent a new world detail, expose it as generatedFactCandidate.
```

Существующая секция `Message examples age style` остается как fallback-compact
block до завершения внедрения. После фазы 2 она должна стать источником
`Speech anchors`, а не самостоятельным большим блоком examples.

## 9. Output schema

Текущий JSON ответа нужно расширить debug/runtime полями. В пользовательский API
можно отдавать только часть полей, но модель должна возвращать структурированные
кандидаты.

```json
{
  "reply": "...",
  "moodHint": "idle|happy|hungry|sad|null",
  "proactiveIntent": null,
  "memoryCandidates": [],
  "relationshipPatch": null,
  "developmentPatch": null,
  "threadPatch": null,
  "goalPatch": null,
  "phraseTrace": {
    "primaryPhraseId": "creature:baby:greeting:000",
    "usedPhraseIds": ["creature:baby:greeting:000"],
    "adaptationMode": "rhythm_only",
    "copiedLiteral": false
  },
  "generatedFactCandidates": [],
  "styleMemoryCandidates": []
}
```

Если не хочется расширять schema модели сразу, `phraseTrace` можно собрать
детерминированно на backend до вызова модели и сохранить в debug отдельно.
Но `generatedFactCandidates` и `styleMemoryCandidates` лучше получать из модели
или отдельного extractor, потому что они зависят от итогового reply.

## 10. Правила инициативы

Инициатива обязательна как живость, но не обязана быть вопросом.

Разрешенные формы инициативы:

- маленькое наблюдение;
- короткое желание;
- предложение действия;
- один узкий вопрос;
- продолжение открытой темы;
- новая микро-деталь мира.

Запрещено:

- каждый раз заканчивать вопросом;
- начинать новую несвязанную тему;
- добавлять крупный лор без повода;
- просить заботу в каждом сообщении;
- противоречить просьбе "без вопросов";
- превращать ответ в интервью.

Добавить runtime-поле `proactivityBudget`:

```json
{
  "allowed": true,
  "maxQuestions": 1,
  "allowNewWorldDetail": true,
  "maxNewWorldFacts": 1,
  "preferredKinds": ["share_observation", "offer_action"]
}
```

При `boundary`, `memory_control`, "коротко", "без вопросов":

- `maxQuestions=0`;
- `allowNewWorldDetail=false`, если пользователь не спрашивает лор;
- `preferredKinds=["share_observation"]`.

## 11. Сохранение нового контента

### 11.1 Главное правило

Сохраняем все как событие, но не все как канон.

Всегда сохранять:

- user message event;
- pet reply event;
- phrase ids / phraseTrace;
- generated fact candidates;
- rejected candidates.

В канон продвигать только:

- подтвержденные пользователем факты;
- факты, прямо извлеченные из Character Bible;
- безопасные мелкие детали, которые не меняют дом/мир/вид/близких;
- повторенные и не конфликтующие детали после 2-3 использований;
- факты, которые были ответом на прямой lore-question и не конфликтуют с
  существующим canon.

### 11.2 Promotion policy

Статусы:

- `event_only`: только в event log;
- `draft`: кандидат, виден в debug/admin;
- `accepted_soft`: можно использовать в retrieval с низким весом;
- `canon`: закрепленный факт;
- `rejected`: нельзя использовать, добавить в rejected pattern;
- `needs_user_confirmation`: нужно спросить/дождаться подтверждения.

Правила продвижения:

| Тип | Стартовый статус | Как продвинуть |
| --- | --- | --- |
| user_fact | `accepted_soft` | сразу, если не sensitive |
| relationship_event | `accepted_soft` | сразу, если событие реально произошло в диалоге |
| pet_generated_fact small | `draft` | 2 повторения или admin accept |
| friend/family/new named entity | `needs_user_confirmation` | user confirms или admin accept |
| home/world/species change | `rejected` | только ручное изменение Библии |
| voice/style habit | `draft` | 3 удачных ответа в том же стиле |
| open_thread | `accepted_soft` | сразу, если есть follow-up |

### 11.3 Что считать "маленькой деталью"

Можно автоматически оставлять в `draft`:

- запах;
- маленький предмет;
- кличка предмета;
- привычка;
- локальное место внутри уже существующего дома;
- бытовая причина;
- короткий эпизод без больших последствий.

Нельзя автоматически канонизировать:

- новый дом;
- новый мир;
- новый вид;
- родственника;
- лучшего друга с именем;
- смерть, войну, катастрофу, спасение мира;
- травматичный backstory;
- постоянную любовь/семью с пользователем без отношения в памяти.

## 12. Character Bible и world building

Character Bible остается главным источником фактов.

Добавить в effective/local memory отдельный раздел:

```json
{
  "worldBuilding": {
    "draftFacts": [],
    "acceptedSoftFacts": [],
    "canonFacts": [],
    "rejectedPatterns": []
  },
  "voiceLearning": {
    "styleMemories": [],
    "successfulAnchors": [],
    "avoidAnchors": []
  }
}
```

При сборке prompt:

1. Character Bible.
2. Canon memory.
3. Accepted soft facts, только если релевантны.
4. Draft facts, только для debug/admin или если пользователь прямо продолжает эту тему.
5. Rejected patterns как negative constraints.
6. Style memories как voice hints.

Draft facts не должны попадать в обычный prompt как канон.

## 13. Phrase library growth

Когда ответ прошел validator и не был fallback:

1. Сохранять `PhraseUsageEvent`:
   - selected phrase ids;
   - reply;
   - intent;
   - stage;
   - mood;
   - whether user continued positively;
   - validator flags;
   - generated facts count.
2. Если ответ был удачным:
   - выделить style pattern;
   - добавить `styleMemoryCandidate`;
   - обновить `timesUsed/timesAccepted` у PhraseExample.
3. Если ответ был плохим:
   - добавить rejected pattern;
   - снизить вес anchor для похожих contexts.

MVP outcome без сложной аналитики:

- positive: пользователь продолжил тему, погладил/покормил/поиграл, или не
  исправил питомца в ближайшие 2 сообщения;
- negative: пользователь сказал "не выдумывай", "не так", "ты придумал",
  "не задавай вопросы", "короче", "это не правда".

## 14. UI/debug требования

В dev/debug режиме показывать:

- detected intent;
- prompt layers;
- selected phrase anchors;
- rejected phrase candidates;
- adaptation mode;
- copy risk;
- generated fact candidates;
- какие факты сохранены как draft/accepted/canon/rejected;
- rejected patterns;
- proactivity gate result.

Admin actions:

- `Закрепить в Библию`;
- `Оставить как draft`;
- `Отклонить факт`;
- `Запретить мотив`;
- `Повысить вес фразы`;
- `Понизить вес фразы`;
- `Добавить как style-memory`.

## 15. План внедрения

### Фаза 0: аудит и трассировка, 0.5-1 день

- Добавить debug `phraseTrace` без изменения генерации.
- Логировать выбранные examples из текущего `age_message_examples`.
- В debug показывать, какие dataset categories попали в prompt.
- Добавить счетчики: сколько examples в prompt, какие phrase ids, было ли
  дословное копирование.

Definition of Done:

- в debug видно, что именно из `creature_phrases_dataset` повлияло на ответ;
- можно объяснить плохую реплику через выбранные anchors или отсутствие anchors.

### Фаза 1: нормализация phrase library, 1-2 дня

- Превратить dataset в список `PhraseExample`.
- Добавить deterministic ids.
- Автоматически рассчитать `styleFeatures`.
- Добавить первичную разметку `dialogueActs` из category/action/intent mapping.
- Добавить hard filters и repetition guard.

Definition of Done:

- retrieval возвращает 1-5 кандидатов с score/reasons;
- выбранные кандидаты не повторяют последние pet replies;
- неподходящие placeholders не проходят hard filters.

### Фаза 2: Speech anchors в prompt, 1-2 дня

- Добавить `Speech anchors` section.
- Уменьшить большой examples block до fallback/legacy.
- Передавать adaptation mode и blocked transfers.
- Добавить copy guard в validator.

Definition of Done:

- питомец отвечает по форме выбранной фразы, но не копирует ее;
- на один и тот же вопрос разные питомцы отвечают разными деталями из Библии;
- baby/teen/adult сохраняют разную форму.

### Фаза 3: generated facts и style-memory, 2-4 дня

- Добавить `generatedFactCandidates`.
- Добавить `styleMemoryCandidates`.
- Разделить `draft`, `accepted_soft`, `canon`, `rejected`.
- Не маппить model-generated `pet_canon_fact` напрямую в `world_fact`.
- Добавить promotion rules.

Definition of Done:

- новая мелкая деталь сохраняется как draft/accepted_soft, а не сразу как canon;
- крупные изменения мира отклоняются или требуют подтверждения;
- удачные речевые привычки начинают возвращаться в prompt как style memory.

### Фаза 4: ручная калибровка, 1 день

Прогнать 10-20 питомцев по набору сообщений:

- "привет";
- "как ты?";
- "что ты любишь?";
- "где ты живешь?";
- "почему?";
- "кто твой друг?";
- "расскажи подробнее";
- "не задавай вопросов";
- "поиграем?";
- "я тебя покормлю";
- "не выдумывай".

Смотреть:

- прямоту ответа;
- связь с Библией;
- естественность инициативы;
- количество новых фактов;
- повторяемые мотивы;
- копирование examples;
- сохранение/отклонение memory candidates.

## 16. Тесты

### Phrase retrieval tests

- stage hard filter работает для `baby`, `teen`, `adult`;
- `ageStyle=false` не вставляет возрастные anchors;
- `boundary` отбрасывает anchors с вопросом;
- повтор последних phrase ids штрафуется;
- placeholders без данных не создают чужую анатомию;
- `scoreReasons` и `rejectionReasons` заполняются.

### Prompt tests

- prompt содержит `Speech anchors`;
- prompt содержит adaptation mode;
- prompt запрещает перенос фактов из anchors;
- prompt не содержит больше 5 phrase anchors;
- legacy `Message examples age style` можно выключить;
- debug показывает phrase ids.

### Validator tests

- дословное копирование primary anchor отклоняется, если это не fallback;
- перенос чужого друга/дома из фразы отклоняется;
- `baby` может использовать короткие звуки;
- `adult` не принимает baby-coded speech;
- ответ с двумя несвязанными новыми facts отклоняется или режется.

### Memory tests

- `pet_generated_fact` сохраняется как `draft`;
- friend/family named fact требует confirmation или admin accept;
- home/species/world change отклоняется;
- маленький предмет в уже существующем доме может стать `accepted_soft`;
- style-memory появляется только после нескольких evidence events;
- rejected pattern попадает в prompt как negative constraint.

### Regression tests

- текущие prompt layers продолжают отключать свои секции;
- fallback работает без OpenAI;
- persisted chat не теряет memory patch;
- local chat и persisted chat используют одинаковые promotion rules.

## 17. Acceptance criteria

Готово, когда:

- каждый ответ имеет trace выбранных phrase anchors в debug;
- `creature_phrases_dataset` используется как retrieval/style-anchor corpus, а
  не только как общий examples block;
- модель не копирует фразы дословно в обычном режиме;
- фразы адаптируются под Character Bible и не переносят чужие факты;
- питомец отвечает на вопрос пользователя сначала, а инициативу добавляет потом;
- новые детали мира сохраняются как draft/accepted/canon по правилам;
- крупные выдумки не попадают в канон автоматически;
- удачные речевые паттерны пополняют style-memory конкретного питомца;
- manual calibration показывает меньше несвязанной отсебятины и меньше повторов.

## 18. Главное решение

Не делать fine-tuning сейчас и не превращать датасет в словарь готовых ответов.

Правильный следующий шаг:

1. Retrieval по фразам.
2. Speech anchors с trace.
3. Controlled adaptation.
4. Generated facts как draft, а не canon.
5. Style-memory на основе удачных ответов.

Так питомец сможет говорить инициативно и придумывать новое, но система перестанет
закреплять случайные галлюцинации как правду.

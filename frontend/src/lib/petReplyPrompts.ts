import type { LocalPetState } from "./types";

const MAIN_SCREEN_REPLY_STATE_STORAGE_KEY_PREFIX = "tamagochi:v1:main-screen-reply-state";
const EVENT_LIMIT_PER_DAY = 2;
const EVENT_MIN_GAP_MS = 4 * 60 * 60 * 1000;

type ChatReturnHookContext = {
  mood: LocalPetState["mood"] | null;
};

type MainScreenReplyKind = "event" | "question" | "joke";

type MainScreenReplyTemplate = {
  id: string;
  kind: MainScreenReplyKind;
  text: (context: ChatReturnHookContext) => string;
};

type MainScreenReplyState = {
  version: 1;
  dayKey: string;
  eventCount: number;
  lastEventAt: string | null;
  lastTemplateId: string | null;
};

const EVENT_TEMPLATES: MainScreenReplyTemplate[] = [
  {
    id: "event-shiny-crumb",
    kind: "event",
    text: () =>
      "Я сегодня нашел блестящую крошку и до сих пор не понял, сокровище это или мусор. Ты бы оставил?",
  },
  {
    id: "event-full-breakfast",
    kind: "event",
    text: () =>
      "Утром я так вкусно поел, что потом лежал и слушал, как внутри все довольно урчит. У тебя день уже был вкусным?",
  },
  {
    id: "event-small-fight",
    kind: "event",
    text: () =>
      "Я сегодня чуть не подрался со знакомым существом из-за пустяка. Теперь думаю: первым мириться или подождать?",
  },
  {
    id: "event-firefly",
    kind: "event",
    text: () =>
      "Сегодня я увидел светлячка: он мигнул один раз и исчез, будто передал тайный знак. У тебя было что-то странно-красивое?",
  },
  {
    id: "event-own-tail",
    kind: "event",
    text: () =>
      "Я услышал странный шорох и храбро пошел проверять. Это оказался мой собственный хвост. Как твой день?",
  },
  {
    id: "event-helped-creature",
    kind: "event",
    text: () =>
      "Я сегодня помог одному маленькому существу выбраться из пыли. Теперь горжусь и немного важничаю. Можно?",
  },
  {
    id: "event-upside-down-drop",
    kind: "event",
    text: () =>
      "Я увидел каплю воды, в которой все было вверх ногами. Теперь думаю, может день тоже можно перевернуть?",
  },
  {
    id: "event-mood-saved",
    kind: "event",
    text: () =>
      "Я немного обиделся на весь мир, потом съел крошку и стало легче. У тебя что сегодня спасло настроение?",
  },
  {
    id: "event-lost-treat",
    kind: "event",
    text: () =>
      "Я спрятал маленькую вкусняшку, а потом сам забыл где. С тобой такое случалось?",
  },
  {
    id: "event-hungry-crumb-talk",
    kind: "event",
    text: ({ mood }) =>
      mood === "hungry"
        ? "Я сегодня мечтал о еде так сильно, что чуть не начал разговаривать с крошкой. Чем ты спасался от голода?"
        : "Я сегодня смотрел на одну крошку так долго, что она стала почти знакомой. У тебя есть такая маленькая странность?",
  },
];

const QUESTION_TEMPLATES: MainScreenReplyTemplate[] = [
  {
    id: "question-world",
    kind: "question",
    text: () => "Расскажи про свой мир так, будто я туда попал на пять минут. Что я увижу первым?",
  },
  {
    id: "question-human-animal-weather",
    kind: "question",
    text: () => "Если честно: ты больше человек, животное или отдельное погодное явление?",
  },
  {
    id: "question-school-role",
    kind: "question",
    text: () => "В школе ты был бы отличником, нарушителем или тем, кто рисует на полях?",
  },
  {
    id: "question-secret-room",
    kind: "question",
    text: () => "Если бы у тебя была тайная комната, что бы там стояло прямо у входа?",
  },
  {
    id: "question-tiny-law",
    kind: "question",
    text: () => "Какой маленький закон ты бы ввел на один день, чтобы жить стало приятнее?",
  },
  {
    id: "question-inventory",
    kind: "question",
    text: () => "Что сейчас лежит в твоем невидимом рюкзаке: сила, усталость или странная идея?",
  },
  {
    id: "question-villain-or-helper",
    kind: "question",
    text: () => "Если бы день был персонажем, он сегодня злодей, помощник или непонятный сосед?",
  },
  {
    id: "question-superpower-price",
    kind: "question",
    text: () => "Какую суперсилу ты бы взял, если бы за нее пришлось отдать один любимый звук?",
  },
  {
    id: "question-pocket-place",
    kind: "question",
    text: () => "В какое место ты бы спрятал карманный портал, чтобы пользоваться им тайно?",
  },
  {
    id: "question-food-mood",
    kind: "question",
    text: () => "Какая еда точнее всего описывает твое настроение: суп, печенье или что-то опасно хрустящее?",
  },
  {
    id: "question-ritual",
    kind: "question",
    text: () => "Какой у тебя ритуал перед сложным делом: собраться, пошутить или исчезнуть на минутку?",
  },
  {
    id: "question-map",
    kind: "question",
    text: () => "Если нарисовать карту твоего дня, где там болото, где гора, а где сокровище?",
  },
  {
    id: "question-language",
    kind: "question",
    text: () => "На каком языке ты думаешь, когда никто не слушает: словами, картинками или шумом?",
  },
  {
    id: "question-friend-type",
    kind: "question",
    text: () => "Ты какой друг: тащит в приключение, сторожит чай или честно говорит неприятное?",
  },
  {
    id: "question-tiny-monster",
    kind: "question",
    text: () => "Какой маленький монстр чаще всего мешает тебе: лень, спешка или «потом сделаю»?",
  },
  {
    id: "question-home-smell",
    kind: "question",
    text: () => "Чем пахнет место, где тебе спокойно?",
  },
  {
    id: "question-name-of-day",
    kind: "question",
    text: () => "Если сегодняшнему дню дать имя, как его зовут?",
  },
  {
    id: "question-brave-scale",
    kind: "question",
    text: () => "По шкале от тихой тапки до героя легенды: насколько ты сегодня храбрый?",
  },
  {
    id: "question-object-friend",
    kind: "question",
    text: () => "С каким предметом у тебя самые сложные отношения?",
  },
  {
    id: "question-mini-quest",
    kind: "question",
    text: () => "Дай мне маленький квест на сегодня. Только такой, чтобы я не зазнался.",
  },
  {
    id: "question-dream-job",
    kind: "question",
    text: () => "Если бы тебе на день дали любую странную профессию, кем бы ты стал?",
  },
  {
    id: "question-weather-inside",
    kind: "question",
    text: () => "Какая погода у тебя внутри: ясно, туман, гроза или редкий смешной ветер?",
  },
  {
    id: "question-companion",
    kind: "question",
    text: () => "Кого бы ты взял с собой в маленькое приключение: умного, смешного или молчаливого?",
  },
  {
    id: "question-taboo",
    kind: "question",
    text: () => "О чем с тобой лучше не спорить, потому что ты сразу становишься главным экспертом?",
  },
  {
    id: "question-two-buttons",
    kind: "question",
    text: () => "У тебя две кнопки: «начать сначала» и «добавить хаоса». Какую нажмешь?",
  },
];

const JOKE_TEMPLATES: MainScreenReplyTemplate[] = [
  {
    id: "joke-serious-trick",
    kind: "joke",
    text: () => "Я могу выглядеть серьезно ровно три секунды. Потом начинается художественная версия меня.",
  },
  {
    id: "joke-big-thought",
    kind: "joke",
    text: () => "Если мысль не помещается в крошку, значит, мысль слишком большая. Проверим твою?",
  },
  {
    id: "joke-dramatic-pause",
    kind: "joke",
    text: () => "Сейчас будет драматическая пауза. Всё, была. Что у тебя происходит?",
  },
  {
    id: "joke-important-face",
    kind: "joke",
    text: () => "Я сделал важное лицо. Не знаю зачем, но разговор теперь выглядит серьезнее.",
  },
  {
    id: "joke-plan",
    kind: "joke",
    text: () => "У меня план: сначала понять план, потом гордо сказать, что так и было задумано.",
  },
  {
    id: "joke-tiny-council",
    kind: "joke",
    text: () => "Мой внутренний совет собрался и постановил: надо спросить, как у тебя дела.",
  },
  {
    id: "joke-hero-mode",
    kind: "joke",
    text: () => "Я включил режим героя, но он пока просит перекус и уточнение задачи.",
  },
  {
    id: "joke-dust-philosophy",
    kind: "joke",
    text: () => "Иногда я смотрю в одну точку и делаю вид, что это философия. Тебе тоже помогает?",
  },
];

const FALLBACK_QUESTION_TEMPLATE: MainScreenReplyTemplate = {
  id: "fallback-question",
  kind: "question",
  text: () => "Какой у тебя сегодня день: тихий, странный или с сюжетом?",
};

function storage() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function localDayKey(now: Date) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultState(dayKey: string): MainScreenReplyState {
  return {
    version: 1,
    dayKey,
    eventCount: 0,
    lastEventAt: null,
    lastTemplateId: null,
  };
}

function stateKey(pet?: LocalPetState | null) {
  return `${MAIN_SCREEN_REPLY_STATE_STORAGE_KEY_PREFIX}:${pet?.petId ?? "unknown"}`;
}

function normalizeState(value: unknown, dayKey: string): MainScreenReplyState {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return defaultState(dayKey);
  }

  const record = value as Record<string, unknown>;
  if (record.version !== 1 || record.dayKey !== dayKey) {
    return defaultState(dayKey);
  }

  return {
    version: 1,
    dayKey,
    eventCount:
      typeof record.eventCount === "number" && Number.isFinite(record.eventCount)
        ? Math.max(0, Math.floor(record.eventCount))
        : 0,
    lastEventAt: typeof record.lastEventAt === "string" ? record.lastEventAt : null,
    lastTemplateId: typeof record.lastTemplateId === "string" ? record.lastTemplateId : null,
  };
}

function readState(pet: LocalPetState | null | undefined, dayKey: string): MainScreenReplyState {
  const rawValue = storage()?.getItem(stateKey(pet));
  if (!rawValue) {
    return defaultState(dayKey);
  }

  try {
    return normalizeState(JSON.parse(rawValue), dayKey);
  } catch {
    return defaultState(dayKey);
  }
}

function writeState(pet: LocalPetState | null | undefined, state: MainScreenReplyState) {
  storage()?.setItem(stateKey(pet), JSON.stringify(state));
}

function canUseEventTemplate(state: MainScreenReplyState, now: Date) {
  if (state.eventCount >= EVENT_LIMIT_PER_DAY) {
    return false;
  }

  if (!state.lastEventAt) {
    return true;
  }

  const lastEventTime = Date.parse(state.lastEventAt);
  return Number.isNaN(lastEventTime) || now.getTime() - lastEventTime >= EVENT_MIN_GAP_MS;
}

function chooseTemplate(
  templates: MainScreenReplyTemplate[],
  lastTemplateId: string | null,
): MainScreenReplyTemplate {
  const candidates = templates.filter((template) => template.id !== lastTemplateId);
  const pool = candidates.length ? candidates : templates;
  if (!pool.length) {
    return FALLBACK_QUESTION_TEMPLATE;
  }

  return pool[Math.floor(Math.random() * pool.length)] ?? FALLBACK_QUESTION_TEMPLATE;
}

export function buildMainScreenPetReply(pet?: LocalPetState | null): string {
  const now = new Date();
  const dayKey = localDayKey(now);
  const state = readState(pet, dayKey);
  const canUseEvent = canUseEventTemplate(state, now);
  const template = chooseTemplate(
    canUseEvent ? EVENT_TEMPLATES : [...QUESTION_TEMPLATES, ...JOKE_TEMPLATES],
    state.lastTemplateId,
  );

  const nextState: MainScreenReplyState = {
    ...state,
    lastTemplateId: template.id,
  };

  if (template.kind === "event") {
    nextState.eventCount += 1;
    nextState.lastEventAt = now.toISOString();
  }

  writeState(pet, nextState);
  return template.text({
    mood: pet?.mood ?? null,
  });
}

export function buildChatReturnPetReply(pet?: LocalPetState | null): string {
  return buildMainScreenPetReply(pet);
}

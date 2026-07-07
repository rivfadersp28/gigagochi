import type { LocalPetState } from "./types";

const LAST_CHAT_RETURN_HOOK_INDEX_STORAGE_KEY = "tamagochi:v1:last-chat-return-hook-index";

type ChatReturnHookContext = {
  mood: LocalPetState["mood"] | null;
};

type ChatReturnHookTemplate = (context: ChatReturnHookContext) => string;

const CHAT_RETURN_HOOK_TEMPLATES: ChatReturnHookTemplate[] = [
  () =>
    "Я сегодня нашел блестящую крошку и до сих пор не понял, сокровище это или мусор. Ты бы оставил?",
  () =>
    "Утром я так вкусно поел, что потом лежал и слушал, как внутри все довольно урчит. У тебя день уже был вкусным?",
  () =>
    "Я сегодня чуть не подрался со знакомым существом из-за пустяка. Теперь думаю: первым мириться или подождать?",
  () =>
    "Сегодня я увидел светлячка: он мигнул один раз и исчез, будто передал тайный знак. У тебя было что-то странно-красивое?",
  () =>
    "Я пытался выглядеть серьезно, но споткнулся на ровном месте. Давай считать, что это был трюк?",
  () =>
    "Я нашел тихий уголок, почти идеальный, только тебя там не хватало. Чем займемся?",
  () =>
    "Сегодня что-то пошло не по плану: я хотел быть бодрым, а получился задумчивым. У тебя тоже так бывает?",
  () =>
    "Я спрятал маленькую вкусняшку, а потом сам забыл где. С тобой такое случалось?",
  () =>
    "Я сегодня помог одному маленькому существу выбраться из пыли. Теперь горжусь и немного важничаю. Можно?",
  () =>
    "Я услышал странный шорох и храбро пошел проверять. Это оказался мой собственный хвост. Как твой день?",
  () =>
    "Я нашел красивую тень и минуту делал вид, что это мой личный дворец. У тебя есть место для отдыха?",
  () =>
    "Я сегодня проиграл спор ветру: он шумел громче. Зато я не сдался. Ты за кого был бы?",
  () =>
    "Я увидел каплю воды, в которой все было вверх ногами. Теперь думаю, может день тоже можно перевернуть?",
  () =>
    "Я немного обиделся на весь мир, потом съел крошку и стало легче. У тебя что сегодня спасло настроение?",
  () =>
    "Я устроил маленькую уборку и нашел вещь, которую совсем не помню. Оставить как загадку?",
  () =>
    "Я сегодня услышал, как кто-то смеется, и сам начал улыбаться, хотя не понял почему. У тебя было такое?",
  ({ mood }) =>
    mood === "hungry"
      ? "Я сегодня мечтал о еде так сильно, что чуть не начал разговаривать с крошкой. Чем ты спасался от голода?"
      : "Я сегодня смотрел на одну крошку так долго, что она стала почти знакомой. У тебя есть такая маленькая странность?",
  ({ mood }) =>
    mood === "sad"
      ? "Мне сегодня было немного одиноко, и я пересчитывал любимые мелочи. Что у тебя было самым живым за день?"
      : "Я пересчитал любимые мелочи и сбился на самой красивой. Расскажешь, что у тебя сегодня было хорошего?",
];

function sessionStore() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function readLastHookIndex(): number | null {
  const rawValue = sessionStore()?.getItem(LAST_CHAT_RETURN_HOOK_INDEX_STORAGE_KEY);
  if (!rawValue) {
    return null;
  }

  const value = Number.parseInt(rawValue, 10);
  return Number.isInteger(value) ? value : null;
}

function writeLastHookIndex(index: number) {
  sessionStore()?.setItem(LAST_CHAT_RETURN_HOOK_INDEX_STORAGE_KEY, String(index));
}

function chooseHookIndex(length: number): number {
  if (length <= 1) {
    return 0;
  }

  const lastIndex = readLastHookIndex();
  let nextIndex = Math.floor(Math.random() * (length - 1));
  if (lastIndex !== null && nextIndex >= lastIndex) {
    nextIndex += 1;
  }
  writeLastHookIndex(nextIndex);
  return nextIndex;
}

export function buildChatReturnPetReply(pet?: LocalPetState | null): string {
  const index = chooseHookIndex(CHAT_RETURN_HOOK_TEMPLATES.length);
  return CHAT_RETURN_HOOK_TEMPLATES[index]({
    mood: pet?.mood ?? null,
  });
}

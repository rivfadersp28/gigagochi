import type { LocalPetAssetSet, PetMood } from "./types";

const TEST_PET_IMAGE_BASE = "/test-pet";
const TEST_PET_SCENE_URL = `${TEST_PET_IMAGE_BASE}/scene.png`;
const TEST_PET_SAD_SCENE_URL = `${TEST_PET_IMAGE_BASE}/teen-sad.png`;
const TEST_PET_HAPPY_SCENE_URL = `${TEST_PET_IMAGE_BASE}/teen-happy.png`;
const TEST_PET_STORY_IMAGE_BASE = TEST_PET_IMAGE_BASE;

export const TEST_PET_ASSET_SET_ID = "debug-test-pet-seedance-forest-mouse-v1";

const testPetScenes: Record<PetMood, string> = {
  idle: TEST_PET_SCENE_URL,
  happy: TEST_PET_HAPPY_SCENE_URL,
  hungry: TEST_PET_SCENE_URL,
  sad: TEST_PET_SAD_SCENE_URL,
};

export const TEST_PET_DESCRIPTION =
  "Маленький меланхоличный мышонок-исследователь в сшитом шлеме с компасом и старым ручным фонарем";

export const TEST_PET_ASSET_SET: LocalPetAssetSet = {
  assetSetId: TEST_PET_ASSET_SET_ID,
  generatedAt: "2026-07-09T15:04:31.000Z",
  characterBible: {
    extensions: {
      recent_story_events: [
        {
          id: "test-story-cracked-light",
          title: "Трещина в старом свете",
          summary: "Мышонок спасся из запирающегося коридора, но повредил фонарь.",
          storyText:
            "Под древним каменным мостом мышонок-исследователь шел по заросшему подземному коридору, держа старый фонарь перед носом. Когда он задел плечом выступающий корень, тяжелая плита впереди поползла вниз и начала запирать путь назад. Мышонок подложил фонарь в щель, успел протиснуться в боковой лаз и вытащил его следом. Он выбрался наружу, но стекло фонаря треснуло, и теперь свет ложится перед ним узкой дрожащей полосой.",
          imageUrl: `${TEST_PET_STORY_IMAGE_BASE}/story-cracked-light.png`,
          generatedAt: "2026-07-10T14:12:30.021044Z",
          createdAt: "2026-07-10T14:12:30.021044Z",
          source: "test_fixture",
        },
        {
          id: "test-story-grey-hail",
          title: "Вмятина от серого града",
          summary: "Мышонок переждал каменный град, но на шлеме осталась вмятина.",
          storyText:
            "На узкой горной тропе мышонок увидел, как низкая туча осыпает склон серым градом. Мелкие камни застучали по плитам, и идти дальше стало нельзя. Он поддел плоский сланец, поставил его ребром перед выемкой и забрался за каменную заслонку. Когда град стих, на его сшитом шлеме осталась новая вмятина, зато дыхание стало ровнее.",
          imageUrl: `${TEST_PET_STORY_IMAGE_BASE}/story-grey-hail.png`,
          generatedAt: "2026-07-10T14:13:29.359694Z",
          createdAt: "2026-07-10T14:13:29.359694Z",
          source: "test_fixture",
        },
        {
          id: "test-story-foggy-hollow",
          title: "Кочка под туманом",
          summary: "Мышонок выбрался из скрытой канавы, но повредил компас.",
          storyText:
            "На туманном лугу мокрая осока скрывала под собой тонкую травяную корку. Мышонок ступил на неё и провалился в холодную канаву почти по грудь. Он вжал медный корпус компаса между корнями осоки и, подтягиваясь на ремешке, выбрался на плотную кочку. Мышонок ушёл дальше промокшим и дрожащим, а крышка компаса осталась погнутой, и стрелка теперь заедала.",
          imageUrl: `${TEST_PET_STORY_IMAGE_BASE}/story-foggy-hollow.png`,
          generatedAt: "2026-07-10T14:18:31.011926Z",
          createdAt: "2026-07-10T14:18:31.011926Z",
          source: "test_fixture",
        },
      ],
    },
  },
  videoUrl: `${TEST_PET_IMAGE_BASE}/scene.mp4`,
  sadVideoUrl: `${TEST_PET_IMAGE_BASE}/teen-sad.mp4`,
  happyVideoUrl: `${TEST_PET_IMAGE_BASE}/teen-happy.mp4`,
  images: {
    baby: { ...testPetScenes },
    teen: { ...testPetScenes },
    adult: { ...testPetScenes },
  },
};

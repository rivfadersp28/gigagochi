import { describe, expect, it } from "vitest";

import {
  splitPetReplyPortions,
  splitPetReplySentences,
} from "./petReplySentences";

describe("splitPetReplySentences", () => {
  it("splits a reply into complete sentences", () => {
    expect(
      splitPetReplySentences("Я нашёл светящийся камень. Хочешь узнать, где? Покажу завтра!"),
    ).toEqual([
      "Я нашёл светящийся камень",
      "Хочешь узнать, где?",
      "Покажу завтра!",
    ]);
  });

  it("keeps expressive punctuation and closing quotes with the sentence", () => {
    expect(splitPetReplySentences("«Ты это слышал?» Я сначала тоже не поверил… Потом проверил.")).toEqual([
      "«Ты это слышал?»",
      "Я сначала тоже не поверил…",
      "Потом проверил",
    ]);
  });

  it("removes a terminal period before a closing quote", () => {
    expect(splitPetReplySentences("«Я вернусь.»")).toEqual(["«Я вернусь»"]);
  });

  it("returns an unfinished phrase as one sentence", () => {
    expect(splitPetReplySentences("  Думаю   о лунной тропе  ")).toEqual([
      "Думаю о лунной тропе",
    ]);
  });

  it("keeps a long sentence in one portion", () => {
    expect(
      splitPetReplySentences(
        "Я нашёл старую башню среди деревьев и долго искал дверь, которая открывается только в полночь.",
      ),
    ).toEqual([
      "Я нашёл старую башню среди деревьев и долго искал дверь, которая открывается только в полночь",
    ]);
  });
});

describe("splitPetReplyPortions", () => {
  it("keeps three idle sentences as three portions", () => {
    const portions = splitPetReplyPortions(
      "Я заметил огонёк у старой башни. Хочу проверить, кто его оставил. Вернусь до рассвета!",
      { maxPortions: 3 },
    );

    expect(portions).toEqual([
      "Я заметил огонёк у старой башни",
      "Хочу проверить, кто его оставил",
      "Вернусь до рассвета!",
    ]);
  });

  it("keeps a short idle reply in one portion", () => {
    expect(
      splitPetReplyPortions("Хочешь посмотреть на звёзды?", {
        maxPortions: 2,
      }),
    ).toEqual(["Хочешь посмотреть на звёзды?"]);
  });

  it("keeps a long sentence intact in one portion", () => {
    const sentence = "а".repeat(90);
    const portions = splitPetReplyPortions(sentence, { maxPortions: 3 });

    expect(portions).toEqual([sentence]);
  });
});

import { describe, expect, it } from "vitest";

import { splitPetReplySentences } from "./petReplySentences";

describe("splitPetReplySentences", () => {
  it("splits a reply into complete sentences", () => {
    expect(
      splitPetReplySentences("Я нашёл светящийся камень. Хочешь узнать, где? Покажу завтра!"),
    ).toEqual([
      "Я нашёл светящийся камень.",
      "Хочешь узнать, где?",
      "Покажу завтра!",
    ]);
  });

  it("keeps punctuation and closing quotes with the sentence", () => {
    expect(splitPetReplySentences("«Ты это слышал?» Я сначала тоже не поверил… Потом проверил.")).toEqual([
      "«Ты это слышал?»",
      "Я сначала тоже не поверил…",
      "Потом проверил.",
    ]);
  });

  it("returns an unfinished phrase as one sentence", () => {
    expect(splitPetReplySentences("  Думаю   о лунной тропе  ")).toEqual([
      "Думаю о лунной тропе",
    ]);
  });
});

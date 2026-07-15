import type { InteractiveTravelPart, InteractiveTravelPlan } from "./types";

function task(index: number): InteractiveTravelPlan["tasks"][number] {
  const choices: [string, string, string, string] = [
    `Правильный ответ ${index}`,
    `Неверный ответ ${index}.1`,
    `Неверный ответ ${index}.2`,
    `Неверный ответ ${index}.3`,
  ];
  return {
    taskId: `task-${index}`,
    leadIn: `У маяка я встретил путника ${index}.`,
    situation: `Путнику нужна помощь в ситуации ${index}.`,
    question: `Как решить задачу ${index}?`,
    choices,
    correctChoice: choices[0],
    explanation: `Первый ответ решает задачу ${index}.`,
  };
}

export function interactiveTravelPlanFixture(): InteractiveTravelPlan {
  return {
    version: "task-bank-location-v4",
    tasks: [task(1), task(2), task(3), task(4)],
  };
}

export function interactiveTravelPartFixture(
  plan: InteractiveTravelPlan,
  index: 0 | 1 | 2 | 3,
  resolved = false,
): InteractiveTravelPart {
  const task = plan.tasks[index];
  const resultText = task.choiceOutcomes?.[0] ?? task.explanation ?? "Выбор сделан.";
  return {
    partNumber: index + 1,
    title: `Часть ${index + 1}`,
    storyText: `${task.leadIn} ${task.situation}`,
    ...(index > 0
      ? { transition: { elapsedHours: 2, summary: "Прошло два часа." } }
      : {}),
    challenge: task.question,
    actionSuggestions: [...task.choices],
    ...(resolved
      ? {
          answer: task.correctChoice,
          result: {
            text: resultText,
            adviceAssessment: "helpful",
            reaction: "Хороший выбор!",
            reactionTone: "enthusiastic",
            consequence: resultText,
            outcomeValence: "positive",
            statImpacts: [],
          },
        }
      : {}),
  };
}

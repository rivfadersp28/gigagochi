import type { ChatPromptDebug } from "@/lib/types";

type PromptDebugCarrier = {
  debug?: {
    promptDebug?: ChatPromptDebug[];
  };
};

export function logBrowserPromptDebug(label: string, carrier: PromptDebugCarrier): void {
  const prompts = carrier.debug?.promptDebug;
  if (!prompts?.length) {
    return;
  }

  console.groupCollapsed(`[prompt-debug] ${label}: ${prompts.length}`);
  prompts.forEach((prompt, index) => {
    console.log(`${index + 1}. ${prompt.label ?? "chat prompt"}`, prompt);
  });
  console.groupEnd();
}

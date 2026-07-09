import type {
  LocalPetMemoryContext,
  MemoryConsolidationOperation,
  MemoryOperation,
} from "./localPetMemoryTypes";
import type { ChatPromptDebug } from "./types";

const DEBUG_EVENTS_STORAGE_KEY = "tamagochi:v1:debug-events";
const MAX_EVENTS = 180;
const MAX_EVENT_TEXT_LENGTH = 14_000;

export const DEBUG_PANEL_EVENTS_CHANGED = "tamagochi-debug-panel-events-changed";

export type DebugPanelEventKind = "prompt" | "memory";

export type DebugPanelEvent = {
  id: string;
  createdAt: string;
  kind: DebugPanelEventKind;
  title: string;
  text: string;
};

type DebugEventDraft = Omit<DebugPanelEvent, "id" | "createdAt"> & {
  createdAt?: string;
};

type PromptDebugCarrier = {
  debug?: {
    promptDebug?: ChatPromptDebug[];
  };
};

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}

function createDebugEventId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `debug-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeText(value: unknown, limit = MAX_EVENT_TEXT_LENGTH): string {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function stringifyCompact(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function normalizeEvent(value: unknown): DebugPanelEvent | null {
  if (!isRecord(value)) {
    return null;
  }
  const kind = value.kind === "prompt" || value.kind === "memory" ? value.kind : null;
  const title = normalizeText(value.title, 160);
  const text = normalizeText(value.text);
  const createdAt =
    typeof value.createdAt === "string" && !Number.isNaN(Date.parse(value.createdAt))
      ? value.createdAt
      : new Date().toISOString();
  if (!kind || !title || !text) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createDebugEventId(),
    createdAt,
    kind,
    title,
    text,
  };
}

function notifyDebugEventsChanged() {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event(DEBUG_PANEL_EVENTS_CHANGED));
}

export function readDebugPanelEvents(): DebugPanelEvent[] {
  const store = storage();
  if (!store) {
    return [];
  }
  try {
    const rawValue = store.getItem(DEBUG_EVENTS_STORAGE_KEY);
    const parsed = rawValue ? JSON.parse(rawValue) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map(normalizeEvent)
      .filter((item): item is DebugPanelEvent => Boolean(item))
      .slice(-MAX_EVENTS);
  } catch {
    return [];
  }
}

export function appendDebugPanelEvents(drafts: DebugEventDraft[]) {
  const store = storage();
  if (!store) {
    return;
  }
  const events = drafts
    .map((draft) =>
      normalizeEvent({
        ...draft,
        id: createDebugEventId(),
        createdAt: draft.createdAt ?? new Date().toISOString(),
      }),
    )
    .filter((item): item is DebugPanelEvent => Boolean(item));
  if (!events.length) {
    return;
  }
  const nextEvents = [...readDebugPanelEvents(), ...events].slice(-MAX_EVENTS);
  store.setItem(DEBUG_EVENTS_STORAGE_KEY, JSON.stringify(nextEvents));
  notifyDebugEventsChanged();
}

export function clearDebugPanelEvents() {
  storage()?.removeItem(DEBUG_EVENTS_STORAGE_KEY);
  notifyDebugEventsChanged();
}

function promptLabel(prompt: ChatPromptDebug) {
  return (prompt.label ?? "").toLowerCase();
}

function isReplyPrompt(prompt: ChatPromptDebug) {
  const label = promptLabel(prompt);
  return (
    label.startsWith("pet_reply/lite round") ||
    label === "pet_reply/lite_world_seed" ||
    label === "pet_reply/proactive"
  );
}

function contentToText(content: unknown): string {
  if (typeof content === "string") {
    return content.trim();
  }
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (isRecord(item)) {
          if (typeof item.text === "string") {
            return item.text;
          }
          if (typeof item.content === "string") {
            return item.content;
          }
        }
        return stringifyCompact(item);
      })
      .map((item) => item.trim())
      .filter(Boolean)
      .join("\n");
  }
  if (content == null) {
    return "";
  }
  return stringifyCompact(content);
}

function promptMessagesToText(prompt: ChatPromptDebug): string {
  const messages = Array.isArray(prompt.messages) ? prompt.messages : [];
  const text = messages
    .map((message) => {
      const role = typeof message.role === "string" ? message.role : "message";
      const content = contentToText(message.content);
      return content ? `${role}:\n${content}` : "";
    })
    .filter(Boolean)
    .join("\n\n");
  return text || stringifyCompact(prompt);
}

export function recordReplyPromptDebug(carrier: PromptDebugCarrier) {
  const prompts = carrier.debug?.promptDebug?.filter(isReplyPrompt) ?? [];
  appendDebugPanelEvents(
    prompts.map((prompt) => ({
      kind: "prompt",
      title: "Запрос реплики",
      text: promptMessagesToText(prompt),
    })),
  );
}

function factLine(fact: Record<string, unknown>, fallbackSphere?: string): string | null {
  const text = normalizeText(fact.text, 900);
  if (!text) {
    return null;
  }
  const sphere = normalizeText(fact.sphere, 80) || fallbackSphere || "character";
  const kind = normalizeText(fact.kind, 80);
  return `- [${kind ? `${sphere}/${kind}` : sphere}] ${text}`;
}

function collectLiteFacts(value: unknown, sphereHint?: string, seen = new Set<string>()): string[] {
  if (!isRecord(value)) {
    return [];
  }
  const lines: string[] = [];
  const ownLine = factLine(value, sphereHint);
  if (ownLine && !seen.has(ownLine)) {
    seen.add(ownLine);
    lines.push(ownLine);
  }
  if (Array.isArray(value.facts)) {
    value.facts.forEach((fact) => {
      if (!isRecord(fact)) {
        return;
      }
      const line = factLine(fact, sphereHint);
      if (line && !seen.has(line)) {
        seen.add(line);
        lines.push(line);
      }
    });
  }
  if (isRecord(value.spheres)) {
    Object.entries(value.spheres).forEach(([sphere, sphereValue]) => {
      lines.push(...collectLiteFacts(sphereValue, sphere, seen));
    });
  }
  return lines;
}

export function recordLiteOverlayPatchDebug(patch?: Record<string, unknown>) {
  if (!patch) {
    return;
  }
  const facts = collectLiteFacts(patch);
  appendDebugPanelEvents([
    {
      kind: "memory",
      title: "Память персонажа обновлена",
      text: facts.length ? facts.join("\n") : stringifyCompact(patch),
    },
  ]);
}

export function recordMemoryContextDebug(
  memoryContext: LocalPetMemoryContext,
  title = "Память подставлена в prompt",
) {
  const lines: string[] = [];
  if (memoryContext.userProfile) {
    lines.push(`Профиль: ${memoryContext.userProfile}`);
  }
  if (memoryContext.summary) {
    lines.push(`Сводка: ${memoryContext.summary}`);
  }
  memoryContext.relevantMemories.forEach((item) => {
    lines.push(`- [${item.kind}] ${item.text}${item.dueAt ? ` (${item.dueAt})` : ""}`);
  });
  memoryContext.episodes?.forEach((episode, index) => {
    lines.push(`Эпизод ${index + 1}:`);
    episode.messages.forEach((message) => {
      lines.push(`${message.role}: ${message.text}`);
    });
  });
  if (memoryContext.proactiveCandidate?.reason) {
    lines.push(`Проактивный повод: ${memoryContext.proactiveCandidate.reason}`);
  }
  if (!lines.length) {
    return;
  }
  appendDebugPanelEvents([
    {
      kind: "memory",
      title,
      text: lines.join("\n"),
    },
  ]);
}

export function recordMemoryOperationsDebug(operations: MemoryOperation[]) {
  if (!operations.length) {
    return;
  }
  const lines = operations
    .map((operation) => {
      if (operation.type === "remember_user_fact") {
        return `- [${operation.kind}] ${operation.text}`;
      }
      return `- [learning/${operation.kind ?? "user_fact"}] ${operation.observation}`;
    })
    .filter(Boolean);
  appendDebugPanelEvents([
    {
      kind: "memory",
      title: "Память пользователя обновлена",
      text: lines.join("\n"),
    },
  ]);
}

export function recordMemoryConsolidationDebug(operations: MemoryConsolidationOperation[]) {
  if (!operations.length) {
    return;
  }
  const lines = operations.map((operation) => {
    if (operation.type === "promote_learning") {
      return `- promoted: [${operation.memory.kind}] ${operation.memory.text}`;
    }
    if (operation.type === "prune_learning") {
      return `- pruned: ${operation.learningId}${operation.reason ? ` (${operation.reason})` : ""}`;
    }
    if (operation.type === "rewrite_summary") {
      return `- summary: ${operation.content}`;
    }
    return `- user_profile: ${operation.content}`;
  });
  appendDebugPanelEvents([
    {
      kind: "memory",
      title: "Память пользователя обновлена",
      text: lines.join("\n"),
    },
  ]);
}

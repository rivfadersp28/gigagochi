import { API_URL, ApiError } from "./api";

export type AdminSpeechFileFormat = "json" | "jsonl";
export type AdminSpeechDataSource = "local" | "production";

export type AdminSpeechFile = {
  id: string;
  label: string;
  path: string;
  format: AdminSpeechFileFormat;
  description: string;
  exists: boolean;
  sizeBytes: number;
  updatedAt: string | null;
  summary: Record<string, unknown>;
  content: string;
};

export type AdminSpeechManifest = {
  generatedAt: string;
  mode: AdminSpeechDataSource;
  files: AdminSpeechFile[];
  dialogue: {
    modifiers: AdminDialogueInfluenceItem[];
    collections: AdminDialogueInfluenceItem[];
  };
  sync: {
    status: "disabled" | "already_current" | "synced" | string;
    message: string;
    serverCommit: string | null;
    updatedAt: string;
  };
  deploy: {
    enabled: boolean;
    message: string;
  };
};

export type AdminDialogueInfluenceItem = {
  id: string;
  label: string;
  role?: string;
  surfaces: string[];
  source: string;
  editable: boolean;
  fileId: string | null;
  configPath: string | null;
  summary: string;
};

export type AdminSpeechSaveResponse = {
  saved: boolean;
  updatedAt: string;
  errors: Record<string, string>;
  files: Array<{
    id: string;
    path: string;
    backupPath: string | null;
    sizeBytes: number;
  }>;
};

export type AdminSpeechPublishStatus = "pending" | "running" | "succeeded" | "failed";

export type AdminSpeechPublishLog = {
  at: string;
  level: "info" | "warning" | "error" | string;
  message: string;
};

export type AdminSpeechPublishJob = {
  id: string;
  status: AdminSpeechPublishStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  logs: AdminSpeechPublishLog[];
  error: string | null;
  errorCode: string | null;
  savedFiles: AdminSpeechSaveResponse["files"];
  commitSha: string | null;
};

export type AdminPushStatus = {
  count: number;
  latest: AdminPushRecord | null;
  records: AdminPushRecord[];
};

export type AdminPushRecord = {
  telegramId: number;
  username?: string | null;
  firstName?: string | null;
  petId: string;
  registeredAt: string;
  lastPushAt?: string | null;
  lastDebugPushAt?: string | null;
  lastPushError?: string | null;
  lastPushErrorAt?: string | null;
};

export type AdminPushSendResponse = {
  sent: boolean;
  manual: boolean;
  telegramId: number;
  petId: string;
  reply: string;
  sentAt: string;
  debug?: unknown;
};

async function adminRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    let code: string | undefined;
    try {
      const payload = await response.json();
      const detail = payload.detail ?? payload;
      message =
        detail.message ??
        detail.code ??
        detail.errors?.[Object.keys(detail.errors)[0]] ??
        JSON.stringify(detail);
      code = detail.code;
    } catch {
      message = `${message} ${response.statusText}`;
    }
    throw new ApiError(message, code, response.status);
  }

  return response.json() as Promise<T>;
}

export function fetchAdminSpeechManifest(
  source: AdminSpeechDataSource = "local",
): Promise<AdminSpeechManifest> {
  return adminRequest<AdminSpeechManifest>(`/api/admin/speech?source=${source}`);
}

export function saveAdminSpeechFiles(
  files: Array<Pick<AdminSpeechFile, "id" | "content">>,
  source: AdminSpeechDataSource = "local",
): Promise<AdminSpeechSaveResponse> {
  return adminRequest<AdminSpeechSaveResponse>(`/api/admin/speech?source=${source}`, {
    method: "PUT",
    body: JSON.stringify({ files }),
  });
}

export function startAdminSpeechPublish(
  files: Array<Pick<AdminSpeechFile, "id" | "content">>,
  message?: string,
): Promise<AdminSpeechPublishJob> {
  return adminRequest<AdminSpeechPublishJob>("/api/admin/speech/publish", {
    method: "POST",
    body: JSON.stringify({ files, message }),
  });
}

export function fetchAdminSpeechPublishJob(jobId: string): Promise<AdminSpeechPublishJob> {
  return adminRequest<AdminSpeechPublishJob>(`/api/admin/speech/publish/${jobId}`);
}

export function fetchAdminPushStatus(): Promise<AdminPushStatus> {
  return adminRequest<AdminPushStatus>("/api/admin/push/status");
}

export function sendAdminPush(
  reason?: string,
  telegramId?: number,
): Promise<AdminPushSendResponse> {
  return adminRequest<AdminPushSendResponse>("/api/admin/push/send", {
    method: "POST",
    body: JSON.stringify({ reason, telegramId, includeDebug: true }),
  });
}

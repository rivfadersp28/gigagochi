import { API_URL, ApiError } from "./api";

export type AdminSpeechFileFormat = "json" | "jsonl";

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
  mode: "local";
  files: AdminSpeechFile[];
  deploy: {
    enabled: boolean;
    message: string;
  };
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

export function fetchAdminSpeechManifest(): Promise<AdminSpeechManifest> {
  return adminRequest<AdminSpeechManifest>("/api/admin/speech");
}

export function saveAdminSpeechFiles(
  files: Array<Pick<AdminSpeechFile, "id" | "content">>,
): Promise<AdminSpeechSaveResponse> {
  return adminRequest<AdminSpeechSaveResponse>("/api/admin/speech", {
    method: "PUT",
    body: JSON.stringify({ files }),
  });
}

import { ADMIN_API_URL, AdminGenerationLabApiError } from "./adminGenerationLabApi";
import type {
  CalibrationLabStatus,
  CalibrationRunCreateRequest,
  CalibrationRunCreateResponse,
  CalibrationTask,
  CalibrationTaskType,
  CalibrationVote,
  CalibrationVoteCreateRequest,
} from "./types";

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_GENERATION_LAB_TOKEN;

type AdminRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

function adminHeaders(extraHeaders: HeadersInit | undefined): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
    ...extraHeaders,
  };
}

async function adminRequest<T>(path: string, options: AdminRequestOptions = {}): Promise<T> {
  const response = await fetch(`${ADMIN_API_URL}${path}`, {
    ...options,
    headers: adminHeaders(options.headers),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let message = response.statusText || "Calibration lab request failed";
    let code: string | undefined;
    try {
      const payload = await response.json();
      const detail = payload?.detail ?? payload;
      message = detail?.message ?? payload?.message ?? message;
      code = detail?.code ?? payload?.code;
    } catch {
      // Keep the HTTP status text when the backend did not return JSON.
    }
    throw new AdminGenerationLabApiError(message, code);
  }

  return response.json() as Promise<T>;
}

function query(params: { taskType?: string; runId?: string }): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) {
      search.set(key, value);
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function fetchCalibrationExport(
  kind: "votes" | "winners",
  format: "jsonl" | "json" = "jsonl",
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(
    `${ADMIN_API_URL}/admin/calibration-lab/export/${kind}?format=${format}`,
    {
      signal,
      headers: adminHeaders(undefined),
    },
  );

  if (!response.ok) {
    let message = response.statusText || "Calibration export failed";
    let code: string | undefined;
    try {
      const payload = await response.json();
      const detail = payload?.detail ?? payload;
      message = detail?.message ?? payload?.message ?? message;
      code = detail?.code ?? payload?.code;
    } catch {
      // Keep the HTTP status text when the backend did not return JSON.
    }
    throw new AdminGenerationLabApiError(message, code);
  }

  return response.text();
}

export async function getCalibrationLabStatus(signal?: AbortSignal): Promise<CalibrationLabStatus> {
  return adminRequest<CalibrationLabStatus>("/admin/calibration-lab/status", { signal });
}

export async function createCalibrationRun(
  payload: CalibrationRunCreateRequest,
  signal?: AbortSignal,
): Promise<CalibrationRunCreateResponse> {
  return adminRequest<CalibrationRunCreateResponse>("/admin/calibration-lab/runs", {
    method: "POST",
    signal,
    body: payload,
  });
}

export async function getNextCalibrationTask(
  filters: { taskType?: CalibrationTaskType; runId?: string } = {},
  signal?: AbortSignal,
): Promise<CalibrationTask | null> {
  return adminRequest<CalibrationTask | null>(
    `/admin/calibration-lab/tasks/next${query(filters)}`,
    { signal },
  );
}

export async function getCalibrationTask(
  taskId: string,
  signal?: AbortSignal,
): Promise<CalibrationTask> {
  return adminRequest<CalibrationTask>(`/admin/calibration-lab/tasks/${taskId}`, { signal });
}

export async function saveCalibrationVote(
  payload: CalibrationVoteCreateRequest,
  signal?: AbortSignal,
): Promise<CalibrationVote> {
  return adminRequest<CalibrationVote>("/admin/calibration-lab/votes", {
    method: "POST",
    signal,
    body: payload,
  });
}

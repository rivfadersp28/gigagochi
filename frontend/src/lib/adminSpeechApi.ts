import {
  parseAdminSpeechManifest,
  parseAdminSpeechPublishJob,
  parseAdminSpeechSaveResponse,
  type AdminSpeechDataSource,
  type AdminSpeechFile,
  type AdminSpeechManifest,
  type AdminSpeechPublishJob,
  type AdminSpeechSaveResponse,
} from "./adminSpeechContracts";
import { request } from "./apiTransport";

export type {
  AdminSpeechDataSource,
  AdminSpeechFile,
  AdminSpeechManifest,
  AdminSpeechPublishJob,
  AdminSpeechSaveResponse,
} from "./adminSpeechContracts";

export function fetchAdminSpeechManifest(
  source: AdminSpeechDataSource = "local",
): Promise<AdminSpeechManifest> {
  return request(
    `/api/admin/speech?source=${source}`,
    {},
    parseAdminSpeechManifest,
  );
}

export function saveAdminSpeechFiles(
  files: Array<Pick<AdminSpeechFile, "id" | "content">>,
  source: AdminSpeechDataSource = "local",
): Promise<AdminSpeechSaveResponse> {
  return request(
    `/api/admin/speech?source=${source}`,
    {
      method: "PUT",
      body: { files },
    },
    parseAdminSpeechSaveResponse,
  );
}

export function startAdminSpeechPublish(
  files: Array<Pick<AdminSpeechFile, "id" | "content">>,
  message?: string,
): Promise<AdminSpeechPublishJob> {
  return request(
    "/api/admin/speech/publish",
    {
      method: "POST",
      body: { files, message },
    },
    parseAdminSpeechPublishJob,
  );
}

export function fetchAdminSpeechPublishJob(jobId: string): Promise<AdminSpeechPublishJob> {
  return request(
    `/api/admin/speech/publish/${encodeURIComponent(jobId)}`,
    {},
    parseAdminSpeechPublishJob,
  );
}

import type { components } from "./generated/openapi";
import { ApiContractError } from "./apiTransport";

type ApiSchemas = components["schemas"];
type JsonRecord = Record<string, unknown>;

export type AdminSpeechFile = ApiSchemas["AdminSpeechFileResponse"];
export type AdminSpeechManifest = ApiSchemas["AdminSpeechManifestResponse"];
export type AdminSpeechSaveResponse = ApiSchemas["AdminSaveResponse"];
export type AdminSpeechPublishJob = ApiSchemas["AdminPublishJobResponse"];
export type AdminSpeechDataSource = AdminSpeechManifest["mode"];

function fail(path: string, expected: string): never {
  throw new ApiContractError(`${path}: ожидалось ${expected}`);
}

function record(value: unknown, path: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fail(path, "object");
  }
  return value as JsonRecord;
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    return fail(path, "array");
  }
  return value;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") {
    return fail(path, "string");
  }
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : string(value, path);
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fail(path, "finite number");
  }
  return value;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    return fail(path, "boolean");
  }
  return value;
}

function validateSavedFile(value: unknown, path: string): void {
  const file = record(value, path);
  string(file.id, `${path}.id`);
  string(file.path, `${path}.path`);
  nullableString(file.backupPath, `${path}.backupPath`);
  number(file.sizeBytes, `${path}.sizeBytes`);
}

function validateSpeechFile(value: unknown, path: string): void {
  const file = record(value, path);
  string(file.id, `${path}.id`);
  string(file.label, `${path}.label`);
  string(file.path, `${path}.path`);
  if (file.format !== "json") {
    fail(`${path}.format`, "json");
  }
  string(file.description, `${path}.description`);
  boolean(file.exists, `${path}.exists`);
  number(file.sizeBytes, `${path}.sizeBytes`);
  nullableString(file.updatedAt, `${path}.updatedAt`);
  record(file.summary, `${path}.summary`);
  string(file.content, `${path}.content`);
}

export function parseAdminSpeechManifest(value: unknown): AdminSpeechManifest {
  const payload = record(value, "adminManifest");
  string(payload.generatedAt, "adminManifest.generatedAt");
  if (payload.mode !== "local" && payload.mode !== "production") {
    fail("adminManifest.mode", "local or production");
  }
  array(payload.files, "adminManifest.files").forEach((file, index) =>
    validateSpeechFile(file, `adminManifest.files[${index}]`),
  );
  const sync = record(payload.sync, "adminManifest.sync");
  string(sync.status, "adminManifest.sync.status");
  string(sync.message, "adminManifest.sync.message");
  nullableString(sync.serverCommit, "adminManifest.sync.serverCommit");
  string(sync.updatedAt, "adminManifest.sync.updatedAt");
  const deploy = record(payload.deploy, "adminManifest.deploy");
  boolean(deploy.enabled, "adminManifest.deploy.enabled");
  string(deploy.message, "adminManifest.deploy.message");
  return payload as AdminSpeechManifest;
}

export function parseAdminSpeechSaveResponse(value: unknown): AdminSpeechSaveResponse {
  const payload = record(value, "adminSave");
  boolean(payload.saved, "adminSave.saved");
  string(payload.updatedAt, "adminSave.updatedAt");
  const errors = record(payload.errors, "adminSave.errors");
  Object.entries(errors).forEach(([key, message]) =>
    string(message, `adminSave.errors.${key}`),
  );
  array(payload.files, "adminSave.files").forEach((file, index) =>
    validateSavedFile(file, `adminSave.files[${index}]`),
  );
  return payload as AdminSpeechSaveResponse;
}

export function parseAdminSpeechPublishJob(value: unknown): AdminSpeechPublishJob {
  const payload = record(value, "adminPublishJob");
  string(payload.id, "adminPublishJob.id");
  if (!["pending", "running", "succeeded", "failed"].includes(String(payload.status))) {
    fail("adminPublishJob.status", "known publish status");
  }
  string(payload.createdAt, "adminPublishJob.createdAt");
  nullableString(payload.startedAt, "adminPublishJob.startedAt");
  nullableString(payload.finishedAt, "adminPublishJob.finishedAt");
  array(payload.logs, "adminPublishJob.logs").forEach((value, index) => {
    const log = record(value, `adminPublishJob.logs[${index}]`);
    string(log.at, `adminPublishJob.logs[${index}].at`);
    string(log.level, `adminPublishJob.logs[${index}].level`);
    string(log.message, `adminPublishJob.logs[${index}].message`);
  });
  nullableString(payload.error, "adminPublishJob.error");
  nullableString(payload.errorCode, "adminPublishJob.errorCode");
  array(payload.savedFiles, "adminPublishJob.savedFiles").forEach((file, index) =>
    validateSavedFile(file, `adminPublishJob.savedFiles[${index}]`),
  );
  nullableString(payload.commitSha, "adminPublishJob.commitSha");
  return payload as AdminSpeechPublishJob;
}

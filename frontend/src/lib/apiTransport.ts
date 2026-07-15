export const API_URL = process.env.NEXT_PUBLIC_API_URL?.trim() ?? "";

export type ApiErrorDetail = {
  error?: unknown;
  message?: unknown;
  code?: unknown;
  requestId?: unknown;
  retryAfterSeconds?: unknown;
  errors?: unknown;
  diagnostic?: unknown;
  activeJobId?: unknown;
  activeDescription?: unknown;
  travelId?: unknown;
};

export type ApiResponseParser<T> = (payload: unknown) => T;

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  idempotencyKey?: string;
  timeoutMs?: number;
};

const DEFAULT_REQUEST_TIMEOUT_MS = 5 * 60 * 1000;
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$/;
const MAX_RESPONSE_BODY_BYTES = 8 * 1024 * 1024;

export class ApiContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiContractError";
  }
}

export class ApiError extends Error {
  code?: string;
  status?: number;
  requestId?: string;
  diagnostic?: unknown;
  generationTerminal?: boolean;
  activeJobId?: string;
  activeDescription?: string;
  travelId?: string;

  constructor(
    message: string,
    code?: string,
    status?: number,
    options: {
      requestId?: string;
      diagnostic?: unknown;
      generationTerminal?: boolean;
      activeJobId?: string;
      activeDescription?: string;
      travelId?: string;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = options.requestId;
    this.diagnostic = options.diagnostic;
    this.generationTerminal = options.generationTerminal;
    this.activeJobId = options.activeJobId;
    this.activeDescription = options.activeDescription;
    this.travelId = options.travelId;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function jobIdValue(value: unknown): string | undefined {
  const jobId = stringValue(value);
  return jobId && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(jobId) ? jobId : undefined;
}

function interactiveTravelIdValue(value: unknown): string | undefined {
  const travelId = stringValue(value);
  return travelId
    && travelId.length >= 20
    && travelId.length <= 160
    && /^interactive-travel-[A-Za-z0-9_-]+$/u.test(travelId)
    ? travelId
    : undefined;
}

function activeDescriptionValue(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const description = value.trim();
  return description && description.length <= 300 ? description : undefined;
}

function numericValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function formatRetryAfter(seconds: number | undefined): string | undefined {
  if (seconds === undefined || seconds <= 0) {
    return undefined;
  }

  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) {
    return `${minutes} мин`;
  }

  const hours = Math.ceil(minutes / 60);
  if (hours < 24) {
    return `${hours} ч`;
  }

  return `${Math.ceil(hours / 24)} дн`;
}

function rateLimitMessage(detail: ApiErrorDetail, fallbackMessage?: string): string {
  const base = fallbackMessage ?? "Слишком много запросов.";
  const retryAfter = formatRetryAfter(numericValue(detail.retryAfterSeconds));
  return retryAfter ? `${base} Попробуйте через ${retryAfter}.` : base;
}

function errorDetail(payload: unknown): ApiErrorDetail {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  const record = payload as Record<string, unknown>;
  const detail = record.detail;
  if (detail && typeof detail === "object") {
    return detail as ApiErrorDetail;
  }
  return record as ApiErrorDetail;
}

function hasValidationErrors(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (!Array.isArray(detail)) {
    return false;
  }
  const firstError = detail.find(
    (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
  );
  return Boolean(firstError);
}

function errorMessageFromResponse(
  response: Response,
  payload: unknown,
): {
  message: string;
  code?: string;
  diagnostic?: unknown;
  activeJobId?: string;
  activeDescription?: string;
  travelId?: string;
} {
  const detail = errorDetail(payload);
  const code = stringValue(detail.code);
  const message =
    stringValue(detail.message) ??
    firstErrorMessage(detail.errors) ??
    (hasValidationErrors(payload)
      ? "Не получилось обработать данные. Обновите приложение и попробуйте снова."
      : undefined) ??
    stringValue((payload as { message?: unknown } | undefined)?.message);

  if (code === "rate_limited") {
    return {
      message: rateLimitMessage(detail, message),
      code,
      diagnostic: detail.diagnostic,
      activeJobId: jobIdValue(detail.activeJobId),
      activeDescription: activeDescriptionValue(detail.activeDescription),
      travelId: interactiveTravelIdValue(detail.travelId),
    };
  }

  return {
    message:
      message ??
      (response.status >= 500
        ? "Сервис временно недоступен. Попробуйте позже."
        : "Не получилось выполнить действие. Проверьте данные и попробуйте снова."),
    code,
    diagnostic: detail.diagnostic,
    activeJobId: jobIdValue(detail.activeJobId),
    activeDescription: activeDescriptionValue(detail.activeDescription),
    travelId: interactiveTravelIdValue(detail.travelId),
  };
}

function firstErrorMessage(value: unknown): string | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  for (const message of Object.values(value)) {
    const parsed = stringValue(message);
    if (parsed) {
      return parsed;
    }
  }
  return undefined;
}

export function apiErrorFromDetail(
  detail: ApiErrorDetail,
): {
  message: string;
  code?: string;
  diagnostic?: unknown;
  activeJobId?: string;
  activeDescription?: string;
  travelId?: string;
} {
  const code = stringValue(detail.code);
  const message = stringValue(detail.message);

  if (code === "rate_limited") {
    return {
      message: rateLimitMessage(detail, message),
      code,
      diagnostic: detail.diagnostic,
      activeJobId: jobIdValue(detail.activeJobId),
      activeDescription: activeDescriptionValue(detail.activeDescription),
      travelId: interactiveTravelIdValue(detail.travelId),
    };
  }

  return {
    message: message ?? "Не получилось создать питомца. Попробуйте снова.",
    code,
    diagnostic: detail.diagnostic,
    activeJobId: jobIdValue(detail.activeJobId),
    activeDescription: activeDescriptionValue(detail.activeDescription),
    travelId: interactiveTravelIdValue(detail.travelId),
  };
}

function invalidResponseError(response: Response): ApiError {
  return new ApiError(
    "Сервис вернул некорректный ответ. Попробуйте позже.",
    "INVALID_RESPONSE",
    response.status,
  );
}

async function cancelResponseBody(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch {
    // Best effort: rejecting the payload is the important boundary.
  }
}

async function readResponseText(response: Response): Promise<string> {
  const contentLength = response.headers.get("content-length")?.trim();
  if (contentLength && /^\d+$/u.test(contentLength)) {
    const declaredBytes = Number(contentLength);
    if (!Number.isSafeInteger(declaredBytes) || declaredBytes > MAX_RESPONSE_BODY_BYTES) {
      await cancelResponseBody(response);
      throw invalidResponseError(response);
    }
  }

  if (!response.body) {
    return "";
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      totalBytes += value.byteLength;
      if (totalBytes > MAX_RESPONSE_BODY_BYTES) {
        try {
          await reader.cancel();
        } catch {
          // Best effort: the oversized payload is rejected below.
        }
        throw invalidResponseError(response);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(body);
}

async function readJson(response: Response): Promise<unknown> {
  const responseText = await readResponseText(response);
  try {
    return JSON.parse(responseText) as unknown;
  } catch {
    if (!response.ok) {
      return undefined;
    }
    throw invalidResponseError(response);
  }
}

export async function request<T>(
  path: string,
  options: ApiRequestOptions,
  parse: ApiResponseParser<T>,
): Promise<T> {
  const url = `${API_URL}${path}`;
  const {
    body,
    idempotencyKey,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    signal: callerSignal,
    ...fetchOptions
  } = options;
  const requestController = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => requestController.abort(callerSignal?.reason);
  if (callerSignal?.aborted) {
    abortFromCaller();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const effectiveTimeoutMs =
    Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : DEFAULT_REQUEST_TIMEOUT_MS;
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    requestController.abort(new DOMException("Request timed out", "TimeoutError"));
  }, effectiveTimeoutMs);
  if (idempotencyKey !== undefined && !IDEMPOTENCY_KEY_PATTERN.test(idempotencyKey)) {
    globalThis.clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", abortFromCaller);
    throw new ApiError(
      "Не получилось безопасно повторить запрос. Попробуйте создать питомца заново.",
      "INVALID_IDEMPOTENCY_KEY",
    );
  }
  const headers = new Headers(fetchOptions.headers);
  if (body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (idempotencyKey !== undefined) {
    headers.set("Idempotency-Key", idempotencyKey);
  }

  let response: Response;
  let payload: unknown;
  try {
    response = await fetch(url, {
      ...fetchOptions,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: requestController.signal,
    });
    payload = await readJson(response);
  } catch (error) {
    if (callerSignal?.aborted) {
      throw error;
    }
    if (timedOut) {
      throw new ApiError(
        "Сервис не ответил вовремя. Попробуйте ещё раз.",
        "REQUEST_TIMEOUT",
      );
    }
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError(
      "Не удалось связаться с сервисом. Проверьте подключение и попробуйте снова.",
      "NETWORK_ERROR",
    );
  } finally {
    globalThis.clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }

  if (!response.ok) {
    const { message, code, diagnostic, activeJobId, activeDescription, travelId } =
      errorMessageFromResponse(response, payload);
    throw new ApiError(message, code, response.status, {
      diagnostic,
      activeJobId,
      activeDescription,
      travelId,
      requestId: response.headers.get("x-request-id") ?? undefined,
    });
  }

  try {
    return parse(payload);
  } catch (error) {
    if (!(error instanceof ApiContractError)) {
      throw error;
    }
    console.error(`[api-contract] ${path}: ${error.message}`);
    throw new ApiError(
      "Сервис вернул некорректный ответ. Попробуйте позже.",
      "INVALID_RESPONSE",
      response.status,
    );
  }
}

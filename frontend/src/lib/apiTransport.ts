export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type ApiErrorDetail = {
  error?: unknown;
  message?: unknown;
  code?: unknown;
  requestId?: unknown;
  retryAfterSeconds?: unknown;
  errors?: unknown;
};

export type ApiResponseParser<T> = (payload: unknown) => T;

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

export class ApiContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiContractError";
  }
}

export class ApiError extends Error {
  code?: string;
  status?: number;

  constructor(message: string, code?: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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

function errorMessageFromResponse(
  response: Response,
  payload: unknown,
): { message: string; code?: string } {
  const detail = errorDetail(payload);
  const code = stringValue(detail.code);
  const message =
    stringValue(detail.message) ??
    firstErrorMessage(detail.errors) ??
    stringValue((payload as { message?: unknown } | undefined)?.message);

  if (code === "rate_limited") {
    return { message: rateLimitMessage(detail, message), code };
  }

  return {
    message:
      message ??
      (response.status >= 500
        ? "Сервис временно недоступен. Попробуйте позже."
        : "Не получилось выполнить действие. Проверьте данные и попробуйте снова."),
    code,
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
): { message: string; code?: string } {
  const code = stringValue(detail.code);
  const message = stringValue(detail.message);

  if (code === "rate_limited") {
    return { message: rateLimitMessage(detail, message), code };
  }

  return {
    message: message ?? "Не получилось создать питомца. Попробуйте снова.",
    code,
  };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    if (!response.ok) {
      return undefined;
    }
    throw new ApiError(
      "Сервис вернул некорректный ответ. Попробуйте позже.",
      "INVALID_RESPONSE",
      response.status,
    );
  }
}

export async function request<T>(
  path: string,
  options: ApiRequestOptions,
  parse: ApiResponseParser<T>,
): Promise<T> {
  const url = `${API_URL}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(
      "Не удалось связаться с сервисом. Проверьте подключение и попробуйте снова.",
      "NETWORK_ERROR",
    );
  }

  const payload = await readJson(response);
  if (!response.ok) {
    const { message, code } = errorMessageFromResponse(response, payload);
    throw new ApiError(message, code, response.status);
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

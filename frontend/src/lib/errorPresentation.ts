import { ApiError } from "./apiTransport";
import { canUseDebugMenu } from "./telegram";

export type PresentedError = {
  message: string;
  technicalDetails?: string;
};

function diagnosticText(error: ApiError): string | undefined {
  const details: Record<string, unknown> = {};
  if (error.code) details.code = error.code;
  if (error.status) details.status = error.status;
  if (error.requestId) details.requestId = error.requestId;
  if (error.diagnostic !== undefined) details.diagnostic = error.diagnostic;
  return Object.keys(details).length ? JSON.stringify(details, null, 2) : undefined;
}

export function presentError(error: unknown, fallbackMessage: string): PresentedError {
  const message = error instanceof ApiError ? error.message : fallbackMessage;
  if (!canUseDebugMenu()) {
    return { message };
  }
  if (error instanceof ApiError) {
    return { message, technicalDetails: diagnosticText(error) };
  }
  if (error instanceof Error) {
    return {
      message,
      technicalDetails: `${error.name}: ${error.message}`,
    };
  }
  return { message, technicalDetails: String(error) };
}

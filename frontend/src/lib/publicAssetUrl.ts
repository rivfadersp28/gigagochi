import { API_URL } from "./apiTransport";

const MAX_ASSET_URL_LENGTH = 2_000;
const EXPLICIT_ASSET_ORIGINS = process.env.NEXT_PUBLIC_ASSET_ORIGINS ?? "";

function originFrom(value: string, base?: string): string | null {
  try {
    return new URL(value, base).origin;
  } catch {
    return null;
  }
}

function allowedOrigins(): Set<string> {
  const origins = new Set<string>();
  if (typeof window !== "undefined") {
    origins.add(window.location.origin);
  }
  const apiOrigin = originFrom(
    API_URL || "/",
    typeof window !== "undefined" ? window.location.origin : undefined,
  );
  if (apiOrigin && apiOrigin !== "null") {
    origins.add(apiOrigin);
  }
  for (const configured of EXPLICIT_ASSET_ORIGINS.split(",")) {
    const origin = originFrom(configured.trim());
    if (origin && origin !== "null") {
      origins.add(origin);
    }
  }
  return origins;
}

function safeAbsoluteUrl(value: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:")
    || parsed.username
    || parsed.password
    || !allowedOrigins().has(parsed.origin)
  ) {
    return null;
  }
  if (
    typeof window !== "undefined"
    && window.location.protocol === "https:"
    && parsed.protocol !== "https:"
  ) {
    return null;
  }
  return parsed.toString();
}

export function isSafePublicAssetUrl(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const candidate = value.trim();
  if (!candidate || candidate.length > MAX_ASSET_URL_LENGTH || candidate.startsWith("//")) {
    return false;
  }
  if (/^[A-Za-z][A-Za-z\d+.-]*:/u.test(candidate)) {
    return safeAbsoluteUrl(candidate) !== null;
  }
  return !candidate.includes("\\") && !candidate.includes("\0");
}

export function resolveApiAssetUrl(value: unknown): string | null {
  if (!isSafePublicAssetUrl(value)) {
    return null;
  }
  const candidate = value.trim();
  if (/^https?:/iu.test(candidate)) {
    return safeAbsoluteUrl(candidate);
  }
  if (!candidate.startsWith("/")) {
    return candidate;
  }
  const apiBase = API_URL.trim().replace(/\/$/u, "");
  const resolved = `${apiBase}${candidate}`;
  if (!apiBase || !/^https?:/iu.test(resolved)) {
    return resolved;
  }
  return safeAbsoluteUrl(resolved);
}

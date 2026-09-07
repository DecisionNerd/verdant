import { DigestCancelledError, throwIfCancelled } from "./jobControl.js";

/** Default: 2s → 4s → 8s → … capped (env-overridable). */
const BASE_MS = Math.max(500, Number(process.env.VERDANT_RL_BASE_MS ?? 2_000));
const MAX_MS = Math.max(BASE_MS, Number(process.env.VERDANT_RL_MAX_MS ?? 120_000));
const MAX_ATTEMPTS = Math.max(
  1,
  Number(process.env.VERDANT_RL_MAX_ATTEMPTS ?? 8),
);

/** Shared cooldown so concurrent page digests don't stampede after a 429. */
let globalCooldownUntil = 0;

export function isRateLimitError(err: unknown): boolean {
  if (err == null) return false;
  if (typeof err === "object") {
    const o = err as { status?: number; statusCode?: number; code?: string; message?: string };
    if (o.status === 429 || o.statusCode === 429) return true;
    if (String(o.code ?? "").toUpperCase() === "RATE_LIMIT_EXCEEDED") return true;
    if (typeof o.message === "string" && isRateLimitMessage(o.message)) return true;
  }
  if (typeof err === "string") return isRateLimitMessage(err);
  return false;
}

export function isRateLimitMessage(message: string): boolean {
  return /429|rate.?limit|too many requests|quota|resource.?exhausted|throttl/i.test(
    message,
  );
}

/** Parse Retry-After (seconds or HTTP-date) from an error / Response-like object. */
export function getRetryAfterMs(err: unknown): number | undefined {
  if (err == null || typeof err !== "object") return undefined;
  const o = err as {
    headers?: Headers | Record<string, string> | { get?: (k: string) => string | null };
    response?: { headers?: Headers | Record<string, string> };
    error?: { headers?: Record<string, string> };
  };

  const raw =
    readHeader(o.headers, "retry-after") ??
    readHeader(o.response?.headers, "retry-after") ??
    readHeader(o.error?.headers, "retry-after");

  if (!raw) return undefined;
  const asSec = Number(raw);
  if (Number.isFinite(asSec) && asSec >= 0) return Math.round(asSec * 1000);
  const asDate = Date.parse(raw);
  if (Number.isFinite(asDate)) return Math.max(0, asDate - Date.now());
  return undefined;
}

function readHeader(
  headers: Headers | Record<string, string> | { get?: (k: string) => string | null } | undefined,
  name: string,
): string | undefined {
  if (!headers) return undefined;
  if (typeof (headers as Headers).get === "function") {
    return (headers as Headers).get(name) ?? undefined;
  }
  const rec = headers as Record<string, string>;
  const key = Object.keys(rec).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? rec[key] : undefined;
}

/**
 * Exponential backoff with full jitter.
 * attempt is 1-based (first retry after failure → attempt 1).
 */
export function computeBackoffMs(
  attempt: number,
  opts?: { baseMs?: number; maxMs?: number; retryAfterMs?: number },
): number {
  const base = opts?.baseMs ?? BASE_MS;
  const max = opts?.maxMs ?? MAX_MS;
  const exp = Math.min(max, base * 2 ** Math.max(0, attempt - 1));
  // Full jitter: random in [0, exp]
  const jittered = Math.floor(Math.random() * (exp + 1));
  const fromHeader = opts?.retryAfterMs;
  if (fromHeader != null && fromHeader > 0) {
    // Prefer Retry-After, but never below a small floor; still add light jitter.
    return Math.min(max, Math.max(fromHeader, 250) + Math.floor(Math.random() * 500));
  }
  return Math.min(max, Math.max(jittered, Math.floor(base * 0.5)));
}

export function noteRateLimited(retryAfterMs?: number): void {
  const wait = retryAfterMs != null && retryAfterMs > 0
    ? retryAfterMs
    : computeBackoffMs(1);
  globalCooldownUntil = Math.max(globalCooldownUntil, Date.now() + wait);
}

export async function waitForRateLimitWindow(signal?: AbortSignal): Promise<void> {
  throwIfCancelled(signal);
  const wait = globalCooldownUntil - Date.now();
  if (wait > 0) await sleepMs(wait, signal);
}

export async function sleepMs(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return;
  throwIfCancelled(signal);
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DigestCancelledError());
    };
    if (signal?.aborted) {
      reject(new DigestCancelledError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export type LlmRetryOptions = {
  signal?: AbortSignal;
  maxAttempts?: number;
  baseMs?: number;
  maxMs?: number;
  /** Called before each wait (attempt is 1-based retry count). */
  onRetry?: (info: {
    attempt: number;
    maxAttempts: number;
    delayMs: number;
    error: unknown;
  }) => void | Promise<void>;
};

/**
 * Retry an LLM call on HTTP 429 / rate-limit errors with exponential backoff + jitter.
 * Non-rate-limit errors fail immediately.
 */
export async function withRateLimitRetry<T>(
  fn: () => Promise<T>,
  opts: LlmRetryOptions = {},
): Promise<T> {
  const maxAttempts = opts.maxAttempts ?? MAX_ATTEMPTS;
  let lastErr: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await waitForRateLimitWindow(opts.signal);
    throwIfCancelled(opts.signal);

    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (!isRateLimitError(err) || attempt >= maxAttempts) throw err;

      const retryAfterMs = getRetryAfterMs(err);
      const delayMs = computeBackoffMs(attempt, {
        baseMs: opts.baseMs,
        maxMs: opts.maxMs,
        retryAfterMs,
      });
      noteRateLimited(delayMs);
      await opts.onRetry?.({ attempt, maxAttempts, delayMs, error: err });
      await sleepMs(delayMs, opts.signal);
    }
  }

  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

export function rateLimitDefaults() {
  return { baseMs: BASE_MS, maxMs: MAX_MS, maxAttempts: MAX_ATTEMPTS };
}

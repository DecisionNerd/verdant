import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  computeBackoffMs,
  getRetryAfterMs,
  isRateLimitError,
  isRateLimitMessage,
  withRateLimitRetry,
} from "./rateLimit.js";

describe("isRateLimitError", () => {
  it("detects status 429 objects", () => {
    assert.equal(isRateLimitError({ status: 429, message: "nope" }), true);
    assert.equal(isRateLimitError({ statusCode: 429 }), true);
  });

  it("detects message patterns", () => {
    assert.equal(isRateLimitMessage("Error 429: rate limit exceeded"), true);
    assert.equal(isRateLimitMessage("Too Many Requests"), true);
    assert.equal(isRateLimitMessage("validation failed"), false);
  });
});

describe("getRetryAfterMs", () => {
  it("reads seconds from headers map", () => {
    assert.equal(getRetryAfterMs({ headers: { "retry-after": "12" } }), 12_000);
  });
});

describe("computeBackoffMs", () => {
  it("grows exponentially and respects cap", () => {
    const a1 = computeBackoffMs(1, { baseMs: 1000, maxMs: 10_000, retryAfterMs: undefined });
    const a4 = computeBackoffMs(4, { baseMs: 1000, maxMs: 10_000 });
    assert.ok(a1 >= 500 && a1 <= 1000);
    assert.ok(a4 <= 10_000);
  });

  it("prefers Retry-After when present", () => {
    const ms = computeBackoffMs(1, { baseMs: 1000, maxMs: 60_000, retryAfterMs: 15_000 });
    assert.ok(ms >= 15_000 && ms <= 15_500);
  });
});

describe("withRateLimitRetry", () => {
  it("retries on 429 then succeeds", async () => {
    let n = 0;
    const waits: number[] = [];
    const result = await withRateLimitRetry(
      async () => {
        n += 1;
        if (n < 3) {
          const err = new Error("429 rate limit") as Error & { status?: number };
          err.status = 429;
          throw err;
        }
        return "ok";
      },
      {
        maxAttempts: 5,
        baseMs: 10,
        maxMs: 50,
        onRetry: ({ delayMs }) => {
          waits.push(delayMs);
        },
      },
    );
    assert.equal(result, "ok");
    assert.equal(n, 3);
    assert.equal(waits.length, 2);
  });

  it("does not retry non-rate-limit errors", async () => {
    let n = 0;
    await assert.rejects(
      () =>
        withRateLimitRetry(async () => {
          n += 1;
          throw new Error("validation failed");
        }),
      /validation failed/,
    );
    assert.equal(n, 1);
  });
});

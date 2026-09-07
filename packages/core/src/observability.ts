import { NodeSDK } from "@opentelemetry/sdk-node";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";
import {
  trace,
  SpanStatusCode,
  type Span,
  type Tracer,
} from "@opentelemetry/api";
import { LangfuseSpanProcessor } from "@langfuse/otel";
import {
  propagateAttributes,
  startActiveObservation,
  type LangfuseGeneration,
  type LangfuseSpan,
} from "@langfuse/tracing";

let sdk: NodeSDK | null = null;
let langfuseProcessor: LangfuseSpanProcessor | null = null;
let initAttempted = false;
let openObserveEnabled = false;

/** Prefer in-compose URL when host is localhost (worker/mcp containers). */
function resolveServiceUrl(
  externalEnv: string,
  internalEnv: string,
): string | undefined {
  const external = process.env[externalEnv];
  const internal = process.env[internalEnv];
  if (internal && (!external || /localhost|127\.0\.0\.1/.test(external))) {
    return internal.replace(/\/$/, "");
  }
  return external?.replace(/\/$/, "");
}

function resolveLangfuseBaseUrl(): string | undefined {
  return resolveServiceUrl("LANGFUSE_BASE_URL", "LANGFUSE_BASE_URL_INTERNAL");
}

function resolveOpenObserveBaseUrl(): string | undefined {
  return resolveServiceUrl("OPENOBSERVE_URL", "OPENOBSERVE_URL_INTERNAL");
}

export function isLangfuseEnabled(): boolean {
  return Boolean(process.env.LANGFUSE_PUBLIC_KEY && process.env.LANGFUSE_SECRET_KEY);
}

export function isOpenObserveEnabled(): boolean {
  return Boolean(resolveOpenObserveBaseUrl());
}

export function isObservabilityEnabled(): boolean {
  return openObserveEnabled || Boolean(langfuseProcessor);
}

function getTracer(): Tracer {
  return trace.getTracer("verdant");
}

/**
 * Start OpenTelemetry → OpenObserve (primary) + optional Langfuse.
 * Safe to call repeatedly.
 */
export function initObservability(): void {
  if (initAttempted) return;
  initAttempted = true;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const processors: any[] = [];

  const ooBase = resolveOpenObserveBaseUrl();
  if (ooBase) {
    const org = process.env.OPENOBSERVE_ORG || "default";
    const email =
      process.env.ZO_ROOT_USER_EMAIL || process.env.OPENOBSERVE_USER_EMAIL || "";
    const password =
      process.env.ZO_ROOT_USER_PASSWORD ||
      process.env.OPENOBSERVE_USER_PASSWORD ||
      "";
    const headers: Record<string, string> = {};
    if (email && password) {
      headers.Authorization = `Basic ${Buffer.from(`${email}:${password}`).toString("base64")}`;
    }
    const exporter = new OTLPTraceExporter({
      url: `${ooBase}/api/${org}/v1/traces`,
      headers,
    });
    processors.push(new BatchSpanProcessor(exporter));
    openObserveEnabled = true;
    console.log(`[openobserve] tracing enabled → ${ooBase}`);
  } else {
    console.log("[openobserve] tracing disabled (OPENOBSERVE_URL not set)");
  }

  if (isLangfuseEnabled()) {
    const baseUrl = resolveLangfuseBaseUrl();
    if (baseUrl) {
      process.env.LANGFUSE_BASE_URL = baseUrl;
    }
    langfuseProcessor = new LangfuseSpanProcessor({
      publicKey: process.env.LANGFUSE_PUBLIC_KEY,
      secretKey: process.env.LANGFUSE_SECRET_KEY,
      baseUrl: process.env.LANGFUSE_BASE_URL,
    });
    processors.push(langfuseProcessor);
    console.log(
      `[langfuse] tracing enabled → ${process.env.LANGFUSE_BASE_URL ?? "cloud"}`,
    );
  } else {
    console.log("[langfuse] tracing disabled (LANGFUSE_PUBLIC_KEY / SECRET_KEY not set)");
  }

  if (processors.length === 0) return;

  sdk = new NodeSDK({
    resource: resourceFromAttributes({
      [ATTR_SERVICE_NAME]: "verdant",
    }),
    spanProcessors: processors,
  });
  sdk.start();
}

/** @deprecated Use initObservability */
export function initLangfuseTracing(): void {
  initObservability();
}

export async function flushObservability(): Promise<void> {
  if (langfuseProcessor) {
    try {
      await langfuseProcessor.forceFlush();
    } catch (err) {
      console.warn("[langfuse] flush failed:", err instanceof Error ? err.message : err);
    }
  }
}

/** @deprecated Use flushObservability */
export async function flushLangfuseTracing(): Promise<void> {
  return flushObservability();
}

export async function shutdownObservability(): Promise<void> {
  if (!sdk) return;
  try {
    await sdk.shutdown();
  } catch {
    // ignore
  }
  sdk = null;
  langfuseProcessor = null;
  openObserveEnabled = false;
}

/** @deprecated Use shutdownObservability */
export async function shutdownLangfuseTracing(): Promise<void> {
  return shutdownObservability();
}

export function truncateForTrace(value: string, max = 6_000): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max)}\n…[truncated ${value.length - max} chars]`;
}

/** Low-volume structured job progress for OpenObserve log search (stdout JSON). */
export function emitJobLog(
  runId: string,
  message: string,
  phase?: string,
): void {
  if (!openObserveEnabled && !langfuseProcessor) return;
  console.log(
    JSON.stringify({
      level: "info",
      service: "verdant",
      kind: "job_event",
      runId,
      phase: phase ?? null,
      message,
      ts: new Date().toISOString(),
    }),
  );
}

type SpanAttrs = {
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
  level?: "DEBUG" | "DEFAULT" | "WARNING" | "ERROR";
  statusMessage?: string;
};

function applyOtelAttrs(span: Span, attrs?: SpanAttrs): void {
  if (!attrs) return;
  if (attrs.input !== undefined) {
    span.setAttribute("input", JSON.stringify(attrs.input).slice(0, 4000));
  }
  if (attrs.output !== undefined) {
    span.setAttribute("output", JSON.stringify(attrs.output).slice(0, 4000));
  }
  if (attrs.metadata) {
    for (const [k, v] of Object.entries(attrs.metadata)) {
      if (v === undefined) continue;
      span.setAttribute(
        `meta.${k}`,
        typeof v === "string" ? v : JSON.stringify(v).slice(0, 1000),
      );
    }
  }
  if (attrs.statusMessage) span.setAttribute("status.message", attrs.statusMessage);
  if (attrs.level === "ERROR") span.setStatus({ code: SpanStatusCode.ERROR, message: attrs.statusMessage });
}

async function withOtelSpan<T>(
  name: string,
  fn: (span: { update: (attrs: SpanAttrs) => void }) => Promise<T>,
  attrs?: SpanAttrs,
): Promise<T> {
  const tracer = getTracer();
  return tracer.startActiveSpan(name, async (span) => {
    applyOtelAttrs(span, attrs);
    try {
      return await fn({
        update: (next) => applyOtelAttrs(span, next),
      });
    } catch (err) {
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: err instanceof Error ? err.message : String(err),
      });
      throw err;
    } finally {
      span.end();
    }
  });
}

/** Nested span; body still runs when observability is off. */
export async function withSpan<T>(
  name: string,
  fn: (span: { update: (attrs: SpanAttrs) => void }) => Promise<T>,
  attrs?: SpanAttrs,
): Promise<T> {
  initObservability();
  if (!isObservabilityEnabled()) {
    return fn({ update: () => undefined });
  }

  if (langfuseProcessor) {
    return startActiveObservation(
      name,
      async (span: LangfuseSpan) => {
        if (attrs) span.update(attrs);
        try {
          return await fn({
            update: (next) => {
              span.update(next);
            },
          });
        } catch (err) {
          span.update({
            level: "ERROR",
            statusMessage: err instanceof Error ? err.message : String(err),
          });
          throw err;
        }
      },
      { asType: "span" },
    );
  }

  return withOtelSpan(name, fn, attrs);
}

type DigestTraceOpts = {
  runId: string;
  inputPath: string;
  inputName: string;
  format?: string;
  tags?: string[];
};

/** Root chain for a digest run. */
export async function withDigestTrace<T>(
  opts: DigestTraceOpts,
  fn: (trace: { update: (attrs: SpanAttrs) => void }) => Promise<T>,
): Promise<T> {
  initObservability();
  if (!isObservabilityEnabled()) {
    return fn({ update: () => undefined });
  }

  if (langfuseProcessor) {
    return startActiveObservation(
      "document-digest",
      async (span: LangfuseSpan) => {
        return propagateAttributes(
          {
            sessionId: opts.runId,
            tags: ["digest", ...(opts.tags ?? [])],
            metadata: {
              runId: opts.runId,
              inputName: opts.inputName,
              format: opts.format ?? "markdown",
            },
            traceName: `digest:${opts.inputName}`,
          },
          async () => {
            span.update({
              input: {
                runId: opts.runId,
                inputPath: opts.inputPath,
                inputName: opts.inputName,
                format: opts.format ?? "markdown",
              },
              metadata: {
                runId: opts.runId,
              },
            });
            try {
              return await fn({
                update: (next) => {
                  span.update(next);
                },
              });
            } catch (err) {
              span.update({
                level: "ERROR",
                statusMessage: err instanceof Error ? err.message : String(err),
              });
              throw err;
            }
          },
        );
      },
      { asType: "chain" },
    );
  }

  return withOtelSpan(
    "document-digest",
    fn,
    {
      input: {
        runId: opts.runId,
        inputPath: opts.inputPath,
        inputName: opts.inputName,
        format: opts.format ?? "markdown",
      },
      metadata: { runId: opts.runId },
    },
  );
}

/** Wrap an LLM call as a generation (Langfuse) or attributed span (OpenObserve). */
export async function withGeneration<T>(
  name: string,
  attrs: {
    model: string;
    input: unknown;
    modelParameters?: Record<string, string | number>;
    metadata?: Record<string, unknown>;
  },
  fn: (gen: {
    update: (patch: {
      output?: unknown;
      usageDetails?: Record<string, number>;
      level?: "DEBUG" | "DEFAULT" | "WARNING" | "ERROR";
      statusMessage?: string;
      metadata?: Record<string, unknown>;
    }) => void;
  }) => Promise<T>,
): Promise<T> {
  initObservability();
  if (!isObservabilityEnabled()) {
    return fn({ update: () => undefined });
  }

  if (langfuseProcessor) {
    return startActiveObservation(
      name,
      async (generation: LangfuseGeneration) => {
        generation.update({
          model: attrs.model,
          input: attrs.input,
          modelParameters: attrs.modelParameters,
          metadata: attrs.metadata,
        });
        try {
          return await fn({
            update: (patch) => {
              generation.update(patch);
            },
          });
        } catch (err) {
          generation.update({
            level: "ERROR",
            statusMessage: err instanceof Error ? err.message : String(err),
            output: { error: err instanceof Error ? err.message : String(err) },
          });
          throw err;
        }
      },
      { asType: "generation" },
    );
  }

  const tracer = getTracer();
  return tracer.startActiveSpan(name, async (span) => {
    span.setAttribute("gen_ai.request.model", attrs.model);
    span.setAttribute("input", JSON.stringify(attrs.input).slice(0, 4000));
    if (attrs.metadata) {
      for (const [k, v] of Object.entries(attrs.metadata)) {
        if (v === undefined) continue;
        span.setAttribute(
          `meta.${k}`,
          typeof v === "string" ? v : JSON.stringify(v).slice(0, 1000),
        );
      }
    }
    try {
      return await fn({
        update: (patch) => {
          if (patch.output !== undefined) {
            span.setAttribute("output", JSON.stringify(patch.output).slice(0, 4000));
          }
          if (patch.statusMessage) {
            span.setAttribute("status.message", patch.statusMessage);
          }
          if (patch.level === "ERROR") {
            span.setStatus({ code: SpanStatusCode.ERROR, message: patch.statusMessage });
          }
          if (patch.usageDetails) {
            for (const [k, v] of Object.entries(patch.usageDetails)) {
              span.setAttribute(`usage.${k}`, v);
            }
          }
        },
      });
    } catch (err) {
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: err instanceof Error ? err.message : String(err),
      });
      throw err;
    } finally {
      span.end();
    }
  });
}

// keep file focused on observability exports

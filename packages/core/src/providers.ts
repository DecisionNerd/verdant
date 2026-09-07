import {
  bedrockCredentialsConfigured,
  completeBedrockVision,
  listBedrockModels,
  resolveBedrockRegion,
} from "./bedrock.js";
import { withRateLimitRetry } from "./rateLimit.js";

export type ProviderId =
  | "gemini"
  | "openai"
  | "anthropic"
  | "openrouter"
  | "bedrock"
  | "custom";

export type ProviderApiStyle = "openai" | "anthropic" | "bedrock";

export type ProviderDefinition = {
  id: ProviderId;
  label: string;
  defaultBaseUrl: string;
  defaultModel: string;
  apiStyle: ProviderApiStyle;
  requiresBaseUrl: boolean;
  /** AWS region instead of OpenAI-style base URL. */
  requiresRegion?: boolean;
  /** Access key + secret (or default AWS credential chain). */
  auth: "apiKey" | "aws";
};

export const PROVIDERS: Record<ProviderId, ProviderDefinition> = {
  gemini: {
    id: "gemini",
    label: "Gemini",
    defaultBaseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    defaultModel: "gemini-2.5-flash",
    apiStyle: "openai",
    requiresBaseUrl: false,
    auth: "apiKey",
  },
  openai: {
    id: "openai",
    label: "OpenAI",
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultModel: "gpt-4o",
    apiStyle: "openai",
    requiresBaseUrl: false,
    auth: "apiKey",
  },
  anthropic: {
    id: "anthropic",
    label: "Anthropic",
    defaultBaseUrl: "https://api.anthropic.com",
    defaultModel: "claude-sonnet-4-20250514",
    apiStyle: "anthropic",
    requiresBaseUrl: false,
    auth: "apiKey",
  },
  openrouter: {
    id: "openrouter",
    label: "OpenRouter",
    defaultBaseUrl: "https://openrouter.ai/api/v1",
    defaultModel: "google/gemini-2.5-flash",
    apiStyle: "openai",
    requiresBaseUrl: false,
    auth: "apiKey",
  },
  bedrock: {
    id: "bedrock",
    label: "Bedrock",
    defaultBaseUrl: "",
    // Cross-region inference profile ID (on-demand anthropic.* IDs often fail now)
    defaultModel: "us.anthropic.claude-sonnet-4-20250514-v1:0",
    apiStyle: "bedrock",
    requiresBaseUrl: false,
    requiresRegion: true,
    auth: "aws",
  },
  custom: {
    id: "custom",
    label: "Custom",
    defaultBaseUrl: "",
    defaultModel: "",
    apiStyle: "openai",
    requiresBaseUrl: true,
    auth: "apiKey",
  },
};

/** Explicit order so Bedrock stays just before Custom. */
export const PROVIDER_IDS: ProviderId[] = [
  "gemini",
  "openai",
  "anthropic",
  "openrouter",
  "bedrock",
  "custom",
];

export function isProviderId(value: string): value is ProviderId {
  return value in PROVIDERS;
}

export type ResolvedProviderConfig = {
  provider: ProviderId;
  /** API key, or AWS access key id for Bedrock. */
  apiKey: string;
  baseUrl: string;
  model: string;
  apiStyle: ProviderApiStyle;
  region?: string;
  secretAccessKey?: string;
  sessionToken?: string;
};

export async function listProviderModels(opts: {
  provider: ProviderId;
  apiKey: string;
  baseUrl?: string;
  region?: string;
  secretAccessKey?: string;
  sessionToken?: string;
}): Promise<{ id: string; name: string }[]> {
  const def = PROVIDERS[opts.provider];

  if (def.apiStyle === "bedrock") {
    const region = resolveBedrockRegion(opts.region);
    if (
      !bedrockCredentialsConfigured({
        accessKeyId: opts.apiKey,
        secretAccessKey: opts.secretAccessKey,
        region,
      })
    ) {
      throw new Error(
        "Bedrock requires an AWS region and credentials (Access Key + Secret, or the default AWS credential chain).",
      );
    }
    return listBedrockModels({
      region,
      accessKeyId: opts.apiKey || undefined,
      secretAccessKey: opts.secretAccessKey,
      sessionToken: opts.sessionToken,
    });
  }

  const baseUrl = (opts.baseUrl || def.defaultBaseUrl).replace(/\/$/, "");
  if (!opts.apiKey) throw new Error("API key is required to list models.");
  if (def.requiresBaseUrl && !baseUrl) {
    throw new Error("Base URL is required for the Custom provider.");
  }

  if (def.apiStyle === "anthropic") {
    const res = await fetch(`${baseUrl}/v1/models`, {
      headers: {
        "x-api-key": opts.apiKey,
        "anthropic-version": "2023-06-01",
      },
    });
    if (!res.ok) {
      throw new Error(`Model list failed (${res.status}): ${await res.text()}`);
    }
    const data = (await res.json()) as { data?: Array<{ id: string; display_name?: string }> };
    return (data.data ?? [])
      .map((m) => ({ id: m.id, name: m.display_name ?? m.id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  const res = await fetch(`${baseUrl}/models`, {
    headers: {
      Authorization: `Bearer ${opts.apiKey}`,
      ...(opts.provider === "openrouter"
        ? { "HTTP-Referer": "https://github.com/DecisionNerd/verdant", "X-Title": "Verdant" }
        : {}),
    },
  });
  if (!res.ok) {
    throw new Error(`Model list failed (${res.status}): ${await res.text()}`);
  }
  const data = (await res.json()) as { data?: Array<{ id: string }> };
  let models = (data.data ?? []).map((m) => {
    const id = m.id.replace(/^models\//, "");
    return { id, name: id };
  });

  if (opts.provider === "gemini") {
    models = models.filter((m) => /gemini/i.test(m.id));
  } else if (opts.provider === "openai") {
    const preferred = models.filter((m) => /^(gpt-|o[1-9]|chatgpt)/i.test(m.id));
    if (preferred.length) models = preferred;
  }

  return models.sort((a, b) => a.name.localeCompare(b.name));
}

export type VisionPage = {
  mimeType: string;
  base64: string;
};

export type VisionObserve = {
  name: string;
  metadata?: Record<string, string | number | boolean | null>;
};

type VisionResult = {
  text: string;
  usageDetails?: Record<string, number>;
};

export async function completeVisionChat(opts: {
  config: ResolvedProviderConfig;
  system: string;
  userText: string;
  pages: VisionPage[];
  temperature?: number;
  maxTokens?: number;
  observe?: VisionObserve;
  signal?: AbortSignal;
  /** Optional progress hook for rate-limit waits (e.g. digest page loop). */
  onRateLimitRetry?: (info: {
    attempt: number;
    maxAttempts: number;
    delayMs: number;
    error: unknown;
  }) => void | Promise<void>;
}): Promise<string> {
  const { withGeneration, truncateForTrace } = await import("./observability.js");
  const { config } = opts;
  const name = opts.observe?.name ?? "llm-completion";

  return withGeneration(
    name,
    {
      model: config.model,
      input: {
        system: truncateForTrace(opts.system, 2_000),
        user: truncateForTrace(opts.userText, 4_000),
        imageCount: opts.pages.length,
        // Never put base64 image bytes into Langfuse.
        imageMimeTypes: opts.pages.map((p) => p.mimeType),
      },
      modelParameters: {
        temperature: opts.temperature ?? 0.2,
        ...(opts.maxTokens != null ? { maxTokens: opts.maxTokens } : {}),
      },
      metadata: {
        provider: config.provider,
        apiStyle: config.apiStyle,
        ...Object.fromEntries(
          Object.entries(opts.observe?.metadata ?? {}).map(([k, v]) => [k, v]),
        ),
      },
    },
    async (gen) => {
      try {
        const result = await withRateLimitRetry(
          async () => {
            if (config.apiStyle === "bedrock") {
              return { text: await completeBedrockVision(opts) };
            }
            if (config.apiStyle === "anthropic") {
              return await completeAnthropicVision(opts);
            }
            return await completeOpenAiVision(opts);
          },
          {
            signal: opts.signal,
            onRetry: opts.onRateLimitRetry,
          },
        );
        gen.update({
          output: truncateForTrace(result.text),
          usageDetails: result.usageDetails,
        });
        return result.text;
      } catch (err) {
        gen.update({
          level: "ERROR",
          statusMessage: err instanceof Error ? err.message : String(err),
          output: { error: err instanceof Error ? err.message : String(err) },
        });
        throw err;
      }
    },
  );
}

async function completeOpenAiVision(opts: {
  config: ResolvedProviderConfig;
  system: string;
  userText: string;
  pages: VisionPage[];
  temperature?: number;
  maxTokens?: number;
}): Promise<VisionResult> {
  const OpenAI = (await import("openai")).default;
  const client = new OpenAI({
    apiKey: opts.config.apiKey,
    baseURL: opts.config.baseUrl,
    defaultHeaders:
      opts.config.provider === "openrouter"
        ? { "HTTP-Referer": "https://github.com/DecisionNerd/verdant", "X-Title": "Verdant" }
        : undefined,
  });

  const content: Array<
    | { type: "text"; text: string }
    | { type: "image_url"; image_url: { url: string } }
  > = [{ type: "text", text: opts.userText }];

  for (const page of opts.pages) {
    content.push({
      type: "image_url",
      image_url: { url: `data:${page.mimeType};base64,${page.base64}` },
    });
  }

  const completion = await client.chat.completions.create({
    model: opts.config.model,
    messages: [
      { role: "system", content: opts.system },
      { role: "user", content },
    ],
    temperature: opts.temperature ?? 0.2,
    max_tokens: opts.maxTokens,
  });

  const usage = completion.usage;
  return {
    text: completion.choices[0]?.message?.content?.trim() ?? "",
    usageDetails: usage
      ? {
          input: usage.prompt_tokens ?? 0,
          output: usage.completion_tokens ?? 0,
          total: usage.total_tokens ?? 0,
        }
      : undefined,
  };
}

async function completeAnthropicVision(opts: {
  config: ResolvedProviderConfig;
  system: string;
  userText: string;
  pages: VisionPage[];
  temperature?: number;
  maxTokens?: number;
}): Promise<VisionResult> {
  const baseUrl = opts.config.baseUrl.replace(/\/$/, "");
  const content: Array<Record<string, unknown>> = [{ type: "text", text: opts.userText }];
  for (const page of opts.pages) {
    const mediaType =
      page.mimeType === "image/jpeg" || page.mimeType === "image/webp" || page.mimeType === "image/gif"
        ? page.mimeType
        : "image/png";
    content.push({
      type: "image",
      source: {
        type: "base64",
        media_type: mediaType,
        data: page.base64,
      },
    });
  }

  const res = await fetch(`${baseUrl}/v1/messages`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": opts.config.apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: opts.config.model,
      max_tokens: opts.maxTokens ?? 8192,
      temperature: opts.temperature ?? 0.2,
      system: opts.system,
      messages: [{ role: "user", content }],
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    const err = new Error(`Anthropic request failed (${res.status}): ${body}`) as Error & {
      status?: number;
      headers?: Headers;
    };
    err.status = res.status;
    err.headers = res.headers;
    throw err;
  }

  const data = (await res.json()) as {
    content?: Array<{ type: string; text?: string }>;
    usage?: { input_tokens?: number; output_tokens?: number };
  };
  const text =
    data.content
      ?.filter((b) => b.type === "text")
      .map((b) => b.text ?? "")
      .join("\n")
      .trim() ?? "";
  const usageDetails =
    data.usage != null
      ? {
          input: data.usage.input_tokens ?? 0,
          output: data.usage.output_tokens ?? 0,
          total: (data.usage.input_tokens ?? 0) + (data.usage.output_tokens ?? 0),
        }
      : undefined;
  return { text, usageDetails };
}

export async function testProviderConnection(config: ResolvedProviderConfig): Promise<{
  ok: true;
  model: string;
  reply: string;
}> {
  const reply = await completeVisionChat({
    config,
    system: "You are a connectivity check.",
    userText: 'Reply with exactly the word "ok" and nothing else.',
    pages: [],
    temperature: 0,
    maxTokens: 16,
  });
  return { ok: true, model: config.model, reply };
}

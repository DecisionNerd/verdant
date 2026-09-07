import OpenAI from "openai";
import { readLlmSettings, getLlmSettingsPublic } from "./config.js";
import {
  bedrockCredentialsConfigured,
  resolveBedrockRegion,
} from "./bedrock.js";
import {
  PROVIDERS,
  isProviderId,
  testProviderConnection,
  type ProviderId,
  type ResolvedProviderConfig,
} from "./providers.js";

export type LlmConfig = {
  apiKey: string;
  baseUrl: string;
  model: string;
  provider?: ProviderId;
};

export { getLlmSettingsPublic };

/** Resolve active (or overridden) provider credentials. */
export function resolveProviderConfig(overrides?: {
  provider?: ProviderId;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  region?: string;
  secretAccessKey?: string;
  sessionToken?: string;
}): ResolvedProviderConfig {
  const settings = readLlmSettings();
  const provider: ProviderId =
    overrides?.provider && isProviderId(overrides.provider)
      ? overrides.provider
      : settings.activeProvider;
  const def = PROVIDERS[provider];
  const stored = settings.providers[provider] ?? {};

  if (provider === "bedrock") {
    const accessKeyId =
      overrides?.apiKey ||
      stored.apiKey ||
      process.env.AWS_ACCESS_KEY_ID ||
      "";
    const secretAccessKey =
      overrides?.secretAccessKey ||
      stored.secretAccessKey ||
      process.env.AWS_SECRET_ACCESS_KEY ||
      undefined;
    const sessionToken =
      overrides?.sessionToken ||
      stored.sessionToken ||
      process.env.AWS_SESSION_TOKEN ||
      undefined;
    const region = resolveBedrockRegion(
      overrides?.region || stored.region || undefined,
    );
    const model =
      overrides?.model ||
      stored.model ||
      process.env.LLM_MODEL ||
      def.defaultModel;

    if (
      !bedrockCredentialsConfigured({
        accessKeyId,
        secretAccessKey,
        region,
      })
    ) {
      throw new Error(
        "Bedrock requires an AWS region and credentials. " +
          "Open Settings → Bedrock: set region, Access Key ID, and Secret Access Key " +
          "(or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION for the worker).",
      );
    }
    if (!model) {
      throw new Error(
        "Select a Bedrock model or inference profile ID (e.g. us.anthropic.claude-sonnet-4-20250514-v1:0).",
      );
    }

    return {
      provider,
      apiKey: accessKeyId,
      baseUrl: "",
      model,
      apiStyle: "bedrock",
      region,
      secretAccessKey,
      sessionToken,
    };
  }

  const apiKey =
    overrides?.apiKey ||
    stored.apiKey ||
    (provider === settings.activeProvider ? process.env.LLM_API_KEY : undefined) ||
    "";
  const baseUrl = (
    overrides?.baseUrl ||
    stored.baseUrl ||
    (provider === "custom" ? process.env.LLM_BASE_URL : undefined) ||
    def.defaultBaseUrl
  ).replace(/\/$/, "");
  const model =
    overrides?.model ||
    stored.model ||
    (provider === settings.activeProvider ? process.env.LLM_MODEL : undefined) ||
    def.defaultModel;

  if (!apiKey) {
    throw new Error(
      `API key required for ${def.label}. Open Settings, select ${def.label}, paste a key, and Test API key.`,
    );
  }
  if (def.requiresBaseUrl && !baseUrl) {
    throw new Error("Base URL is required for the Custom provider.");
  }
  if (!model) {
    throw new Error(`Select a model for ${def.label}.`);
  }

  return {
    provider,
    apiKey,
    baseUrl,
    model,
    apiStyle: def.apiStyle,
  };
}

/** @deprecated use resolveProviderConfig */
export function getLlmConfig(overrides?: Partial<LlmConfig>): LlmConfig {
  const resolved = resolveProviderConfig(overrides);
  return {
    apiKey: resolved.apiKey,
    baseUrl: resolved.baseUrl,
    model: resolved.model,
    provider: resolved.provider,
  };
}

export async function testLlmConnection(overrides?: {
  provider?: ProviderId;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  region?: string;
  secretAccessKey?: string;
  sessionToken?: string;
}) {
  const config = resolveProviderConfig(overrides);
  return testProviderConnection(config);
}

export function createLlmClient(config?: Partial<LlmConfig>) {
  const resolved = resolveProviderConfig(config);
  if (resolved.apiStyle !== "openai") {
    throw new Error(
      "createLlmClient is only for OpenAI-compatible providers. Use completeVisionChat.",
    );
  }
  const client = new OpenAI({
    apiKey: resolved.apiKey,
    baseURL: resolved.baseUrl,
    defaultHeaders:
      resolved.provider === "openrouter"
        ? { "HTTP-Referer": "https://github.com/DecisionNerd/verdant", "X-Title": "Verdant" }
        : undefined,
  });
  return { client, model: resolved.model, provider: resolved.provider };
}

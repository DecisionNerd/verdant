import path from "node:path";
import { dataRoot } from "./paths.js";
import { bedrockCredentialsConfigured, resolveBedrockRegion } from "./bedrock.js";
import {
  isProviderId,
  PROVIDER_IDS,
  PROVIDERS,
  type ProviderId,
} from "./providers.js";
import { ensureDb } from "./db/client.js";

export type ProviderStored = {
  apiKey?: string;
  model?: string;
  /** Custom OpenAI-compat base URL, or unused for Bedrock. */
  baseUrl?: string;
  /** AWS region for Bedrock. */
  region?: string;
  /** AWS secret access key for Bedrock. */
  secretAccessKey?: string;
  /** Optional STS session token for Bedrock. */
  sessionToken?: string;
};

/** Persisted LLM settings (multi-provider). */
export type LlmSettings = {
  activeProvider: ProviderId;
  providers: Partial<Record<ProviderId, ProviderStored>>;
};

/** @deprecated legacy flat shape — migrated on read */
export type StoredLlmConfig = {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  activeProvider?: ProviderId;
  providers?: Partial<Record<ProviderId, ProviderStored>>;
};

export type VerdantConfig = {
  llm?: StoredLlmConfig & Partial<LlmSettings>;
};

let configCache: VerdantConfig | null = null;
let configHydrated = false;

/** Legacy path — kept for migration / docs. */
export function configPath(): string {
  return path.join(dataRoot(), "config.json");
}

function providerHasCreds(id: ProviderId, providers: Partial<Record<ProviderId, ProviderStored>>): boolean {
  const slot = providers[id];
  if (!slot) return false;
  if (id === "bedrock") {
    return bedrockCredentialsConfigured({
      accessKeyId: slot.apiKey,
      secretAccessKey: slot.secretAccessKey,
      region: slot.region,
    });
  }
  return Boolean(slot.apiKey);
}

function migrateLlm(raw: VerdantConfig["llm"]): LlmSettings {
  const providers: Partial<Record<ProviderId, ProviderStored>> = {
    ...(raw?.providers ?? {}),
  };

  if (raw?.apiKey && !Object.values(providers).some((p) => p?.apiKey)) {
    let target: ProviderId = "gemini";
    const base = raw.baseUrl ?? "";
    if (/openai\.com/i.test(base)) target = "openai";
    else if (/anthropic/i.test(base)) target = "anthropic";
    else if (/openrouter/i.test(base)) target = "openrouter";
    else if (/bedrock/i.test(base)) target = "bedrock";
    else if (base && !/generativelanguage\.googleapis/i.test(base)) target = "custom";

    providers[target] = {
      ...providers[target],
      apiKey: raw.apiKey,
      model: raw.model ?? providers[target]?.model,
      ...(target === "custom" ? { baseUrl: raw.baseUrl } : {}),
      ...(target === "bedrock" ? { region: raw.baseUrl || providers[target]?.region } : {}),
    };
  }

  const active =
    raw?.activeProvider && isProviderId(raw.activeProvider)
      ? raw.activeProvider
      : (PROVIDER_IDS.find((id) => providerHasCreds(id, providers)) ?? "gemini");

  return { activeProvider: active, providers };
}

async function loadConfigFromDb(): Promise<VerdantConfig> {
  const client = await ensureDb();
  const rs = await client.execute("SELECT config_json FROM settings WHERE id = 1");
  const row = rs.rows[0];
  if (!row?.config_json) return {};
  try {
    return JSON.parse(String(row.config_json)) as VerdantConfig;
  } catch {
    return {};
  }
}

/** Hydrate in-memory settings cache from Turso (call at process start). */
export async function hydrateConfigCache(): Promise<VerdantConfig> {
  configCache = await loadConfigFromDb();
  configHydrated = true;
  return configCache;
}

export function readVerdantConfig(): VerdantConfig {
  return configCache ?? {};
}

export function readLlmSettings(): LlmSettings {
  return migrateLlm(readVerdantConfig().llm);
}

export async function writeVerdantConfig(config: VerdantConfig): Promise<VerdantConfig> {
  if (!configHydrated) {
    await hydrateConfigCache();
  }
  const current = readVerdantConfig();
  const nextLlm = config.llm
    ? migrateLlm({
        ...current.llm,
        ...config.llm,
        providers: {
          ...migrateLlm(current.llm).providers,
          ...config.llm.providers,
        },
      })
    : migrateLlm(current.llm);

  for (const id of PROVIDER_IDS) {
    const slot = nextLlm.providers[id];
    if (!slot) continue;
    for (const key of Object.keys(slot) as (keyof ProviderStored)[]) {
      if (slot[key] === undefined || slot[key] === "") delete slot[key];
    }
    if (Object.keys(slot).length === 0) delete nextLlm.providers[id];
  }

  const merged: VerdantConfig = {
    ...current,
    ...config,
    llm: nextLlm,
  };

  const client = await ensureDb();
  const now = new Date().toISOString();
  await client.execute({
    sql: `INSERT INTO settings (id, config_json, updated_at) VALUES (1, ?, ?)
          ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at`,
    args: [JSON.stringify(merged), now],
  });
  configCache = merged;
  configHydrated = true;
  return merged;
}

export async function upsertProviderSettings(
  provider: ProviderId,
  patch: ProviderStored & {
    clearApiKey?: boolean;
    clearSecretAccessKey?: boolean;
    clearSessionToken?: boolean;
  },
  opts?: { makeActive?: boolean },
): Promise<LlmSettings> {
  if (!configHydrated) {
    await hydrateConfigCache();
  }
  const current = readLlmSettings();
  const existing = current.providers[provider] ?? {};
  const next: ProviderStored = { ...existing };

  if (patch.clearApiKey) {
    delete next.apiKey;
  } else if (patch.apiKey && !patch.apiKey.includes("…")) {
    next.apiKey = patch.apiKey.trim();
  }

  if (patch.clearSecretAccessKey) {
    delete next.secretAccessKey;
  } else if (patch.secretAccessKey && !patch.secretAccessKey.includes("…")) {
    next.secretAccessKey = patch.secretAccessKey.trim();
  }

  if (patch.clearSessionToken) {
    delete next.sessionToken;
  } else if (patch.sessionToken !== undefined) {
    if (patch.sessionToken === "" || patch.sessionToken.includes("…")) {
      // keep existing unless explicitly cleared
    } else {
      next.sessionToken = patch.sessionToken.trim();
    }
  }

  if (patch.model !== undefined) {
    if (patch.model === "") delete next.model;
    else next.model = patch.model.trim();
  }
  if (patch.baseUrl !== undefined) {
    if (patch.baseUrl === "") delete next.baseUrl;
    else next.baseUrl = patch.baseUrl.trim();
  }
  if (patch.region !== undefined) {
    if (patch.region === "") delete next.region;
    else next.region = patch.region.trim();
  }

  await writeVerdantConfig({
    llm: {
      activeProvider: opts?.makeActive === false ? current.activeProvider : provider,
      providers: {
        ...current.providers,
        [provider]: next,
      },
    },
  });
  return readLlmSettings();
}

export function maskSecret(value: string | undefined): string {
  if (!value) return "";
  if (value.length <= 8) return "••••••••";
  return `${value.slice(0, 3)}…${value.slice(-4)}`;
}

export function getProviderPublic(provider: ProviderId) {
  const settings = readLlmSettings();
  const stored = settings.providers[provider] ?? {};
  const def = PROVIDERS[provider];

  if (provider === "bedrock") {
    const region = resolveBedrockRegion(stored.region);
    const envAccess = process.env.AWS_ACCESS_KEY_ID ?? "";
    const accessKey = stored.apiKey || envAccess;
    const secret = stored.secretAccessKey || process.env.AWS_SECRET_ACCESS_KEY || "";
    const configured = bedrockCredentialsConfigured({
      accessKeyId: stored.apiKey || envAccess,
      secretAccessKey: stored.secretAccessKey || process.env.AWS_SECRET_ACCESS_KEY,
      region,
    });
    const source = stored.apiKey || stored.secretAccessKey
      ? ("ui" as const)
      : envAccess || process.env.AWS_PROFILE
        ? ("env" as const)
        : ("none" as const);

    return {
      id: provider,
      label: def.label,
      auth: def.auth,
      requiresBaseUrl: false,
      requiresRegion: true,
      defaultBaseUrl: "",
      defaultRegion: region,
      defaultModel: def.defaultModel,
      configured,
      apiKeyMasked: maskSecret(accessKey),
      secretAccessKeyMasked: maskSecret(secret),
      sessionTokenConfigured: Boolean(stored.sessionToken || process.env.AWS_SESSION_TOKEN),
      model: stored.model || def.defaultModel,
      baseUrl: "",
      region,
      source,
    };
  }

  const envFallback =
    settings.activeProvider === provider ? process.env.LLM_API_KEY ?? "" : "";
  const apiKey = stored.apiKey || envFallback;
  return {
    id: provider,
    label: def.label,
    auth: def.auth,
    requiresBaseUrl: def.requiresBaseUrl,
    requiresRegion: false,
    defaultBaseUrl: def.defaultBaseUrl,
    defaultRegion: "",
    defaultModel: def.defaultModel,
    configured: Boolean(apiKey),
    apiKeyMasked: maskSecret(apiKey),
    secretAccessKeyMasked: "",
    sessionTokenConfigured: false,
    model: stored.model || def.defaultModel,
    baseUrl: stored.baseUrl || def.defaultBaseUrl,
    region: "",
    source: stored.apiKey ? ("ui" as const) : envFallback ? ("env" as const) : ("none" as const),
  };
}

export function getLlmSettingsPublic() {
  const settings = readLlmSettings();
  const active = getProviderPublic(settings.activeProvider);
  return {
    activeProvider: settings.activeProvider,
    configured: active.configured,
    apiKeyMasked: active.apiKeyMasked,
    baseUrl: active.baseUrl,
    model: active.model,
    source: active.source,
    region: active.region,
    providers: PROVIDER_IDS.map((id) => getProviderPublic(id)),
  };
}

/** Test helper */
export function resetConfigCacheForTests(): void {
  configCache = null;
  configHydrated = false;
}

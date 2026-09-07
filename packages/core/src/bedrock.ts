import {
  BedrockClient,
  ListFoundationModelsCommand,
  ListInferenceProfilesCommand,
} from "@aws-sdk/client-bedrock";
import {
  BedrockRuntimeClient,
  ConverseCommand,
  type ContentBlock,
  type ImageFormat,
} from "@aws-sdk/client-bedrock-runtime";
import type { ResolvedProviderConfig, VisionPage } from "./providers.js";

const BEDROCK_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-2",
  "eu-west-1",
  "eu-central-1",
  "ap-northeast-1",
  "ap-southeast-1",
  "ap-southeast-2",
] as const;

export { BEDROCK_REGIONS };

export type BedrockCreds = {
  region: string;
  accessKeyId?: string;
  secretAccessKey?: string;
  sessionToken?: string;
};

function awsEnvCredsPresent(): boolean {
  return Boolean(
    (process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY) ||
      process.env.AWS_PROFILE ||
      process.env.AWS_CONTAINER_CREDENTIALS_RELATIVE_URI ||
      process.env.AWS_WEB_IDENTITY_TOKEN_FILE,
  );
}

export function resolveBedrockRegion(explicit?: string): string {
  return (
    explicit?.trim() ||
    process.env.AWS_REGION?.trim() ||
    process.env.AWS_DEFAULT_REGION?.trim() ||
    "us-east-1"
  );
}

export function bedrockCredentialsConfigured(opts: {
  accessKeyId?: string;
  secretAccessKey?: string;
  region?: string;
}): boolean {
  const region = resolveBedrockRegion(opts.region);
  if (!region) return false;
  const hasExplicit = Boolean(opts.accessKeyId?.trim() && opts.secretAccessKey?.trim());
  return hasExplicit || awsEnvCredsPresent();
}

function clientConfig(creds: BedrockCreds) {
  const region = resolveBedrockRegion(creds.region);
  const hasExplicit = Boolean(creds.accessKeyId && creds.secretAccessKey);
  return {
    region,
    ...(hasExplicit
      ? {
          credentials: {
            accessKeyId: creds.accessKeyId!,
            secretAccessKey: creds.secretAccessKey!,
            ...(creds.sessionToken ? { sessionToken: creds.sessionToken } : {}),
          },
        }
      : {}),
  };
}

function imageFormat(mimeType: string): ImageFormat {
  if (mimeType === "image/jpeg") return "jpeg";
  if (mimeType === "image/webp") return "webp";
  if (mimeType === "image/gif") return "gif";
  return "png";
}

function rewriteBedrockError(err: unknown): Error {
  const msg = err instanceof Error ? err.message : String(err);
  if (/ValidationException.*inference profile/i.test(msg) || /on-demand.*throughput/i.test(msg)) {
    return new Error(
      `${msg}\n\nBedrock tip: many Claude models now require a cross-region inference profile ID ` +
        `(e.g. us.anthropic.claude-sonnet-4-20250514-v1:0), not the bare anthropic.* model ID. ` +
        `Enable model access in the Bedrock console for this region.`,
    );
  }
  if (/AccessDeniedException|is not authorized|Model.*not authorized/i.test(msg)) {
    return new Error(
      `${msg}\n\nBedrock tip: enable the model (or inference profile) under Bedrock → Model access ` +
        `in this AWS region, and ensure the IAM principal can bedrock:InvokeModel / Converse.`,
    );
  }
  if (/UnrecognizedClientException|InvalidSignatureException|security token/i.test(msg)) {
    return new Error(
      `${msg}\n\nBedrock tip: use an AWS access key + secret (and session token if using STS), ` +
        `not a Bedrock-specific API key. Region must match where credentials are valid.`,
    );
  }
  if (/Could not load credentials/i.test(msg)) {
    return new Error(
      `${msg}\n\nBedrock tip: paste Access Key ID + Secret Access Key in Settings, ` +
        `or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION in the worker environment.`,
    );
  }
  return err instanceof Error ? err : new Error(msg);
}

export async function listBedrockModels(creds: BedrockCreds): Promise<{ id: string; name: string }[]> {
  const client = new BedrockClient(clientConfig(creds));
  const models = new Map<string, string>();

  try {
    const foundation = await client.send(
      new ListFoundationModelsCommand({
        byOutputModality: "TEXT",
      }),
    );
    for (const m of foundation.modelSummaries ?? []) {
      const id = m.modelId;
      if (!id) continue;
      const inputs = m.inputModalities ?? [];
      // Prefer vision-capable models for Verdant digests
      if (inputs.length && !inputs.includes("IMAGE") && !inputs.includes("TEXT")) continue;
      const vision = inputs.includes("IMAGE");
      const name = `${m.providerName ? `${m.providerName} · ` : ""}${m.modelName ?? id}${
        vision ? " (vision)" : ""
      }`;
      models.set(id, name);
    }
  } catch (err) {
    throw rewriteBedrockError(err);
  }

  try {
    const profiles = await client.send(new ListInferenceProfilesCommand({}));
    for (const p of profiles.inferenceProfileSummaries ?? []) {
      const id = p.inferenceProfileId ?? p.inferenceProfileArn;
      if (!id) continue;
      const name = `${p.inferenceProfileName ?? id} · inference profile`;
      models.set(id, name);
    }
  } catch {
    // Older regions / IAM without profile list permission — foundation list is enough
  }

  const list = [...models.entries()].map(([id, name]) => ({ id, name }));
  // Prefer Anthropic / vision / inference profiles near the top
  list.sort((a, b) => {
    const score = (m: { id: string; name: string }) => {
      let s = 0;
      if (/inference profile/i.test(m.name)) s += 3;
      if (/\(vision\)/i.test(m.name)) s += 2;
      if (/anthropic|claude/i.test(m.id)) s += 2;
      if (/^us\.|^eu\.|^apac\./i.test(m.id)) s += 1;
      return -s;
    };
    const d = score(a) - score(b);
    return d !== 0 ? d : a.name.localeCompare(b.name);
  });
  return list;
}

export async function completeBedrockVision(opts: {
  config: ResolvedProviderConfig;
  system: string;
  userText: string;
  pages: VisionPage[];
  temperature?: number;
  maxTokens?: number;
}): Promise<string> {
  const region = opts.config.region || resolveBedrockRegion();
  const client = new BedrockRuntimeClient(
    clientConfig({
      region,
      accessKeyId: opts.config.apiKey || undefined,
      secretAccessKey: opts.config.secretAccessKey,
      sessionToken: opts.config.sessionToken,
    }),
  );

  const content: ContentBlock[] = [{ text: opts.userText }];
  for (const page of opts.pages) {
    content.push({
      image: {
        format: imageFormat(page.mimeType),
        source: { bytes: Buffer.from(page.base64, "base64") },
      },
    });
  }

  try {
    const out = await client.send(
      new ConverseCommand({
        modelId: opts.config.model,
        system: [{ text: opts.system }],
        messages: [{ role: "user", content }],
        inferenceConfig: {
          maxTokens: opts.maxTokens ?? 8192,
          temperature: opts.temperature ?? 0.2,
        },
      }),
    );

    const parts = out.output?.message?.content ?? [];
    return parts
      .map((b) => ("text" in b && b.text ? b.text : ""))
      .join("\n")
      .trim();
  } catch (err) {
    throw rewriteBedrockError(err);
  }
}

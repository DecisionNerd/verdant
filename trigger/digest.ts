import { logger, task } from "@trigger.dev/sdk/v3";
import { digestDocument, DigestPayloadSchema, runLocalEval } from "@verdant/core";

export const digestDocumentTask = task({
  id: "digest-document",
  retry: {
    maxAttempts: 3,
    factor: 1.8,
    minTimeoutInMs: 1000,
    maxTimeoutInMs: 30_000,
  },
  run: async (payload: unknown) => {
    const parsed = DigestPayloadSchema.parse(payload);
    const result = await digestDocument(parsed, {
      onProgress: async (message, phase) => {
        logger.info(message, { phase });
      },
    });
    if (result.status === "failed") {
      throw new Error(result.error ?? "Digest failed");
    }
    return result;
  },
});

export const runEvalExperimentTask = task({
  id: "run-eval-experiment",
  run: async (payload: { datasetDir?: string; model?: string; name?: string }) => {
    const result = await runLocalEval({
      datasetDir: payload.datasetDir,
      model: payload.model,
    });
    return {
      experiment: payload.name ?? `verdant-eval-${new Date().toISOString()}`,
      ...result,
    };
  },
});

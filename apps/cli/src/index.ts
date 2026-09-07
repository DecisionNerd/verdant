#!/usr/bin/env node
import { Command } from "commander";
import {
  ensureDb,
  hydrateConfigCache,
  initObservability,
  shutdownObservability,
} from "@verdant/core";
import {
  cmdDigest,
  cmdEval,
  cmdListOutputs,
  cmdProcessQueueOnce,
  cmdReadOutput,
  cmdStatus,
  cmdSyncDataset,
} from "./commands.js";
import { startMcpHttp, startMcpStdio } from "./mcp.js";
import { startWorkerHttp } from "./worker-http.js";

async function boot(): Promise<void> {
  await ensureDb();
  await hydrateConfigCache();
  initObservability();
}

await boot();
process.once("beforeExit", () => {
  void shutdownObservability();
});

const program = new Command();

program
  .name("verdant")
  .description("Verdant — visually rich document digestion (image/PDF → Markdown/HTML)")
  .version("0.1.0");

program
  .command("digest")
  .argument("<input>", "Path to image or PDF (e.g. data/inputs/scan.pdf)")
  .option("-f, --format <format>", "markdown | html | both", "markdown")
  .option("--no-wait", "Enqueue only; do not wait for completion")
  .option("--model <model>", "Override LLM model")
  .action(async (input, opts) => {
    const result = await cmdDigest({
      input,
      format: opts.format,
      wait: opts.wait,
      model: opts.model,
    });
    console.log(JSON.stringify(result, null, 2));
    if ("status" in result && result.status === "failed") process.exitCode = 1;
  });

program
  .command("status")
  .argument("<runId>", "Digest run id")
  .action(async (runId) => {
    console.log(JSON.stringify(await cmdStatus(runId), null, 2));
  });

program
  .command("outputs")
  .argument("[runId]", "Optional run id")
  .action(async (runId) => {
    console.log(JSON.stringify(await cmdListOutputs(runId), null, 2));
  });

program
  .command("read")
  .argument("<runId>", "Run id")
  .option("--file <file>", "markdown | html", "markdown")
  .action(async (runId, opts) => {
    const result = await cmdReadOutput({ runId, file: opts.file });
    if ("content" in result && typeof result.content === "string") {
      console.log(result.content);
    } else {
      console.log(JSON.stringify(result, null, 2));
      process.exitCode = 1;
    }
  });

program
  .command("sync-dataset")
  .option("--dir <dir>", "Fixtures directory", "data/datasets")
  .option("--name <name>", "Langfuse dataset name", "verdant-truth")
  .action(async (opts) => {
    console.log(
      JSON.stringify(await cmdSyncDataset({ datasetDir: opts.dir, name: opts.name }), null, 2),
    );
  });

program
  .command("eval")
  .option("--dataset <name>", "Dataset name label", "verdant-truth")
  .option("--dir <dir>", "Fixtures directory", "data/datasets")
  .option("--name <name>", "Experiment name")
  .option("--model <model>", "Override LLM model")
  .action(async (opts) => {
    console.log(
      JSON.stringify(
        await cmdEval({
          dataset: opts.dataset,
          datasetDir: opts.dir,
          name: opts.name,
          model: opts.model,
        }),
        null,
        2,
      ),
    );
  });

program
  .command("worker")
  .description("Process queued digest jobs + HTTP API for the web UI")
  .option("--once", "Process a single job and exit")
  .option("--interval <ms>", "Polling interval", "2000")
  .option("--port <port>", "Worker HTTP port", process.env.WORKER_HTTP_PORT ?? "8791")
  .option("--no-http", "Disable HTTP API (poll loop only)")
  .action(async (opts) => {
    if (opts.once) {
      console.log(JSON.stringify(await cmdProcessQueueOnce(), null, 2));
      return;
    }
    if (opts.http !== false) {
      await startWorkerHttp(Number(opts.port));
      return;
    }
    const interval = Number(opts.interval);
    console.log(`Verdant worker polling every ${interval}ms`);
    for (;;) {
      const result = (await cmdProcessQueueOnce()) as { processed?: boolean };
      if (result.processed) console.log(JSON.stringify(result));
      await new Promise((r) => setTimeout(r, interval));
    }
  });

program
  .command("mcp")
  .description("Start MCP server for local agents")
  .option("--stdio", "Use stdio transport (Cursor)")
  .option("--port <port>", "HTTP port", process.env.VERDANT_MCP_PORT ?? "8790")
  .action(async (opts) => {
    if (opts.stdio) {
      await startMcpStdio();
      return;
    }
    await startMcpHttp(Number(opts.port));
  });

await program.parseAsync(process.argv);

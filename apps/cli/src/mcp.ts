import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import express from "express";
import {
  cmdCancelDigest,
  cmdDigest,
  cmdEval,
  cmdListOutputs,
  cmdReadOutput,
  cmdRerunDigest,
  cmdStatus,
  cmdSyncDataset,
} from "./commands.js";

function textResult(data: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
  };
}

export function createVerdantMcpServer(): McpServer {
  const server = new McpServer({
    name: "verdant",
    version: "0.1.0",
  });

  server.tool(
    "digest_document",
    "Digest an image or PDF under data/ into markdown and/or HTML with Mermaid support",
    {
      inputPath: z
        .string()
        .describe("Path like data/inputs/scan.pdf or absolute path under the data mount"),
      format: z.enum(["markdown", "html", "both"]).optional().describe("Output format"),
      wait: z.boolean().optional().describe("Wait for completion (default true)"),
      model: z.string().optional().describe("Override LLM model id"),
    },
    async ({ inputPath, format, wait, model }) =>
      textResult(await cmdDigest({ input: inputPath, format, wait, model })),
  );

  server.tool(
    "get_digest_status",
    "Get status and output paths for a digest run",
    { runId: z.string() },
    async ({ runId }) => textResult(await cmdStatus(runId)),
  );

  server.tool(
    "cancel_digest",
    "Cancel a queued or running digest job. Completed page digests on disk are kept.",
    { runId: z.string() },
    async ({ runId }) => textResult(await cmdCancelDigest(runId)),
  );

  server.tool(
    "rerun_digest",
    "Re-run a digest job under the same runId. Resumes saved page digests by default; pass fresh=true to re-digest every page.",
    {
      runId: z.string(),
      model: z.string().optional().describe("Override LLM model id for this attempt"),
      format: z.enum(["markdown", "html", "both"]).optional(),
      fresh: z
        .boolean()
        .optional()
        .describe("When true, delete saved page digests and start over"),
      wait: z.boolean().optional().describe("Wait for completion (default true)"),
      stitchPrompt: z.string().optional(),
      stitchPreset: z
        .enum(["default", "too_short", "too_much_garbage", "bad_structure"])
        .optional(),
    },
    async ({ runId, model, format, fresh, wait, stitchPrompt, stitchPreset }) =>
      textResult(
        await cmdRerunDigest(runId, {
          model,
          format,
          fresh,
          wait,
          stitchPrompt,
          stitchPreset,
        }),
      ),
  );

  server.tool(
    "retry_digest",
    "Resume a failed or completed digest (alias for rerun_digest without options)",
    {
      runId: z.string(),
      wait: z.boolean().optional().describe("Wait for completion (default true)"),
    },
    async ({ runId, wait }) => textResult(await cmdRerunDigest(runId, { wait })),
  );

  server.tool(
    "list_outputs",
    "List digest outputs under data/outputs",
    { runId: z.string().optional() },
    async ({ runId }) => textResult(await cmdListOutputs(runId)),
  );

  server.tool(
    "read_output",
    "Read markdown or HTML content for a completed digest run",
    {
      runId: z.string(),
      file: z.enum(["markdown", "html"]).optional(),
    },
    async ({ runId, file }) => textResult(await cmdReadOutput({ runId, file })),
  );

  server.tool(
    "sync_dataset",
    "Sync local truth fixtures from data/datasets into Langfuse (or write offline manifest)",
    {
      datasetDir: z.string().optional(),
      name: z.string().optional(),
    },
    async ({ datasetDir, name }) => textResult(await cmdSyncDataset({ datasetDir, name })),
  );

  server.tool(
    "run_eval",
    "Run evaluation of digests against local truth fixtures; returns structure + similarity scores",
    {
      datasetDir: z.string().optional(),
      dataset: z.string().optional(),
      name: z.string().optional(),
      model: z.string().optional(),
    },
    async (args) => textResult(await cmdEval(args)),
  );

  return server;
}

export async function startMcpStdio(): Promise<void> {
  const server = createVerdantMcpServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

export async function startMcpHttp(port = Number(process.env.VERDANT_MCP_PORT ?? 8790)): Promise<void> {
  const app = express();
  app.use(express.json());

  app.get("/health", (_req, res) => {
    res.json({ ok: true, service: "verdant-mcp" });
  });

  app.post("/mcp", async (req, res) => {
    const server = createVerdantMcpServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  });

  app.get("/mcp", async (req, res) => {
    const server = createVerdantMcpServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res);
  });

  app.listen(port, "0.0.0.0", () => {
    console.log(`Verdant MCP listening on http://0.0.0.0:${port}/mcp`);
  });
}

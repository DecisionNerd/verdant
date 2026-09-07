import fs from "node:fs/promises";
import path from "node:path";
import { createClient, type Client } from "@libsql/client";
import { dataRoot } from "../paths.js";
import { migrate } from "./migrate.js";
import { importFileStateIfNeeded } from "./importFiles.js";

let clientPromise: Promise<Client> | null = null;
let readyPromise: Promise<Client> | null = null;

export function resolveTursoDatabaseUrl(): string {
  const fromEnv = process.env.TURSO_DATABASE_URL?.trim();
  if (fromEnv) return fromEnv;
  return `file:${path.join(dataRoot(), "turso", "verdant.db")}`;
}

async function ensureParentDir(url: string): Promise<void> {
  if (!url.startsWith("file:")) return;
  const raw = url.replace(/^file:/, "");
  // file:/abs or file:///abs
  const filePath = raw.startsWith("//") ? raw.slice(1) : raw;
  await fs.mkdir(path.dirname(filePath), { recursive: true });
}

async function createDbClient(): Promise<Client> {
  const url = resolveTursoDatabaseUrl();
  await ensureParentDir(url);
  return createClient({ url });
}

/** Raw client (migrated). Prefer getDb() which also runs one-shot file import. */
export async function getDbClient(): Promise<Client> {
  if (!clientPromise) {
    clientPromise = (async () => {
      const client = await createDbClient();
      await migrate(client);
      return client;
    })();
  }
  return clientPromise;
}

/**
 * Ensure DB is migrated and legacy JSON state has been imported once.
 * Call at process start (worker / CLI / MCP).
 */
export async function ensureDb(): Promise<Client> {
  if (!readyPromise) {
    readyPromise = (async () => {
      const client = await getDbClient();
      await importFileStateIfNeeded(client);
      return client;
    })();
  }
  return readyPromise;
}

/** Test helper — reset singletons between tests. */
export function resetDbForTests(): void {
  clientPromise = null;
  readyPromise = null;
}

import fs from "node:fs/promises";
import path from "node:path";
import { digestDocument } from "./digest.js";
import { datasetsDir, resolveDataPath, toHostRelative } from "./paths.js";

export type FixtureCase = {
  id: string;
  inputPath: string;
  expectedMarkdown: string;
  meta?: Record<string, unknown>;
};

export type EvalScores = {
  caseId: string;
  structureScore: number;
  similarityScore: number;
  mermaidValid: boolean;
  notes: string[];
};

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

/** Token-level F1 between predicted and expected markdown. */
export function tokenF1(predicted: string, expected: string): number {
  const pred = tokenize(predicted);
  const exp = tokenize(expected);
  if (pred.length === 0 && exp.length === 0) return 1;
  if (pred.length === 0 || exp.length === 0) return 0;

  const counts = new Map<string, number>();
  for (const t of exp) counts.set(t, (counts.get(t) ?? 0) + 1);

  let overlap = 0;
  for (const t of pred) {
    const c = counts.get(t) ?? 0;
    if (c > 0) {
      overlap += 1;
      counts.set(t, c - 1);
    }
  }

  const precision = overlap / pred.length;
  const recall = overlap / exp.length;
  if (precision + recall === 0) return 0;
  return (2 * precision * recall) / (precision + recall);
}

export function evaluateStructure(markdown: string): {
  score: number;
  mermaidValid: boolean;
  notes: string[];
} {
  const notes: string[] = [];
  let points = 0;
  let total = 0;

  total += 1;
  if (/^#\s+/m.test(markdown)) {
    points += 1;
  } else {
    notes.push("Missing top-level heading");
  }

  const mermaidBlocks = [...markdown.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1] ?? "");
  if (mermaidBlocks.length > 0) {
    total += 1;
    const valid = mermaidBlocks.every((b) => /\b(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap)\b/.test(b));
    if (valid) points += 1;
    else notes.push("Mermaid block missing diagram type keyword");
    return { score: points / total, mermaidValid: valid, notes };
  }

  return { score: points / Math.max(total, 1), mermaidValid: true, notes };
}

export async function loadFixtures(root?: string): Promise<FixtureCase[]> {
  const base = root ? resolveDataPath(root) : datasetsDir();
  let entries: string[];
  try {
    entries = await fs.readdir(base);
  } catch {
    return [];
  }

  const cases: FixtureCase[] = [];
  for (const id of entries) {
    const dir = path.join(base, id);
    const stat = await fs.stat(dir).catch(() => null);
    if (!stat?.isDirectory()) continue;

    const files = await fs.readdir(dir);
    const input = files.find((f) => /^input\./i.test(f));
    const expectedFile = files.find((f) => /^expected\.md$/i.test(f));
    if (!input || !expectedFile) continue;

    const expectedMarkdown = await fs.readFile(path.join(dir, expectedFile), "utf8");
    let meta: Record<string, unknown> | undefined;
    if (files.includes("meta.json")) {
      meta = JSON.parse(await fs.readFile(path.join(dir, "meta.json"), "utf8")) as Record<
        string,
        unknown
      >;
    }

    cases.push({
      id,
      inputPath: toHostRelative(path.join(dir, input)),
      expectedMarkdown,
      meta,
    });
  }
  return cases;
}

export async function scoreFixtureCase(
  fixture: FixtureCase,
  predictedMarkdown: string,
): Promise<EvalScores> {
  const structure = evaluateStructure(predictedMarkdown);
  const similarityScore = tokenF1(predictedMarkdown, fixture.expectedMarkdown);
  return {
    caseId: fixture.id,
    structureScore: structure.score,
    similarityScore,
    mermaidValid: structure.mermaidValid,
    notes: structure.notes,
  };
}

export async function runLocalEval(options?: {
  datasetDir?: string;
  model?: string;
}): Promise<{
  dataset: string;
  results: EvalScores[];
  averages: { structure: number; similarity: number };
}> {
  const fixtures = await loadFixtures(options?.datasetDir);
  const results: EvalScores[] = [];

  for (const fixture of fixtures) {
    const digest = await digestDocument({
      inputPath: fixture.inputPath,
      format: "markdown",
      model: options?.model,
    });
    if (digest.status !== "completed" || !digest.hostPaths.markdown) {
      results.push({
        caseId: fixture.id,
        structureScore: 0,
        similarityScore: 0,
        mermaidValid: false,
        notes: [digest.error ?? "digest failed"],
      });
      continue;
    }
    const predicted = await fs.readFile(resolveDataPath(digest.hostPaths.markdown), "utf8");
    results.push(await scoreFixtureCase(fixture, predicted));
  }

  const averages = {
    structure:
      results.length === 0
        ? 0
        : results.reduce((s, r) => s + r.structureScore, 0) / results.length,
    similarity:
      results.length === 0
        ? 0
        : results.reduce((s, r) => s + r.similarityScore, 0) / results.length,
  };

  return {
    dataset: options?.datasetDir ?? "data/datasets",
    results,
    averages,
  };
}

export type LangfuseDatasetItem = {
  input: { path: string; caseId: string };
  expectedOutput: string;
  metadata?: Record<string, unknown>;
};

export async function fixturesToLangfuseItems(
  datasetDir?: string,
): Promise<LangfuseDatasetItem[]> {
  const fixtures = await loadFixtures(datasetDir);
  return fixtures.map((f) => ({
    input: { path: f.inputPath, caseId: f.id },
    expectedOutput: f.expectedMarkdown,
    metadata: f.meta,
  }));
}

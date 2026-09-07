import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { diagnoseDocument, summarizePlan } from "./diagnose.js";
import { resolveProviderConfig } from "./llm.js";
import {
  ensurePageBase64,
  normalizeInputToPages,
  peekPdfPageCount,
  type PageImage,
} from "./normalize.js";
import { completeVisionChat, type ResolvedProviderConfig } from "./providers.js";
import {
  flushLangfuseTracing,
  truncateForTrace,
  withDigestTrace,
  withSpan,
} from "./observability.js";
import {
  DIGEST_SYSTEM_PROMPT,
  IR_DIGEST_SYSTEM_PROMPT,
  STITCH_SYSTEM_PROMPT,
  buildIrPageUserPrompt,
  buildSinglePageUserPrompt,
  buildStitchUserPrompt,
  stripOuterMarkdownFence,
} from "./prompts.js";
import { markdownToHtmlDocument } from "./html.js";
import { DigestCancelledError, throwIfCancelled } from "./jobControl.js";
import { inputKindFromPath } from "./normalize.js";
import { outputsDir, resolveDataPath, toHostRelative } from "./paths.js";
import {
  annotatePlanWithPdfText,
  assessExtractedPageText,
  extractPdfPageTexts,
  normalizePdfTextToMarkdown,
  pageIrFromExtractedText,
  pdfTextMode,
} from "./pdfText.js";
import {
  isRateLimitMessage,
} from "./rateLimit.js";
import { validatePageDigest } from "./validate.js";
import {
  assembleDocumentFromIrWithReviewAsync,
  applyTocPageOffset,
  compilePageMarkdown,
  cropPageFigures,
  detectTocCandidatePages,
  extractDocumentToc,
  inferDocumentStructure,
  parsePageIr,
  validatePageIr,
  writeTocJson,
  type DocumentToc,
  type PageIr,
  type StripMode,
} from "./ir/index.js";
import type {
  DigestPayload,
  DigestPlan,
  DigestResult,
  PageProgress,
  PageWorkEntry,
} from "./types.js";

const STITCH_BATCH = Math.max(2, Number(process.env.VERDANT_STITCH_BATCH ?? 8));
const STITCH_CHAR_BUDGET = Math.max(
  8_000,
  Number(process.env.VERDANT_STITCH_CHAR_BUDGET ?? 28_000),
);
const MAX_PAGE_ATTEMPTS = 2;

function extractMode(): "ir" | "markdown" {
  const raw = (process.env.VERDANT_EXTRACT_MODE ?? "ir").toLowerCase().trim();
  return raw === "markdown" ? "markdown" : "ir";
}

export type DigestProgressExtra = {
  plan?: DigestPlan;
  pageProgress?: PageProgress;
};

export type DigestProgressFn = (
  message: string,
  phase?: string,
  extra?: DigestProgressExtra,
) => void | Promise<void>;

function pageMdName(pageNumber: number): string {
  return `page-${String(pageNumber).padStart(3, "0")}.md`;
}

function pageIrName(pageNumber: number): string {
  return `page-${String(pageNumber).padStart(3, "0")}.ir.json`;
}

function pagePngName(pageNumber: number): string {
  return `page-${String(pageNumber).padStart(3, "0")}.png`;
}

function joinFallback(pageMarkdowns: string[]): string {
  return pageMarkdowns
    .map((md, i) => `<!-- page ${i + 1} -->\n\n${md.trim()}`)
    .join("\n\n");
}

export { DigestCancelledError } from "./jobControl.js";

async function mapPool<T>(
  items: T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<void>,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  let next = 0;
  const workers = Array.from(
    { length: Math.min(concurrency, Math.max(items.length, 1)) },
    async () => {
      while (true) {
        throwIfCancelled(opts?.signal);
        const i = next++;
        if (i >= items.length) return;
        await fn(items[i]!, i);
      }
    },
  );
  await Promise.all(workers);
}

async function digestOnePage(opts: {
  config: ResolvedProviderConfig;
  page: PageImage;
  totalPages: number;
  signal?: AbortSignal;
  onRateLimitRetry?: (info: {
    attempt: number;
    maxAttempts: number;
    delayMs: number;
  }) => void | Promise<void>;
}): Promise<string> {
  const loaded = await ensurePageBase64(opts.page);
  const markdown = await completeVisionChat({
    config: opts.config,
    system: DIGEST_SYSTEM_PROMPT,
    userText: buildSinglePageUserPrompt(opts.page.pageNumber, opts.totalPages),
    pages: [{ mimeType: loaded.mimeType, base64: loaded.base64 }],
    temperature: 0.2,
    signal: opts.signal,
    onRateLimitRetry: opts.onRateLimitRetry,
    observe: {
      name: `digest-page-${opts.page.pageNumber}`,
      metadata: {
        pageNumber: opts.page.pageNumber,
        totalPages: opts.totalPages,
        kind: "page-digest",
        extractMode: "markdown",
      },
    },
  });
  if (!markdown.trim()) {
    throw new Error(`Empty model response for page ${opts.page.pageNumber}`);
  }
  return stripOuterMarkdownFence(markdown);
}

async function digestOnePageIr(opts: {
  config: ResolvedProviderConfig;
  page: PageImage;
  totalPages: number;
  signal?: AbortSignal;
  onRateLimitRetry?: (info: {
    attempt: number;
    maxAttempts: number;
    delayMs: number;
  }) => void | Promise<void>;
}): Promise<PageIr> {
  const loaded = await ensurePageBase64(opts.page);
  const raw = await completeVisionChat({
    config: opts.config,
    system: IR_DIGEST_SYSTEM_PROMPT,
    userText: buildIrPageUserPrompt(opts.page.pageNumber, opts.totalPages),
    pages: [{ mimeType: loaded.mimeType, base64: loaded.base64 }],
    temperature: 0.1,
    signal: opts.signal,
    onRateLimitRetry: opts.onRateLimitRetry,
    observe: {
      name: `digest-page-ir-${opts.page.pageNumber}`,
      metadata: {
        pageNumber: opts.page.pageNumber,
        totalPages: opts.totalPages,
        kind: "page-ir",
        extractMode: "ir",
      },
    },
  });
  if (!raw.trim()) {
    throw new Error(`Empty model response for page ${opts.page.pageNumber}`);
  }
  const ir = parsePageIr(raw);
  // Authoritative page indices from pipeline (models sometimes drift).
  ir.page = opts.page.pageNumber;
  ir.pages = opts.totalPages;
  return ir;
}

async function stitchOnce(
  config: ResolvedProviderConfig,
  pageMarkdowns: string[],
  observeName: string,
): Promise<string | null> {
  try {
    const merged = await completeVisionChat({
      config,
      system: STITCH_SYSTEM_PROMPT,
      userText: buildStitchUserPrompt(pageMarkdowns),
      pages: [],
      temperature: 0.1,
      maxTokens: 8192,
      observe: {
        name: observeName,
        metadata: {
          kind: "stitch",
          parts: pageMarkdowns.length,
          chars: pageMarkdowns.reduce((n, md) => n + md.length, 0),
        },
      },
    });
    const cleaned = stripOuterMarkdownFence(merged);
    return cleaned || null;
  } catch {
    return null;
  }
}

/** Deterministic stitch: unbounded by model context; preserves every page digest. */
function joinDeterministic(pageMarkdowns: string[]): string {
  return pageMarkdowns
    .map((md) => md.trim())
    .filter(Boolean)
    .join("\n\n");
}

function stitchMode(): "code" | "llm" {
  const raw = (process.env.VERDANT_STITCH_MODE ?? "code").toLowerCase().trim();
  return raw === "llm" ? "llm" : "code";
}

/**
 * Assemble page digests into one document.
 * Default `VERDANT_STITCH_MODE=code` joins in process memory (no context-window cap).
 * Set `VERDANT_STITCH_MODE=llm` for hierarchical LLM stitch (legacy).
 */
async function stitchPageMarkdown(
  config: ResolvedProviderConfig,
  pageMarkdowns: string[],
  progress: DigestProgressFn,
): Promise<string> {
  if (pageMarkdowns.length === 0) return "";
  if (pageMarkdowns.length === 1) return pageMarkdowns[0]!;

  if (stitchMode() === "code") {
    await progress(
      `Assembling ${pageMarkdowns.length} page digests (deterministic join)…`,
      "stitch",
    );
    return joinDeterministic(pageMarkdowns);
  }

  let level = pageMarkdowns;
  let round = 1;

  while (level.length > 1) {
    const totalChars = level.reduce((n, md) => n + md.length, 0);
    const fitsOneCall =
      level.length <= STITCH_BATCH && totalChars <= STITCH_CHAR_BUDGET;

    if (fitsOneCall) {
      await progress(
        `Stitching ${level.length} section(s) into final document…`,
        "stitch",
      );
      return (
        (await stitchOnce(config, level, "stitch-final")) ?? joinFallback(level)
      );
    }

    const next: string[] = [];
    const batches = Math.ceil(level.length / STITCH_BATCH);
    for (let b = 0; b < batches; b++) {
      const batch = level.slice(b * STITCH_BATCH, (b + 1) * STITCH_BATCH);
      await progress(
        `Stitch round ${round}: batch ${b + 1}/${batches} (${batch.length} parts)…`,
        "stitch",
      );
      next.push(
        (await stitchOnce(config, batch, `stitch-r${round}-b${b + 1}`)) ??
          joinFallback(batch),
      );
    }
    level = next;
    round += 1;
  }

  return level[0]!;
}

function buildInitialProgress(plan: DigestPlan): PageProgress {
  return {
    total: plan.pageCount,
    completed: 0,
    failed: 0,
    skipped: 0,
    phase: "plan",
    pages: plan.pages.map((p) => ({
      pageNumber: p.pageNumber,
      status: p.action === "skip" ? ("skipped" as const) : ("pending" as const),
      attempts: 0,
    })),
  };
}

function recountProgress(progress: PageProgress): PageProgress {
  const completed = progress.pages.filter((p) => p.status === "done").length;
  const failed = progress.pages.filter((p) => p.status === "failed").length;
  const skipped = progress.pages.filter((p) => p.status === "skipped").length;
  return { ...progress, completed, failed, skipped };
}

function updatePageEntry(
  progress: PageProgress,
  pageNumber: number,
  patch: Partial<PageWorkEntry>,
): PageProgress {
  const pages = progress.pages.map((p) =>
    p.pageNumber === pageNumber ? { ...p, ...patch } : p,
  );
  return recountProgress({ ...progress, pages, current: pageNumber });
}

export async function digestDocument(
  payload: DigestPayload,
  opts?: { onProgress?: DigestProgressFn; signal?: AbortSignal },
): Promise<DigestResult> {
  const progress = opts?.onProgress ?? (async () => undefined);
  const signal = opts?.signal;
  throwIfCancelled(signal);
  const runId = payload.runId ?? randomUUID().slice(0, 12);
  const inputPath = resolveDataPath(payload.inputPath);
  const runDir = payload.outputDir
    ? resolveDataPath(payload.outputDir)
    : path.join(outputsDir(), runId);
  const workDir = path.join(runDir, ".work");
  const artifactsDir = path.join(runDir, "artifacts");
  const pagesMdDir = path.join(runDir, "pages");
  const inputName = path.basename(inputPath);

  await fs.mkdir(runDir, { recursive: true });
  await fs.mkdir(artifactsDir, { recursive: true });
  await fs.mkdir(pagesMdDir, { recursive: true });

  try {
    await fs.access(inputPath);
  } catch {
    await progress(`Input not found: ${payload.inputPath}`, "error");
    return {
      runId,
      status: "failed",
      pageCount: 0,
      model: payload.model ?? process.env.LLM_MODEL ?? "unknown",
      error: `Input not found: ${payload.inputPath}`,
      hostPaths: { runDir: toHostRelative(runDir) },
    };
  }

  try {
    return await withDigestTrace(
      {
        runId,
        inputPath: payload.inputPath,
        inputName,
        format: payload.format ?? "markdown",
      },
      async (trace) => {
        let pages: PageImage[] = [];
        let model = payload.model ?? "unknown";
        let plan: DigestPlan | undefined;
        let pageProgress: PageProgress | undefined;
        let pdfPageTexts: Map<number, string> | null = null;
        const isPdf = inputKindFromPath(inputPath) === "pdf";
        const textMode = pdfTextMode(payload.pdfText);

        try {
          throwIfCancelled(signal);
          await progress(`Starting digest · ${inputName}`, "start");

          pages = await withSpan("normalize", async (span) => {
            await progress("Diagnosing input…", "diagnose");
            if (isPdf) {
              const peeked = await peekPdfPageCount(inputPath);
              if (peeked != null) {
                await progress(
                  `PDF reports ${peeked} page(s) — rasterizing for review and fallback OCR…`,
                  "diagnose",
                );
                span.update({ metadata: { peekedPages: peeked } });
              } else {
                await progress("Rasterizing PDF pages (Poppler)…", "normalize");
              }
            } else {
              await progress("Normalizing image…", "normalize");
            }

            const normalized = await normalizeInputToPages(inputPath, workDir);
            await progress(
              normalized.length === 1
                ? "Normalized 1 page to disk"
                : `Normalized ${normalized.length} pages to disk`,
              "normalize",
            );
            span.update({ output: { pageCount: normalized.length } });
            return normalized;
          });

          if (isPdf && textMode !== "off") {
            pdfPageTexts = await withSpan("pdf-text", async (span) => {
              await progress("Probing PDF text layer (pdftotext)…", "diagnose");
              const texts = await extractPdfPageTexts(inputPath, pages.length);
              span.update({
                output: {
                  pagesWithText: texts?.size ?? 0,
                  mode: textMode,
                },
              });
              if (texts?.size) {
                const usable = [...texts.keys()].filter((n) => {
                  const t = texts.get(n);
                  return t && assessExtractedPageText(t).usable;
                }).length;
                await progress(
                  usable > 0
                    ? `PDF text layer: ${usable}/${pages.length} page(s) usable — will skip vision where possible`
                    : "PDF text layer present but too sparse — using vision for all pages",
                  "diagnose",
                );
              } else {
                await progress(
                  textMode === "force"
                    ? "No PDF text layer found"
                    : "No PDF text layer — using vision OCR",
                  "diagnose",
                );
              }
              return texts;
            });
            if (textMode === "force" && !pdfPageTexts) {
              throw new Error(
                "VERDANT_PDF_TEXT=force but this PDF has no extractable text layer",
              );
            }
          }

          plan = await withSpan("plan", async (span) => {
            await progress("Building page-by-page work plan…", "plan");
            const built = await diagnoseDocument({ inputPath, pages });
            if (pdfPageTexts) {
              const { pdfTextPages, visionPages } = annotatePlanWithPdfText(
                built,
                pdfPageTexts,
                textMode,
              );
              span.update({ output: { pdfTextPages, visionPages } });
            } else if (textMode !== "off") {
              annotatePlanWithPdfText(built, null, textMode);
            }
            pageProgress = buildInitialProgress(built);
            pageProgress.skipped = built.skippedBlank;
            pageProgress.phase = "plan";

            await fs.writeFile(
              path.join(runDir, "plan.json"),
              JSON.stringify(built, null, 2),
              "utf8",
            );
            await progress(`Plan: ${summarizePlan(built)}`, "plan", {
              plan: built,
              pageProgress,
            });
            span.update({
              output: {
                pageCount: built.pageCount,
                digestPages: built.digestPages,
                skippedBlank: built.skippedBlank,
                concurrency: built.concurrency,
                strategy: built.strategy,
              },
            });
            return built;
          });

          const config = resolveProviderConfig({
            model: payload.model,
            baseUrl: payload.baseUrl,
          });
          model = config.model;
          trace.update({
            metadata: {
              provider: config.provider,
              model: config.model,
            },
          });
          await progress(`Using ${config.provider} · ${config.model}`, "provider", {
            plan,
            pageProgress,
          });

          for (const page of pages) {
            const dest = path.join(artifactsDir, pagePngName(page.pageNumber));
            await fs.copyFile(page.path, dest);
          }

          const pageByNumber = new Map(pages.map((p) => [p.pageNumber, p]));
          const toDigest = plan.pages.filter((p) => p.action === "digest");

          let progressChain = Promise.resolve();
          const emitProgress = (
            message: string,
            phase: string,
            mutate?: (current: PageProgress) => PageProgress,
          ) => {
            progressChain = progressChain.then(async () => {
              if (mutate && pageProgress) {
                pageProgress = mutate(pageProgress);
              }
              await progress(message, phase, { plan, pageProgress });
            });
            return progressChain;
          };

          let documentToc: DocumentToc = {
            v: 1,
            sourcePages: [],
            entries: [],
          };
          const mode = extractMode();
          if (mode === "ir" && toDigest.length > 0) {
            const candidates = detectTocCandidatePages(pages, plan);
            await emitProgress(
              candidates.length
                ? `Scanning TOC on page(s) ${candidates.map((p) => p.pageNumber).join(", ")}…`
                : "No TOC candidate pages",
              "toc",
            );
            documentToc = await withSpan("toc", async (span) => {
              span.update({
                input: {
                  sourcePages: candidates.map((p) => p.pageNumber),
                },
              });
              const toc = await extractDocumentToc({
                config,
                pageImages: candidates,
              });
              await writeTocJson(runDir, toc);
              span.update({
                output: {
                  entries: toc.entries.length,
                  sourcePages: toc.sourcePages,
                },
              });
              return toc;
            });
          }

          pageProgress = { ...pageProgress!, phase: "llm" };
          throwIfCancelled(signal);
          await emitProgress(
            `Digesting ${toDigest.length} page(s) (concurrency ${plan.concurrency})…`,
            "llm",
          );

          await withSpan("page-digest", async (span) => {
            span.update({
              input: {
                digestPages: toDigest.length,
                concurrency: plan!.concurrency,
                provider: config.provider,
                model: config.model,
                extractMode: mode,
              },
            });

            await mapPool(
              toDigest,
              plan!.concurrency,
              async (entry) => {
                throwIfCancelled(signal);
                await withSpan(`page-${entry.pageNumber}`, async (pageSpan) => {
                pageSpan.update({
                  input: { pageNumber: entry.pageNumber, extractMode: mode },
                });

                const page = pageByNumber.get(entry.pageNumber);
                if (!page) {
                  await emitProgress(`Page ${entry.pageNumber}: missing raster`, "error", (pp) =>
                    updatePageEntry(pp, entry.pageNumber, {
                      status: "failed",
                      error: "Missing raster page",
                    }),
                  );
                  pageSpan.update({
                    level: "ERROR",
                    statusMessage: "Missing raster page",
                  });
                  return;
                }

                const mdPath = path.join(pagesMdDir, pageMdName(entry.pageNumber));
                const irPath = path.join(pagesMdDir, pageIrName(entry.pageNumber));

                if (mode === "ir") {
                  try {
                    const existingIr = parsePageIr(await fs.readFile(irPath, "utf8"));
                    const prior = validatePageIr(existingIr, {
                      page: entry.pageNumber,
                      pages: plan!.pageCount,
                    });
                    if (prior.ok || prior.keepChars > 0) {
                      await emitProgress(
                        `Page ${entry.pageNumber}/${plan!.pageCount}: resumed IR from disk`,
                        "llm",
                        (pp) =>
                          updatePageEntry(pp, entry.pageNumber, {
                            status: "done",
                            chars: prior.keepChars,
                            attempts: 0,
                            validation: prior.ok ? [] : prior.reasons,
                          }),
                      );
                      pageSpan.update({
                        output: {
                          status: "resumed",
                          chars: prior.keepChars,
                          discard: prior.discardCount,
                        },
                      });
                      return;
                    }
                  } catch {
                    // no prior IR
                  }
                } else {
                  try {
                    const existing = await fs.readFile(mdPath, "utf8");
                    const prior = validatePageDigest(existing);
                    if (prior.ok) {
                      await emitProgress(
                        `Page ${entry.pageNumber}/${plan!.pageCount}: resumed from disk`,
                        "llm",
                        (pp) =>
                          updatePageEntry(pp, entry.pageNumber, {
                            status: "done",
                            chars: prior.chars,
                            attempts: 0,
                            validation: [],
                          }),
                      );
                      pageSpan.update({
                        output: { status: "resumed", chars: prior.chars },
                      });
                      return;
                    }
                  } catch {
                    // no prior digest
                  }
                }

                if (
                  entry.extractSource === "pdf_text" &&
                  pdfPageTexts &&
                  textMode !== "off"
                ) {
                  const raw = pdfPageTexts.get(entry.pageNumber);
                  if (raw) {
                    await emitProgress(
                      `Page ${entry.pageNumber}/${plan!.pageCount}: PDF text extraction` +
                        (mode === "ir" ? " (IR)" : "") +
                        "…",
                      "llm",
                      (pp) => ({
                        ...updatePageEntry(pp, entry.pageNumber, {
                          status: "running",
                          attempts: 0,
                        }),
                        current: entry.pageNumber,
                        phase: "llm",
                      }),
                    );
                    try {
                      if (mode === "ir") {
                        const ir = pageIrFromExtractedText(
                          raw,
                          entry.pageNumber,
                          plan!.pageCount,
                        );
                        const validation = validatePageIr(ir, {
                          page: entry.pageNumber,
                          pages: plan!.pageCount,
                        });
                        const md = compilePageMarkdown(ir);
                        await fs.writeFile(
                          irPath,
                          JSON.stringify(ir, null, 2),
                          "utf8",
                        );
                        await fs.writeFile(mdPath, md, "utf8");
                        await emitProgress(
                          `Page ${entry.pageNumber}/${plan!.pageCount}: done (PDF text · ${validation.keepChars} chars)`,
                          "llm",
                          (pp) =>
                            updatePageEntry(pp, entry.pageNumber, {
                              status: "done",
                              chars: validation.keepChars,
                              attempts: 0,
                              validation: validation.ok ? [] : validation.reasons,
                            }),
                        );
                        pageSpan.update({
                          output: {
                            status: "done",
                            extractSource: "pdf_text",
                            chars: validation.keepChars,
                          },
                        });
                        return;
                      }

                      const md = normalizePdfTextToMarkdown(raw);
                      const validation = validatePageDigest(md);
                      await fs.writeFile(mdPath, md, "utf8");
                      await emitProgress(
                        `Page ${entry.pageNumber}/${plan!.pageCount}: done (PDF text · ${validation.chars} chars)`,
                        "llm",
                        (pp) =>
                          updatePageEntry(pp, entry.pageNumber, {
                            status: "done",
                            chars: validation.chars,
                            attempts: 0,
                            validation: validation.ok ? [] : validation.reasons,
                          }),
                      );
                      pageSpan.update({
                        output: {
                          status: "done",
                          extractSource: "pdf_text",
                          chars: validation.chars,
                        },
                      });
                      return;
                    } catch (err) {
                      const msg = err instanceof Error ? err.message : String(err);
                      if (textMode === "force") {
                        await emitProgress(
                          `Page ${entry.pageNumber}/${plan!.pageCount}: PDF text failed — ${msg}`,
                          "llm",
                          (pp) =>
                            updatePageEntry(pp, entry.pageNumber, {
                              status: "failed",
                              error: msg,
                              attempts: 0,
                            }),
                        );
                        pageSpan.update({
                          output: { status: "failed", error: msg },
                        });
                        return;
                      }
                      await emitProgress(
                        `Page ${entry.pageNumber}/${plan!.pageCount}: PDF text failed — falling back to vision`,
                        "llm",
                      );
                    }
                  } else if (textMode === "force") {
                    const msg = "No PDF text for page (force mode)";
                    await emitProgress(
                      `Page ${entry.pageNumber}/${plan!.pageCount}: ${msg}`,
                      "llm",
                      (pp) =>
                        updatePageEntry(pp, entry.pageNumber, {
                          status: "failed",
                          error: msg,
                          attempts: 0,
                        }),
                    );
                    pageSpan.update({ output: { status: "failed", error: msg } });
                    return;
                  }
                }

                if (
                  textMode === "force" &&
                  isPdf &&
                  entry.extractSource === "vision"
                ) {
                  const msg =
                    entry.reason ??
                    "Page lacks usable PDF text (VERDANT_PDF_TEXT=force)";
                  await emitProgress(
                    `Page ${entry.pageNumber}/${plan!.pageCount}: ${msg}`,
                    "llm",
                    (pp) =>
                      updatePageEntry(pp, entry.pageNumber, {
                        status: "failed",
                        error: msg,
                        attempts: 0,
                      }),
                  );
                  pageSpan.update({ output: { status: "failed", error: msg } });
                  return;
                }

                let lastError: string | undefined;
                let lastReasons: string[] = [];

                const onRateLimitRetry = async (info: {
                  attempt: number;
                  maxAttempts: number;
                  delayMs: number;
                }) => {
                  await emitProgress(
                    `Page ${entry.pageNumber}: rate limited (429) — waiting ${Math.round(info.delayMs / 1000)}s` +
                      ` (backoff ${info.attempt}/${info.maxAttempts})`,
                    "llm",
                    (pp) =>
                      updatePageEntry(pp, entry.pageNumber, {
                        status: "retrying",
                        attempts: info.attempt,
                      }),
                  );
                };

                for (let attempt = 1; attempt <= MAX_PAGE_ATTEMPTS; attempt++) {
                  await emitProgress(
                    `Page ${entry.pageNumber}/${plan!.pageCount}: digesting` +
                      (mode === "ir" ? " (IR)" : "") +
                      (attempt > 1 ? ` (retry ${attempt}/${MAX_PAGE_ATTEMPTS})` : "") +
                      "…",
                    "llm",
                    (pp) => ({
                      ...updatePageEntry(pp, entry.pageNumber, {
                        status: attempt === 1 ? "running" : "retrying",
                        attempts: attempt,
                      }),
                      current: entry.pageNumber,
                      phase: "llm",
                    }),
                  );

                  try {
                    if (mode === "ir") {
                      const ir = await digestOnePageIr({
                        config,
                        page,
                        totalPages: plan!.pageCount,
                        signal,
                        onRateLimitRetry,
                      });
                      const validation = validatePageIr(ir, {
                        page: entry.pageNumber,
                        pages: plan!.pageCount,
                      });

                      if (!validation.ok && attempt < MAX_PAGE_ATTEMPTS) {
                        lastReasons = validation.reasons;
                        lastError = `validation failed: ${validation.reasons.join(", ")}`;
                        await emitProgress(
                          `Page ${entry.pageNumber}: IR validation failed (${validation.reasons.join(", ")}) — retrying`,
                          "validate",
                        );
                        continue;
                      }

                      if (!validation.ok && validation.keepChars === 0) {
                        throw new Error(
                          `validation failed: ${validation.reasons.join(", ")}`,
                        );
                      }

                      const pagePng = path.join(
                        artifactsDir,
                        pagePngName(entry.pageNumber),
                      );
                      const withFigures = await cropPageFigures({
                        ir,
                        pageImagePath: pagePng,
                        artifactsDir,
                      });
                      const md = compilePageMarkdown(withFigures);
                      await fs.writeFile(
                        irPath,
                        JSON.stringify(withFigures),
                        "utf8",
                      );
                      await fs.writeFile(mdPath, md, "utf8");
                      await emitProgress(
                        `Page ${entry.pageNumber}/${plan!.pageCount}: done (${validation.keepChars} keep chars, ${validation.discardCount} discard)` +
                          (validation.ok
                            ? ""
                            : ` · soft issues: ${validation.reasons.join(", ")}`),
                        validation.ok ? "llm" : "validate",
                        (pp) =>
                          updatePageEntry(pp, entry.pageNumber, {
                            status: "done",
                            chars: validation.keepChars,
                            attempts: attempt,
                            validation: validation.ok ? [] : validation.reasons,
                            error: undefined,
                          }),
                      );
                      pageSpan.update({
                        output: {
                          status: "done",
                          chars: validation.keepChars,
                          discard: validation.discardCount,
                          attempts: attempt,
                        },
                      });
                      return;
                    }

                    const md = await digestOnePage({
                      config,
                      page,
                      totalPages: plan!.pageCount,
                      signal,
                      onRateLimitRetry,
                    });
                    const validation = validatePageDigest(md);

                    if (!validation.ok && attempt < MAX_PAGE_ATTEMPTS) {
                      lastReasons = validation.reasons;
                      lastError = `validation failed: ${validation.reasons.join(", ")}`;
                      await emitProgress(
                        `Page ${entry.pageNumber}: validation failed (${validation.reasons.join(", ")}) — retrying`,
                        "validate",
                      );
                      continue;
                    }

                    if (!validation.ok && validation.chars === 0) {
                      throw new Error(
                        `validation failed: ${validation.reasons.join(", ")}`,
                      );
                    }

                    await fs.writeFile(mdPath, md, "utf8");
                    await emitProgress(
                      `Page ${entry.pageNumber}/${plan!.pageCount}: done (${validation.chars} chars)` +
                        (validation.ok
                          ? ""
                          : ` · soft issues: ${validation.reasons.join(", ")}`),
                      validation.ok ? "llm" : "validate",
                      (pp) =>
                        updatePageEntry(pp, entry.pageNumber, {
                          status: "done",
                          chars: validation.chars,
                          attempts: attempt,
                          validation: validation.ok ? [] : validation.reasons,
                          error: undefined,
                        }),
                    );
                    pageSpan.update({
                      output: {
                        status: "done",
                        chars: validation.chars,
                        attempts: attempt,
                      },
                    });
                    return;
                  } catch (err) {
                    if (err instanceof DigestCancelledError) throw err;
                    lastError = err instanceof Error ? err.message : String(err);
                    // Rate-limit retries happen inside completeVisionChat; if we still
                    // see 429 here, attempts are exhausted — fail the page.
                    if (attempt < MAX_PAGE_ATTEMPTS && !isRateLimitMessage(lastError)) {
                      await emitProgress(
                        `Page ${entry.pageNumber}: ${lastError} — retrying`,
                        "llm",
                      );
                      continue;
                    }
                    break;
                  }
                }

                await emitProgress(
                  `Page ${entry.pageNumber}: failed — ${lastError ?? "unknown"}`,
                  "error",
                  (pp) =>
                    updatePageEntry(pp, entry.pageNumber, {
                      status: "failed",
                      attempts: MAX_PAGE_ATTEMPTS,
                      error: lastError,
                      validation: lastReasons,
                    }),
                );
                pageSpan.update({
                  level: "ERROR",
                  statusMessage: lastError ?? "unknown",
                  output: { status: "failed", error: lastError },
                });
              });
              },
              { signal },
            );

            await progressChain;
            pageProgress = recountProgress(pageProgress!);
            span.update({
              output: {
                completed: pageProgress.completed,
                failed: pageProgress.failed,
                skipped: pageProgress.skipped,
                total: pageProgress.total,
                extractMode: mode,
              },
            });
          });

          await fs.writeFile(
            path.join(runDir, "page-progress.json"),
            JSON.stringify(pageProgress, null, 2),
            "utf8",
          );

          const failedPages = pageProgress!.pages.filter((p) => p.status === "failed");
          if (failedPages.length > 0) {
            throw new Error(
              `${failedPages.length} page(s) failed: ` +
                failedPages.map((p) => `#${p.pageNumber}`).join(", "),
            );
          }

          throwIfCancelled(signal);

          await progress(
            mode === "ir"
              ? "Validating page IR for assemble…"
              : "Validating page digests for stitch…",
            "validate",
            {
              plan,
              pageProgress: { ...pageProgress!, phase: "validate" },
            },
          );

          pageProgress = { ...pageProgress!, phase: "stitch" };

          let markdown: string;
          if (mode === "ir") {
            const pageIrs: PageIr[] = [];
            for (const entry of plan.pages) {
              if (entry.action === "skip") continue;
              const irPath = path.join(pagesMdDir, pageIrName(entry.pageNumber));
              const ir = parsePageIr(await fs.readFile(irPath, "utf8"));
              const validation = validatePageIr(ir, {
                page: entry.pageNumber,
                pages: plan.pageCount,
              });
              if (!validation.ok && validation.keepChars === 0) {
                throw new Error(
                  `Page ${entry.pageNumber} invalid before assemble: ${validation.reasons.join(", ")}`,
                );
              }
              pageIrs.push(ir);
            }
            if (pageIrs.length === 0) {
              throw new Error("No page IR available to assemble");
            }

            const stitchPreset = payload.stitchPreset ?? "default";
            const stripMode: StripMode =
              stitchPreset === "too_short"
                ? "lenient"
                : stitchPreset === "too_much_garbage"
                  ? "aggressive"
                  : "normal";
            const structureEnabled =
              (process.env.VERDANT_STRUCTURE_LLM ?? "1") !== "0" &&
              pageIrs.length > 0;

            let structure = null as Awaited<
              ReturnType<typeof inferDocumentStructure>
            > | null;
            if (structureEnabled) {
              await progress(
                `Inferring document structure (${stitchPreset})…`,
                "structure",
                { plan, pageProgress },
              );
              structure = await withSpan("structure", async (span) => {
                span.update({
                  input: {
                    pages: pageIrs.length,
                    preset: stitchPreset,
                  },
                });
                const s = await inferDocumentStructure({
                  config,
                  pages: pageIrs,
                  preset: stitchPreset,
                  prompt: payload.stitchPrompt,
                  toc: documentToc,
                });
                await fs.writeFile(
                  path.join(runDir, "structure.json"),
                  JSON.stringify(s, null, 2),
                  "utf8",
                );
                span.update({
                  output: {
                    sections: s.sections.length,
                    chromePatterns: s.chromePatterns.length,
                  },
                });
                return s;
              });
            }

            if (pageIrs.length > 1) {
              await progress(
                `Assembling ${pageIrs.length} page IR(s) (code compile + chrome strip)…`,
                "stitch",
                { plan, pageProgress },
              );
            }
            markdown = await withSpan("stitch", async (span) => {
              span.update({
                input: {
                  parts: pageIrs.length,
                  mode: "ir-assemble",
                  stitchMode: "code",
                  stripMode,
                  preset: stitchPreset,
                },
              });
              const { markdown: md, review: hierarchyReview } =
                await assembleDocumentFromIrWithReviewAsync(pageIrs, {
                  structure,
                  stripMode,
                  toc: applyTocPageOffset(documentToc, pageIrs),
                  pdfTextByPage: pdfPageTexts ?? undefined,
                  visionRecheck: {
                    config,
                    runDir,
                    signal,
                  },
                });
              await fs.writeFile(
                path.join(runDir, "hierarchy-review.json"),
                JSON.stringify(hierarchyReview, null, 2),
                "utf8",
              );
              span.update({
                output: {
                  chars: md.length,
                  preview: truncateForTrace(md, 1_500),
                  hierarchy: {
                    promotions: hierarchyReview.listItemPromotions,
                    jumpFixes: hierarchyReview.jumpFixes,
                    uncertain: hierarchyReview.uncertainCount,
                  },
                },
              });
              return md;
            });
          } else {
            const pageMarkdowns: string[] = [];
            for (const entry of plan.pages) {
              if (entry.action === "skip") continue;
              const mdPath = path.join(pagesMdDir, pageMdName(entry.pageNumber));
              const md = await fs.readFile(mdPath, "utf8");
              const validation = validatePageDigest(md);
              if (!validation.ok && validation.chars === 0) {
                throw new Error(
                  `Page ${entry.pageNumber} invalid before stitch: ${validation.reasons.join(", ")}`,
                );
              }
              pageMarkdowns.push(md);
            }

            if (pageMarkdowns.length === 0) {
              throw new Error("No page digests available to stitch");
            }

            if (pageMarkdowns.length > 1) {
              await progress(
                `Bringing ${pageMarkdowns.length} page digests together…`,
                "stitch",
                { plan, pageProgress },
              );
            }

            markdown = await withSpan("stitch", async (span) => {
              span.update({ input: { parts: pageMarkdowns.length } });
              const md = await stitchPageMarkdown(config, pageMarkdowns, progress);
              span.update({
                output: {
                  chars: md.length,
                  preview: truncateForTrace(md, 1_500),
                },
              });
              return md;
            });
          }

          pageProgress = { ...pageProgress, phase: "write" };
          await progress("Writing document.md to outputs…", "write", {
            plan,
            pageProgress,
          });
          const markdownPath = path.join(runDir, "document.md");
          await fs.writeFile(markdownPath, markdown, "utf8");

          let htmlPath: string | undefined;
          const format = payload.format ?? "markdown";
          if (format === "html" || format === "both") {
            await progress("Rendering HTML…", "write", { plan, pageProgress });
            htmlPath = path.join(runDir, "document.html");
            const html = await markdownToHtmlDocument(markdown, `Verdant ${runId}`);
            await fs.writeFile(htmlPath, html, "utf8");
          }

          await fs.rm(workDir, { recursive: true, force: true });
          pageProgress = { ...pageProgress, phase: "done" };
          await progress("Saved — ready for preview", "done", { plan, pageProgress });

          const result = {
            runId,
            status: "completed" as const,
            markdownPath: format === "html" ? undefined : markdownPath,
            htmlPath,
            artifactsDir,
            pageCount: pages.length,
            model,
            plan,
            hostPaths: {
              markdown: format === "html" ? undefined : toHostRelative(markdownPath),
              html: htmlPath ? toHostRelative(htmlPath) : undefined,
              artifacts: toHostRelative(artifactsDir),
              runDir: toHostRelative(runDir),
            },
          };
          trace.update({
            output: {
              status: result.status,
              pageCount: result.pageCount,
              model: result.model,
              markdownChars: markdown.length,
            },
          });
          return result;
        } catch (err) {
          await fs.rm(workDir, { recursive: true, force: true }).catch(() => undefined);
          if (err instanceof DigestCancelledError) throw err;
          const message = err instanceof Error ? err.message : String(err);
          await progress(`Error: ${message}`, "error", { plan, pageProgress });
          const failed = {
            runId,
            status: "failed" as const,
            pageCount: pages.length,
            model,
            error: message,
            plan,
            hostPaths: { runDir: toHostRelative(runDir) },
          };
          trace.update({
            level: "ERROR",
            statusMessage: message,
            output: {
              status: failed.status,
              pageCount: failed.pageCount,
              error: message,
              completed: pageProgress?.completed,
              failedPages: pageProgress?.failed,
            },
          });
          return failed;
        }
      },
    );
  } finally {
    await flushLangfuseTracing();
  }
}

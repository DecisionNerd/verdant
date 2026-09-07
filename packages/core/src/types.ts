import { z } from "zod";

export const DigestFormatSchema = z.enum(["markdown", "html", "both"]);
export type DigestFormat = z.infer<typeof DigestFormatSchema>;

export const DigestPayloadSchema = z.object({
  inputPath: z.string().min(1),
  format: DigestFormatSchema.default("markdown"),
  outputDir: z.string().optional(),
  model: z.string().optional(),
  baseUrl: z.string().optional(),
  runId: z.string().optional(),
  /** Optional assemble guidance applied during IR structure + stitch. */
  stitchPrompt: z.string().max(4000).optional(),
  stitchPreset: z
    .enum(["default", "too_short", "too_much_garbage", "bad_structure"])
    .optional(),
  /** Override VERDANT_PDF_TEXT for this run: auto | off | force */
  pdfText: z.enum(["auto", "off", "force"]).optional(),
});
export type DigestPayload = z.infer<typeof DigestPayloadSchema>;

export const PageKindSchema = z.enum(["content", "sparse", "blank"]);
export type PageKind = z.infer<typeof PageKindSchema>;

export const PagePlanEntrySchema = z.object({
  pageNumber: z.number().int().positive(),
  kind: PageKindSchema,
  action: z.enum(["digest", "skip"]),
  reason: z.string().optional(),
  /** When set, page digest skips vision and uses Poppler text extraction. */
  extractSource: z.enum(["pdf_text", "vision"]).optional(),
});
export type PagePlanEntry = z.infer<typeof PagePlanEntrySchema>;

export const DigestPlanSchema = z.object({
  inputKind: z.enum(["pdf", "image"]),
  pageCount: z.number().int().nonnegative(),
  digestPages: z.number().int().nonnegative(),
  skippedBlank: z.number().int().nonnegative(),
  concurrency: z.number().int().positive(),
  strategy: z.enum(["single", "multipage"]),
  pages: z.array(PagePlanEntrySchema),
});
export type DigestPlan = z.infer<typeof DigestPlanSchema>;

export const PageWorkStatusSchema = z.enum([
  "pending",
  "running",
  "done",
  "skipped",
  "failed",
  "retrying",
]);
export type PageWorkStatus = z.infer<typeof PageWorkStatusSchema>;

export const PageWorkEntrySchema = z.object({
  pageNumber: z.number().int().positive(),
  status: PageWorkStatusSchema,
  chars: z.number().int().nonnegative().optional(),
  attempts: z.number().int().nonnegative().optional(),
  error: z.string().optional(),
  validation: z.array(z.string()).optional(),
});
export type PageWorkEntry = z.infer<typeof PageWorkEntrySchema>;

export const PageProgressSchema = z.object({
  total: z.number().int().nonnegative(),
  completed: z.number().int().nonnegative(),
  failed: z.number().int().nonnegative(),
  skipped: z.number().int().nonnegative(),
  current: z.number().int().positive().optional(),
  phase: z.string().optional(),
  pages: z.array(PageWorkEntrySchema),
});
export type PageProgress = z.infer<typeof PageProgressSchema>;

export const DigestResultSchema = z.object({
  runId: z.string(),
  status: z.enum(["completed", "failed"]),
  markdownPath: z.string().optional(),
  htmlPath: z.string().optional(),
  artifactsDir: z.string().optional(),
  hostPaths: z.object({
    markdown: z.string().optional(),
    html: z.string().optional(),
    artifacts: z.string().optional(),
    runDir: z.string(),
  }),
  pageCount: z.number(),
  model: z.string(),
  error: z.string().optional(),
  plan: DigestPlanSchema.optional(),
});
export type DigestResult = z.infer<typeof DigestResultSchema>;

export const JobEventSchema = z.object({
  ts: z.string(),
  message: z.string(),
  phase: z.string().optional(),
});
export type JobEvent = z.infer<typeof JobEventSchema>;

export const JobStatusSchema = z.object({
  runId: z.string(),
  status: z.enum(["queued", "running", "completed", "failed", "cancelled"]),
  createdAt: z.string(),
  updatedAt: z.string(),
  payload: DigestPayloadSchema,
  result: DigestResultSchema.optional(),
  error: z.string().optional(),
  /** Set when cancel is requested; survives worker restarts. */
  cancelRequestedAt: z.string().optional(),
  events: z.array(JobEventSchema).optional(),
  plan: DigestPlanSchema.optional(),
  pageProgress: PageProgressSchema.optional(),
});
export type JobStatus = z.infer<typeof JobStatusSchema>;

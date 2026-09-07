import { z } from "zod";

const zone = z.enum(["top", "body", "bottom", "margin", "overlay"]);
const edge = z.enum(["top", "bottom"]);
const cont = z.enum(["start", "mid", "end"]);

const discardKind = z.enum([
  "page_number",
  "running_header",
  "running_footer",
  "folio",
  "branding",
  "decorative",
  "layout",
  "nav_chrome",
  "blank_region",
  "other_chrome",
]);

const blockBase = {
  id: z.string().min(1).max(32),
  z: zone,
  /** Optional when structured fields carry content (list items / table rows). */
  t: z.string().optional().default(""),
  edge: edge.optional(),
  uncertain: z.boolean().optional(),
};

export const discardBlockSchema = z.object({
  ...blockBase,
  k: discardKind,
});

const keepHeadingSchema = z.object({
  ...blockBase,
  k: z.literal("heading"),
  lvl: z.union([
    z.literal(1),
    z.literal(2),
    z.literal(3),
    z.literal(4),
    z.literal(5),
    z.literal(6),
  ]),
});

const keepParagraphSchema = z.object({
  ...blockBase,
  k: z.literal("paragraph"),
  cont: cont.optional(),
});

const keepListSchema = z.object({
  ...blockBase,
  k: z.literal("list"),
  ordered: z.boolean().optional(),
  items: z.array(z.string()).min(1),
  cont: cont.optional(),
});

const keepTableSchema = z.object({
  ...blockBase,
  k: z.literal("table"),
  headers: z.array(z.string()).optional(),
  rows: z.array(z.array(z.string())).optional(),
  cont: cont.optional(),
});

const keepCalloutSchema = z.object({
  ...blockBase,
  k: z.literal("callout"),
  tone: z.enum(["note", "warn", "tip", "other"]).optional(),
});

const keepFigureSchema = z.object({
  ...blockBase,
  k: z.literal("figure"),
  artifact: z.string().optional(),
  caption: z.string().optional(),
  bbox: z
    .object({
      x: z.number(),
      y: z.number(),
      w: z.number(),
      h: z.number(),
    })
    .optional(),
});

const keepMermaidSchema = z.object({
  ...blockBase,
  k: z.literal("mermaid"),
  src: z.string().min(1),
});

const keepMathSchema = z.object({
  ...blockBase,
  k: z.literal("math"),
  display: z.boolean().optional(),
});

const keepCodeSchema = z.object({
  ...blockBase,
  k: z.literal("code"),
  lang: z.string().optional(),
});

const keepCaptionSchema = z.object({
  ...blockBase,
  k: z.literal("caption"),
});

export const keepBlockSchema = z.discriminatedUnion("k", [
  keepHeadingSchema,
  keepParagraphSchema,
  keepListSchema,
  keepTableSchema,
  keepCalloutSchema,
  keepFigureSchema,
  keepMermaidSchema,
  keepMathSchema,
  keepCodeSchema,
  keepCaptionSchema,
]);

export const pageIrSchema = z.object({
  v: z.literal(1),
  page: z.number().int().positive(),
  pages: z.number().int().positive(),
  keep: z.array(keepBlockSchema),
  discard: z.array(discardBlockSchema),
});

export type PageIrParsed = z.infer<typeof pageIrSchema>;

/**
 * Dual-stream page extraction IR (ADR-0005).
 * Compile final docs from `keep` only; `discard` drives chrome fingerprints / seam cleanup.
 */

export const IR_VERSION = 1 as const;

export type IrZone = "top" | "body" | "bottom" | "margin" | "overlay";
export type IrEdge = "top" | "bottom";
export type IrContinuation = "start" | "mid" | "end";

/** Material that must never appear in the final document. */
export type DiscardKind =
  | "page_number"
  | "running_header"
  | "running_footer"
  | "folio"
  | "branding"
  | "decorative"
  | "layout"
  | "nav_chrome"
  | "blank_region"
  | "other_chrome";

/** Substantive content that may compile into the final document. */
export type KeepKind =
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "callout"
  | "figure"
  | "mermaid"
  | "math"
  | "code"
  | "caption";

export type IrBlockBase = {
  id: string;
  z: IrZone;
  /** Short for discard; full transcription for keep. */
  t: string;
  edge?: IrEdge;
  uncertain?: boolean;
};

export type DiscardBlock = IrBlockBase & {
  k: DiscardKind;
};

export type KeepHeading = IrBlockBase & {
  k: "heading";
  lvl: 1 | 2 | 3 | 4 | 5 | 6;
};

export type KeepParagraph = IrBlockBase & {
  k: "paragraph";
  cont?: IrContinuation;
};

export type KeepList = IrBlockBase & {
  k: "list";
  ordered?: boolean;
  items: string[];
  cont?: IrContinuation;
};

export type KeepTable = IrBlockBase & {
  k: "table";
  /** Prefer compact rows; `t` may hold GFM fallback. */
  headers?: string[];
  rows?: string[][];
  cont?: IrContinuation;
};

export type KeepCallout = IrBlockBase & {
  k: "callout";
  tone?: "note" | "warn" | "tip" | "other";
};

export type KeepFigure = IrBlockBase & {
  k: "figure";
  artifact?: string;
  caption?: string;
  /** Normalized page crop box (0–1). Used to write artifacts/figure-pNNN-fMM.png */
  bbox?: { x: number; y: number; w: number; h: number };
};

export type KeepMermaid = IrBlockBase & {
  k: "mermaid";
  /** Mermaid source; `t` may duplicate for search. */
  src: string;
};

export type KeepMath = IrBlockBase & {
  k: "math";
  display?: boolean;
};

export type KeepCode = IrBlockBase & {
  k: "code";
  lang?: string;
};

export type KeepCaption = IrBlockBase & {
  k: "caption";
};

export type KeepBlock =
  | KeepHeading
  | KeepParagraph
  | KeepList
  | KeepTable
  | KeepCallout
  | KeepFigure
  | KeepMermaid
  | KeepMath
  | KeepCode
  | KeepCaption;

export type PageIr = {
  v: typeof IR_VERSION;
  page: number;
  pages: number;
  keep: KeepBlock[];
  /** Exhaustive chrome / noise stream — never compiled into document.md. */
  discard: DiscardBlock[];
};

import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";
import type { KeepBlock, KeepFigure, PageIr } from "./types.js";

export type FigureBBox = { x: number; y: number; w: number; h: number };

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

type BBoxUnitMode = "frac" | "px" | "k";

/**
 * Detect how bbox numbers were encoded.
 * Vision models often mix 0–1 fractions with 0–1000 grids (e.g. y:702, w:0.2).
 */
function detectBBoxMode(
  x: number,
  y: number,
  w: number,
  h: number,
  imgW: number,
  imgH: number,
): BBoxUnitMode {
  const vals = [x, y, w, h];
  const hasFrac = vals.some((v) => v >= 0 && v <= 1);
  const over = vals.filter((v) => v > 1);
  if (over.length === 0) return "frac";
  // Mixed 0–1 + (1,1000] → treat outliers as a 0–1000 grid.
  if (hasFrac && over.every((v) => v <= 1000)) return "k";
  if (imgW > 0 && imgH > 0) {
    if (over.some((v) => v > 1000 || v > Math.max(imgW, imgH))) return "px";
    return "px";
  }
  if (over.every((v) => v <= 1000)) return "k";
  return "px";
}

function axisToFrac(v: number, dim: number, mode: BBoxUnitMode): number {
  if (!Number.isFinite(v)) return 0;
  if (v <= 1) return v;
  if (mode === "k") return v / 1000;
  if (mode === "px" && dim > 0) return v / dim;
  if (v <= 1000) return v / 1000;
  if (dim > 0) return v / dim;
  return v;
}

/**
 * Normalize a figure bbox to 0–1 page fractions.
 * Accepts pure fractions, pixels, 0–1000 grids, or mixed units.
 */
export function normalizeBBox(
  raw: Partial<FigureBBox> | undefined,
  imgW = 0,
  imgH = 0,
): FigureBBox | null {
  if (!raw) return null;
  const x0 = Number(raw.x);
  const y0 = Number(raw.y);
  const w0 = Number(raw.w);
  const h0 = Number(raw.h);
  if (![x0, y0, w0, h0].every((n) => Number.isFinite(n))) return null;

  const mode = detectBBoxMode(x0, y0, w0, h0, imgW, imgH);
  let x = axisToFrac(x0, imgW, mode);
  let y = axisToFrac(y0, imgH, mode);
  let w = axisToFrac(w0, imgW, mode);
  let h = axisToFrac(h0, imgH, mode);

  if (x > 1.5 || y > 1.5 || w > 1.5 || h > 1.5) return null;

  x = clamp01(x);
  y = clamp01(y);
  w = clamp01(w);
  h = clamp01(h);
  if (w < 0.03 || h < 0.03) return null;
  if (x + w > 1.02 || y + h > 1.02) {
    return {
      x,
      y,
      w: Math.min(w, Math.max(0.03, 1 - x)),
      h: Math.min(h, Math.max(0.03, 1 - y)),
    };
  }
  return { x, y, w, h };
}

/** True when the figure looks like a headshot/profile (not a wide diagram). */
export function looksLikePhotoFigure(block: KeepFigure): boolean {
  const blob = `${block.t} ${block.caption ?? ""}`.toLowerCase();
  return (
    /photo|portrait|headshot|profile|avatar|face|person|author|head\b/.test(blob) ||
    /@[\w.]+/.test(blob)
  );
}

/**
 * Profile crops often include adjacent name text to the right.
 * 1) Clamp wide boxes toward square (left-anchored).
 * 2) Prefer slightly under-square width so circle edges beat text bleed.
 */
export function tightenPhotoBBox(
  bbox: FigureBBox,
  imgW: number,
  imgH: number,
): FigureBBox {
  if (imgW < 8 || imgH < 8) return bbox;
  const pxH = bbox.h * imgH;
  if (pxH < 8) return bbox;
  // Target ~0.92×height: circular avatars leave less room for name glyphs on the right.
  const targetW = clamp01((pxH * 0.92) / imgW);
  if (bbox.w <= targetW * 1.02) {
    return { ...bbox, w: Math.min(bbox.w, targetW) };
  }
  return {
    ...bbox,
    w: Math.max(0.03, targetW),
  };
}

/**
 * Content-aware trim: cut after the left dense subject (circle/photo) before
 * sparser name/handle ink. Operates in page pixels, returns normalized bbox.
 */
export async function trimPhotoBBoxToSubject(
  pageImagePath: string,
  bbox: FigureBBox,
  imgW: number,
  imgH: number,
): Promise<FigureBBox> {
  const left = Math.max(0, Math.floor(bbox.x * imgW));
  const top = Math.max(0, Math.floor(bbox.y * imgH));
  // Analyze a slightly wider window than the tightened box so we can see text drop-off.
  const analyzeW = Math.max(
    8,
    Math.min(imgW - left, Math.floor(Math.max(bbox.w, bbox.h * 1.35) * imgW)),
  );
  const height = Math.max(8, Math.min(imgH - top, Math.floor(bbox.h * imgH)));
  if (analyzeW < 16 || height < 16) return tightenPhotoBBox(bbox, imgW, imgH);

  let data: Buffer;
  let info: sharp.OutputInfo;
  try {
    ({ data, info } = await sharp(pageImagePath)
      .extract({ left, top, width: analyzeW, height })
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true }));
  } catch {
    return tightenPhotoBBox(bbox, imgW, imgH);
  }

  const cols = new Array(info.width).fill(0);
  for (let y = 0; y < info.height; y++) {
    for (let x = 0; x < info.width; x++) {
      const i = (y * info.width + x) * info.channels;
      const r = data[i]!,
        g = data[i + 1]!,
        b = data[i + 2]!;
      // Page bg is near-white; subject/text is darker.
      if (r < 240 || g < 240 || b < 240) cols[x]! += 1;
    }
  }

  const dense = info.height * 0.35;
  const sparse = info.height * 0.08;
  let start = 0;
  while (start < info.width && cols[start]! < sparse) start += 1;

  // Find the left dense subject (avatar circle): take the first contiguous
  // high-density run, ending at the last dense column before a real gap.
  let firstDense = -1;
  let lastDense = -1;
  let sparseRun = 0;
  for (let x = start; x < info.width; x++) {
    const c = cols[x]!;
    if (c >= dense) {
      if (firstDense < 0) firstDense = x;
      lastDense = x;
      sparseRun = 0;
      continue;
    }
    if (firstDense < 0) continue;
    if (c <= sparse) {
      sparseRun += 1;
      // Gap after the circle (before name text) — stop.
      if (sparseRun >= Math.max(2, Math.floor(info.height * 0.02))) break;
    } else {
      sparseRun = 0;
    }
  }

  if (firstDense < 0 || lastDense < firstDense) {
    return tightenPhotoBBox(bbox, imgW, imgH);
  }

  // Vertical content bounds within the extract (trim letterboxing).
  const rows = new Array(info.height).fill(0);
  for (let y = 0; y < info.height; y++) {
    for (let x = firstDense; x <= lastDense; x++) {
      const i = (y * info.width + x) * info.channels;
      const r = data[i]!,
        g = data[i + 1]!,
        b = data[i + 2]!;
      if (r < 240 || g < 240 || b < 240) rows[y]! += 1;
    }
  }
  const rowSparse = (lastDense - firstDense + 1) * 0.08;
  let topRow = 0;
  let botRow = info.height - 1;
  while (topRow < info.height && rows[topRow]! < rowSparse) topRow += 1;
  while (botRow > topRow && rows[botRow]! < rowSparse) botRow -= 1;

  const pad = 1;
  const subjectLeft = Math.max(0, firstDense - pad);
  const subjectRight = Math.min(info.width - 1, lastDense + pad);
  const subjectTop = Math.max(0, topRow - pad);
  const subjectBot = Math.min(info.height - 1, botRow + pad);
  let subjectW = Math.max(8, subjectRight - subjectLeft + 1);
  let subjectH = Math.max(8, subjectBot - subjectTop + 1);

  // Force square on the larger side so circular avatars don't keep a wide strip.
  const side = Math.max(subjectW, subjectH);
  // Grow toward left/top of subject; clamp inside analyzed window.
  let sqLeft = subjectLeft - Math.floor((side - subjectW) / 2);
  let sqTop = subjectTop - Math.floor((side - subjectH) / 2);
  sqLeft = Math.max(0, Math.min(sqLeft, info.width - side));
  sqTop = Math.max(0, Math.min(sqTop, info.height - side));
  // Prefer not expanding right into name text: if we need to grow, grow left.
  if (subjectW < side) {
    sqLeft = Math.max(0, subjectRight + 1 - side);
  }

  const outW = clamp01(side / imgW);
  const outH = clamp01(side / imgH);
  const outX = clamp01((left + sqLeft) / imgW);
  const outY = clamp01((top + sqTop) / imgH);
  return {
    x: outX,
    y: outY,
    w: Math.max(0.03, Math.min(outW, 1 - outX)),
    h: Math.max(0.03, Math.min(outH, 1 - outY)),
  };
}

/** Normalized top edge for placement (handles 0–1000-style y). */
export function figureY(fig: KeepFigure): number {
  const yRaw = fig.bbox?.y;
  if (yRaw == null || !Number.isFinite(yRaw)) {
    if (fig.z === "top") return 0.1;
    if (fig.z === "bottom") return 0.85;
    return 0.5;
  }
  if (yRaw > 1 && yRaw <= 1000) return yRaw / 1000;
  if (yRaw > 1) return 0.5;
  return yRaw;
}

/**
 * Insert one figure into reading order using vertical position.
 * Bottom-band figures go *before* trailing bottom-zone text (photo → bio),
 * not after it.
 */
export function insertFigureByY(keep: KeepBlock[], fig: KeepFigure): KeepBlock[] {
  const yn = figureY(fig);
  const bottomish = yn > 0.65 || fig.z === "bottom" || fig.edge === "bottom";

  if (yn < 0.35 || fig.z === "top") {
    const firstHeading = keep.findIndex((b) => b.k === "heading");
    if (firstHeading >= 0) {
      return [
        ...keep.slice(0, firstHeading + 1),
        fig,
        ...keep.slice(firstHeading + 1),
      ];
    }
    return [fig, ...keep];
  }

  if (bottomish) {
    // Photo / byline sits above author bio and other bottom prose.
    let insertAt = keep.length;
    for (let i = keep.length - 1; i >= 0; i--) {
      const b = keep[i]!;
      if (b.k === "figure") continue;
      if (b.z === "bottom" || b.edge === "bottom") {
        insertAt = i;
        continue;
      }
      break;
    }
    return [...keep.slice(0, insertAt), fig, ...keep.slice(insertAt)];
  }

  const at = Math.max(1, Math.min(keep.length, Math.floor(keep.length * yn)));
  return [...keep.slice(0, at), fig, ...keep.slice(at)];
}

/**
 * Strip figures and re-insert top→bottom by bbox/zone so reading order matches
 * the page (used after every crop — digest and figure-fix).
 */
export function reorderFiguresByBBox(ir: PageIr): PageIr {
  const figures = ir.keep.filter((b): b is KeepFigure => b.k === "figure");
  if (figures.length === 0) return ir;
  const nonFigures: KeepBlock[] = ir.keep.filter((b) => b.k !== "figure");
  const sorted = [...figures].sort((a, b) => figureY(a) - figureY(b));
  let keep: KeepBlock[] = nonFigures;
  for (const fig of sorted) {
    keep = insertFigureByY(keep, fig);
  }
  return { ...ir, keep };
}

/**
 * Crop figure regions from the page raster into artifacts/figure-pNNN-fMM.png
 * and rewrite keep figure.artifact paths. Reorders figures by vertical position.
 */
export async function cropPageFigures(opts: {
  ir: PageIr;
  pageImagePath: string;
  artifactsDir: string;
}): Promise<PageIr> {
  const { ir, pageImagePath, artifactsDir } = opts;
  let meta: sharp.Metadata;
  try {
    meta = await sharp(pageImagePath).metadata();
  } catch {
    return reorderFiguresByBBox(ir);
  }
  const imgW = meta.width ?? 0;
  const imgH = meta.height ?? 0;
  if (imgW < 8 || imgH < 8) return reorderFiguresByBBox(ir);

  await fs.mkdir(artifactsDir, { recursive: true });
  const keep = [...ir.keep];
  let figIndex = 0;

  for (let i = 0; i < keep.length; i++) {
    const block = keep[i]!;
    if (block.k !== "figure") continue;
    figIndex += 1;
    const rawBBox = (block as KeepFigure & { bbox?: FigureBBox }).bbox;
    let bbox = normalizeBBox(rawBBox, imgW, imgH);
    if (!bbox) {
      if (rawBBox) {
        const { bbox: _drop, ...rest } = block as KeepFigure & { bbox?: FigureBBox };
        keep[i] = rest as KeepFigure;
      }
      continue;
    }

    if (looksLikePhotoFigure(block)) {
      // Keep a wider analysis window, then trim to the left subject (circle).
      bbox = await trimPhotoBBoxToSubject(pageImagePath, bbox, imgW, imgH);
    }

    const left = Math.max(0, Math.floor(bbox.x * imgW));
    const top = Math.max(0, Math.floor(bbox.y * imgH));
    const width = Math.max(1, Math.min(imgW - left, Math.floor(bbox.w * imgW)));
    const height = Math.max(1, Math.min(imgH - top, Math.floor(bbox.h * imgH)));
    if (width < 8 || height < 8) continue;

    const name = `figure-p${String(ir.page).padStart(3, "0")}-f${String(figIndex).padStart(2, "0")}.png`;
    const dest = path.join(artifactsDir, name);
    try {
      await sharp(pageImagePath)
        .extract({ left, top, width, height })
        .png()
        .toFile(dest);
      keep[i] = {
        ...block,
        bbox,
        z:
          block.z ??
          (figureY({ ...block, bbox }) > 0.65
            ? "bottom"
            : figureY({ ...block, bbox }) < 0.35
              ? "top"
              : "body"),
        artifact: `artifacts/${name}`,
      };
    } catch {
      // leave placeholder
    }
  }

  return reorderFiguresByBBox({ ...ir, keep });
}

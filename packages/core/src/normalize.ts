import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import sharp from "sharp";

const execFileAsync = promisify(execFile);

export type PageImage = {
  index: number;
  path: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  /** Present only when eagerly loaded; prefer ensurePageBase64 for large PDFs. */
  base64?: string;
  pageNumber: number;
};

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);

/** Max width sent to vision models. */
const MAX_PAGE_WIDTH = 2048;
/** Raster DPI for pdftoppm (150 is a good quality/size balance). */
const PDF_DPI = Number(process.env.VERDANT_PDF_DPI ?? 150);

async function which(bin: string): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync("sh", ["-c", `command -v ${bin}`]);
    const p = stdout.trim();
    return p || null;
  } catch {
    return null;
  }
}

/** Load page bytes from disk for a vision call (keeps large PDFs out of RAM). */
export async function ensurePageBase64(
  page: PageImage,
): Promise<PageImage & { base64: string }> {
  if (page.base64) return page as PageImage & { base64: string };
  const buf = await fs.readFile(page.path);
  return { ...page, base64: buf.toString("base64") };
}

/**
 * Rough blank/sparse detection from PNG luminance stats.
 * Near-white + low variance ⇒ blank page chrome only.
 */
export async function classifyPageImage(
  pagePath: string,
): Promise<{ kind: "content" | "sparse" | "blank"; mean: number; stdev: number }> {
  const { channels } = await sharp(pagePath).stats();
  const mean =
    channels.reduce((sum, c) => sum + c.mean, 0) / Math.max(channels.length, 1);
  const stdev =
    channels.reduce((sum, c) => sum + c.stdev, 0) / Math.max(channels.length, 1);

  if (mean > 245 && stdev < 8) return { kind: "blank", mean, stdev };
  if (mean > 235 && stdev < 18) return { kind: "sparse", mean, stdev };
  return { kind: "content", mean, stdev };
}

/** Best-effort page count before full rasterize (pdfinfo). */
export async function peekPdfPageCount(pdfPath: string): Promise<number | null> {
  const pdfinfo = await which("pdfinfo");
  if (!pdfinfo) return null;
  try {
    const { stdout } = await execFileAsync(pdfinfo, [pdfPath], {
      maxBuffer: 2 * 1024 * 1024,
    });
    const match = /^Pages:\s+(\d+)\s*$/m.exec(stdout);
    return match ? Number(match[1]) : null;
  } catch {
    return null;
  }
}

async function loadImageAsPage(filePath: string, index: number): Promise<PageImage> {
  const buf = await fs.readFile(filePath);
  const normalized = await sharp(buf)
    .rotate()
    .resize({ width: MAX_PAGE_WIDTH, withoutEnlargement: true })
    .png()
    .toBuffer();

  return {
    index,
    pageNumber: index + 1,
    path: filePath,
    mimeType: "image/png",
    base64: normalized.toString("base64"),
  };
}

/**
 * Rasterize a PDF to one PNG per page using Poppler (`pdftoppm`).
 * Writes pages to disk; does not keep all base64 blobs in memory.
 */
async function pdfToPageImages(pdfPath: string, workDir: string): Promise<PageImage[]> {
  await fs.mkdir(workDir, { recursive: true });

  const pdftoppm = await which("pdftoppm");
  if (!pdftoppm) {
    throw new Error(
      "PDF processing requires Poppler (`pdftoppm`). " +
        "The Verdant Docker worker image includes it. " +
        "On macOS: `brew install poppler`. On Debian/Ubuntu: `apt-get install poppler-utils`.",
    );
  }

  const prefix = path.join(workDir, "page");
  try {
    await execFileAsync(
      pdftoppm,
      ["-png", "-r", String(PDF_DPI), pdfPath, prefix],
      { maxBuffer: 64 * 1024 * 1024 },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`pdftoppm failed to rasterize PDF: ${msg}`);
  }

  const files = (await fs.readdir(workDir))
    .filter((f) => /^page-\d+\.png$/i.test(f))
    .sort((a, b) => {
      const na = Number(a.match(/(\d+)/)?.[1] ?? 0);
      const nb = Number(b.match(/(\d+)/)?.[1] ?? 0);
      return na - nb;
    });

  if (files.length === 0) {
    throw new Error(
      "pdftoppm produced no page images. The PDF may be empty, encrypted, or corrupt.",
    );
  }

  const pages: PageImage[] = [];
  for (let i = 0; i < files.length; i++) {
    const src = path.join(workDir, files[i]!);
    const dest = path.join(workDir, `page-${String(i + 1).padStart(3, "0")}.png`);
    const buf = await fs.readFile(src);
    const normalized = await sharp(buf)
      .resize({ width: MAX_PAGE_WIDTH, withoutEnlargement: true })
      .png()
      .toBuffer();
    await fs.writeFile(dest, normalized);
    if (src !== dest) {
      await fs.unlink(src).catch(() => undefined);
    }
    pages.push({
      index: i,
      pageNumber: i + 1,
      path: dest,
      mimeType: "image/png",
      // Disk-first: load via ensurePageBase64 at digest time.
    });
  }

  return pages;
}

export async function normalizeInputToPages(
  inputPath: string,
  workDir: string,
): Promise<PageImage[]> {
  const ext = path.extname(inputPath).toLowerCase();
  await fs.mkdir(workDir, { recursive: true });

  if (ext === ".pdf") {
    return pdfToPageImages(inputPath, path.join(workDir, "pages"));
  }

  if (IMAGE_EXTS.has(ext)) {
    const outPath = path.join(workDir, "pages", "page-001.png");
    await fs.mkdir(path.dirname(outPath), { recursive: true });
    const page = await loadImageAsPage(inputPath, 0);
    await fs.writeFile(outPath, Buffer.from(page.base64!, "base64"));
    return [{ ...page, path: outPath, base64: undefined }];
  }

  throw new Error(`Unsupported input type: ${ext || "(none)"}. Use PNG, JPEG, WebP, or PDF.`);
}

export function inputKindFromPath(inputPath: string): "pdf" | "image" {
  return path.extname(inputPath).toLowerCase() === ".pdf" ? "pdf" : "image";
}

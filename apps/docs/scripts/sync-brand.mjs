#!/usr/bin/env node
/** Regenerate docs favicons from the canonical transparent logo in /assets. */
import sharp from "sharp";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../..");
const src = path.join(root, "assets/verdant-logo-transparent.png");
const out = path.resolve(here, "../public");
const srcAssets = path.join(root, "apps/docs/src/assets");

const sizes = [
  [16, "favicon-16.png"],
  [32, "favicon-32.png"],
  [48, "favicon-48.png"],
  [180, "apple-touch-icon.png"],
  [192, "favicon-192.png"],
  [512, "favicon-512.png"],
];

for (const [size, name] of sizes) {
  await sharp(src)
    .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(path.join(out, name));
}

// Legacy .ico request — 32px PNG (widely accepted).
await sharp(src)
  .resize(32, 32, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toFile(path.join(out, "favicon.ico"));

await fs.copyFile(src, path.join(srcAssets, "logo.png"));
await fs.copyFile(path.join(root, "assets/logo.svg"), path.join(out, "logo.svg"));

console.log("Synced docs brand assets from assets/verdant-logo-transparent.png");

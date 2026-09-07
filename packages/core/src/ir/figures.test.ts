import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  figureY,
  insertFigureByY,
  normalizeBBox,
  reorderFiguresByBBox,
  tightenPhotoBBox,
} from "./figures.js";
import type { KeepFigure, KeepHeading, KeepParagraph, PageIr } from "./types.js";

describe("normalizeBBox", () => {
  it("passes through 0–1 fractions", () => {
    assert.deepEqual(normalizeBBox({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 }, 1000, 1000), {
      x: 0.1,
      y: 0.2,
      w: 0.3,
      h: 0.4,
    });
  });

  it("treats mixed 0–1 + 0–1000 as a 0–1000 grid on outliers", () => {
    // Real failure mode: y:702 with fractional x/w/h
    assert.deepEqual(
      normalizeBBox({ x: 0.053, y: 702, w: 0.202, h: 0.108 }, 1240, 1755),
      { x: 0.053, y: 0.702, w: 0.202, h: 0.108 },
    );
  });

  it("converts pure pixel boxes with image dims", () => {
    const b = normalizeBBox({ x: 100, y: 200, w: 300, h: 400 }, 1000, 1000);
    assert.deepEqual(b, { x: 0.1, y: 0.2, w: 0.3, h: 0.4 });
  });
});

describe("tightenPhotoBBox", () => {
  it("shrinks wide crops toward under-square (left-anchored)", () => {
    const tight = tightenPhotoBBox(
      { x: 0.05, y: 0.7, w: 0.25, h: 0.1 },
      1000,
      1000,
    );
    assert.ok(tight.w < 0.25);
    assert.ok(tight.w <= 0.092 + 0.001);
    assert.equal(tight.x, 0.05);
  });

  it("also trims already-square crops slightly", () => {
    const box = { x: 0.05, y: 0.7, w: 0.11, h: 0.1 };
    const tight = tightenPhotoBBox(box, 1000, 1000);
    assert.ok(tight.w <= 0.092 + 0.001);
    assert.equal(tight.x, 0.05);
  });
});

describe("figure reading order", () => {
  const heading = (t: string): KeepHeading => ({
    id: "h1",
    k: "heading",
    t,
    z: "top",
    lvl: 1,
  });
  const para = (id: string, t: string, z: "body" | "bottom"): KeepParagraph => ({
    id,
    k: "paragraph",
    t,
    z,
  });
  const fig = (partial: Partial<KeepFigure> & { id: string }): KeepFigure => ({
    k: "figure",
    t: "Profile photo",
    caption: "Bartek @bartek",
    z: "bottom",
    bbox: { x: 0.05, y: 0.7, w: 0.14, h: 0.12 },
    ...partial,
  });

  it("places bottom figures before trailing bottom-zone bio text", () => {
    const keep = [
      heading("Introduction"),
      heading("Headline"),
      para("p1", "Body one", "body"),
      para("p2", "Body two", "body"),
      para("bio", "Author bio…", "bottom"),
    ];
    const next = insertFigureByY(keep, fig({ id: "fig1" }));
    const kinds = next.map((b) => `${b.k}:${b.id ?? (b as { t: string }).t.slice(0, 8)}`);
    assert.deepEqual(
      next.map((b) => b.k),
      ["heading", "heading", "paragraph", "paragraph", "figure", "paragraph"],
    );
    assert.equal(next[4]!.k, "figure");
    assert.equal((next[5] as KeepParagraph).t, "Author bio…");
    assert.ok(kinds.includes("figure:fig1"));
  });

  it("reorderFiguresByBBox moves a misplaced bottom figure above bio", () => {
    const ir: PageIr = {
      v: 1,
      page: 3,
      pages: 6,
      keep: [
        heading("Introduction"),
        para("p1", "Body", "body"),
        para("bio", "Bio text", "bottom"),
        fig({ id: "fig1", bbox: { x: 0.05, y: 0.72, w: 0.14, h: 0.1 } }),
      ],
      discard: [],
    };
    const ordered = reorderFiguresByBBox(ir);
    assert.deepEqual(
      ordered.keep.map((b) => b.k),
      ["heading", "paragraph", "figure", "paragraph"],
    );
    assert.equal((ordered.keep[3] as KeepParagraph).t, "Bio text");
  });

  it("figureY understands 0–1000-style values", () => {
    assert.equal(figureY(fig({ id: "f", bbox: { x: 0.1, y: 700, w: 0.1, h: 0.1 } })), 0.7);
  });
});

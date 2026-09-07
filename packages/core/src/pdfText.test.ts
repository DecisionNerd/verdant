import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  annotatePlanWithPdfText,
  assessExtractedPageText,
  normalizePdfTextToMarkdown,
  pageIrFromExtractedText,
} from "./pdfText.js";
import type { DigestPlan } from "./types.js";

describe("assessExtractedPageText", () => {
  it("accepts a normal text page", () => {
    const text =
      "Introduction\n\nThis document describes the architecture of the system and how components interact across services.";
    const a = assessExtractedPageText(text);
    assert.equal(a.usable, true);
    assert.equal(a.reasons.length, 0);
  });

  it("rejects empty or scanned noise", () => {
    const a = assessExtractedPageText("   \n\n  ");
    assert.equal(a.usable, false);
    assert.ok(a.reasons.includes("too_short"));
  });

  it("rejects fragmented single-character words", () => {
    const text = "a b c d e f g h i j k l m n o p q r s t u v w x y z";
    const a = assessExtractedPageText(text);
    assert.equal(a.usable, false);
    assert.ok(a.reasons.includes("fragmented"));
  });

  it("rejects multi-column mashed layout", () => {
    const text = [
      "Welcome & Setup                      Modular Plays (continued)",
      "Introduction                        Fail Safe",
      "How leverage the playbook           Gamified Progress",
      "Product strategies overview         Intent Mirroring",
      "Onboarding                          Trust Building",
      "Activation                          Experience",
      "Retention                           Habit Formation",
      "Engagement                          Premium",
    ].join("\n");
    const a = assessExtractedPageText(text);
    assert.equal(a.usable, false);
    assert.ok(a.reasons.includes("columnar_layout"));
  });

  it("rejects truncated PDF encoding artifacts", () => {
    const text = [
      "Introductio Fail Saf Gamified Progres Intent Mirrorin",
      "Intentional Frictio Investmen Onboardin Activatio",
      "Retentio Engagemen Monetisatio Conversion Optimisatio",
      "Experience Refinemen Trust Buildin Intent Shapin",
      "Habit Formatio Premium Positioning Shareabilit",
    ].join(" ");
    const a = assessExtractedPageText(text);
    assert.equal(a.usable, false);
    assert.ok(a.reasons.includes("truncated_words"));
  });
});

describe("normalizePdfTextToMarkdown", () => {
  it("de-hyphenates line breaks", () => {
    const md = normalizePdfTextToMarkdown("docu-\nment overview");
    assert.equal(md, "document overview");
  });
});

describe("pageIrFromExtractedText", () => {
  it("promotes title-like lines to headings", () => {
    const ir = pageIrFromExtractedText(
      "Personalisation\n\nWhat it is:\nIt adapts the interface to the user.\n\nWhy it works:\nRelevance reduces friction.",
      2,
      5,
    );
    assert.equal(ir.page, 2);
    const kinds = ir.keep.map((b) => b.k);
    assert.ok(kinds.includes("heading"));
    assert.ok(kinds.includes("paragraph"));
    const headings = ir.keep.filter((b) => b.k === "heading").map((b) => b.t);
    assert.ok(headings.some((t) => /personalisation/i.test(t)));
    assert.ok(headings.some((t) => /what it is/i.test(t)));
  });
});

describe("annotatePlanWithPdfText", () => {
  it("tags usable pages as pdf_text", () => {
    const plan: DigestPlan = {
      inputKind: "pdf",
      pageCount: 2,
      digestPages: 2,
      skippedBlank: 0,
      concurrency: 1,
      strategy: "multipage",
      pages: [
        { pageNumber: 1, kind: "content", action: "digest" },
        { pageNumber: 2, kind: "content", action: "digest" },
      ],
    };
    const texts = new Map([
      [
        1,
        "Chapter one with enough words to pass the heuristic quality checks for PDF text extraction.",
      ],
      [2, "x"],
    ]);
    const { pdfTextPages, visionPages } = annotatePlanWithPdfText(plan, texts, "auto");
    assert.equal(pdfTextPages, 1);
    assert.equal(visionPages, 1);
    assert.equal(plan.pages[0]?.extractSource, "pdf_text");
    assert.equal(plan.pages[1]?.extractSource, "vision");
  });

  it("forces vision when text quality is uneven across pages", () => {
    const plan: DigestPlan = {
      inputKind: "pdf",
      pageCount: 6,
      digestPages: 6,
      skippedBlank: 0,
      concurrency: 1,
      strategy: "multipage",
      pages: Array.from({ length: 6 }, (_, i) => ({
        pageNumber: i + 1,
        kind: "content" as const,
        action: "digest" as const,
      })),
    };
    const good =
      "Chapter text with enough clean words to pass the heuristic quality checks for extraction path.";
    const bad = [
      "Welcome & Setup                      Modular Plays continued",
      "Introduction                        Fail Safe Mode",
      "How leverage playbook               Gamified Progress Bars",
      "Product strategies                  Intent Mirroring Tools",
      "Onboarding flows                    Trust Building Steps",
    ].join("\n");
    const texts = new Map([
      [1, good],
      [2, bad],
      [3, bad],
      [4, bad],
      [5, good],
      [6, bad],
    ]);
    const { pdfTextPages } = annotatePlanWithPdfText(plan, texts, "auto");
    assert.equal(pdfTextPages, 0);
    assert.ok(plan.pages.every((p) => p.extractSource === "vision"));
  });
});

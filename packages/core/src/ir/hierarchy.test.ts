import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  applyVisionRecheckAnswers,
  proposeFromLine,
  recheckCandidatesWithPdfText,
  resolveHierarchy,
  softBodyBase,
  stripListMarker,
  stripOrderedPrefix,
  type LineCandidate,
} from "./hierarchy.js";
import {
  shouldPreserveCatalogNumbers,
  stripCatalogNumber,
} from "./sectionPath.js";
import type { PageIr } from "./types.js";
import { pathFromToc, type DocumentToc } from "./toc.js";

function page(
  n: number,
  breadcrumb: string | null,
  keep: PageIr["keep"],
): PageIr {
  return {
    v: 1,
    page: n,
    pages: 10,
    keep,
    discard: breadcrumb
      ? [{ id: "d1", k: "running_header", t: breadcrumb, z: "top" }]
      : [],
  };
}

describe("proposeFromLine", () => {
  it("scores lettered lines as ol_item", () => {
    const p = proposeFromLine("a. Solve larger goals");
    assert.equal(p[0]?.role, "ol_item");
    assert.ok((p[0]?.score ?? 0) >= 0.9);
  });

  it("scores bullets as ul_item", () => {
    assert.equal(proposeFromLine("- Hello world")[0]?.role, "ul_item");
  });

  it("scores template labels as heading", () => {
    const p = proposeFromLine("What it is:");
    assert.equal(p[0]?.role, "heading");
  });
});

describe("softBodyBase", () => {
  it("nests under leaf depth without an H3 ceiling", () => {
    assert.equal(softBodyBase(0), 1);
    assert.equal(softBodyBase(1), 2);
    assert.equal(softBodyBase(2), 3);
    assert.equal(softBodyBase(3), 4);
    assert.equal(softBodyBase(4), 5);
    assert.equal(softBodyBase(5), 6);
  });
});

describe("strip helpers", () => {
  it("strips list markers and ordered prefixes", () => {
    assert.equal(stripListMarker("a. Solve larger goals"), "Solve larger goals");
    assert.equal(stripOrderedPrefix("1. Already numbered"), "Already numbered");
  });

  it("strips TOC catalog numbers from section titles", () => {
    assert.equal(stripCatalogNumber("1. Onboarding"), "Onboarding");
    assert.equal(stripCatalogNumber("28. Success Moments"), "Success Moments");
    assert.equal(stripCatalogNumber("Fail Safe"), "Fail Safe");
  });

  it("preserves chapter numbers when sequence is monotonic", () => {
    assert.equal(shouldPreserveCatalogNumbers([1, 2, 3, 4]), true);
    assert.equal(shouldPreserveCatalogNumbers([1, 2, 3, 1, 2]), true); // part restart
    assert.equal(shouldPreserveCatalogNumbers([1, 9, 7, 8, 10]), false); // shuffled
  });
});

describe("resolveHierarchy", () => {
  it("turns lettered heading run into one ordered list", () => {
    const pages = [
      page(1, "Play > Fail Safe", [
        { id: "h1", k: "heading", lvl: 2, t: "Overview", z: "body" },
        { id: "h2", k: "heading", lvl: 4, t: "a. Solve larger goals", z: "body" },
        { id: "h3", k: "heading", lvl: 4, t: "b. Audit your product", z: "body" },
        { id: "h4", k: "heading", lvl: 4, t: "c. Explore routes", z: "body" },
      ]),
    ];
    const stripped = pages.map((p) => p.keep);
    const { pages: keeps, review } = resolveHierarchy(pages, stripped);
    const flat = keeps.flat();
    const list = flat.find((b) => b.k === "list");
    assert.ok(list && list.k === "list");
    assert.equal(list.ordered, true);
    assert.deepEqual(list.items, [
      "Solve larger goals",
      "Audit your product",
      "Explore routes",
    ]);
    assert.ok(review.listItemPromotions >= 2);
    assert.ok(!flat.some((b) => b.k === "heading" && /^a\./i.test(b.t)));
  });

  it("nests body under breadcrumb leaf including H4/H5 detail", () => {
    const pages = [
      page(25, "Combo Play > Trust Building > Insights & Metrics", [
        { id: "a", k: "heading", lvl: 2, t: "KPIs", z: "body" },
        { id: "b", k: "heading", lvl: 3, t: "North star", z: "body" },
      ]),
    ];
    const { pages: keeps } = resolveHierarchy(pages, pages.map((p) => p.keep));
    const headings = keeps
      .flat()
      .filter((b) => b.k === "heading")
      .map((b) => ({ lvl: b.k === "heading" ? b.lvl : 0, t: b.t }));
    assert.deepEqual(
      headings.map((h) => `${"#".repeat(h.lvl)} ${h.t}`),
      [
        "# Combo Play",
        "## Trust Building",
        "### Insights & Metrics",
        "#### KPIs",
        "##### North star",
      ],
    );
  });

  it("allows skipped levels like H1 then H3", () => {
    const pages = [
      page(1, null, [
        { id: "a", k: "heading", lvl: 1, t: "Chapter", z: "body" },
        { id: "b", k: "heading", lvl: 3, t: "Subsection without H2", z: "body" },
        { id: "c", k: "heading", lvl: 2, t: "Section after intro", z: "body" },
        { id: "d", k: "heading", lvl: 4, t: "Detail item", z: "body" },
      ]),
    ];
    const { pages: keeps, review } = resolveHierarchy(
      pages,
      pages.map((p) => p.keep),
    );
    const lvls = keeps
      .flat()
      .filter((b): b is Extract<typeof b, { k: "heading" }> => b.k === "heading")
      .map((b) => b.lvl);
    assert.deepEqual(lvls, [1, 3, 2, 4]);
    // H1→H3 is a skip, not an auto-fix
    assert.ok(
      review.issues.some((i) => i.kind === "level_skip"),
    );
    assert.equal(
      review.issues.filter((i) => i.kind === "level_jump_steep").length,
      0,
    );
  });

  it("flags steep jumps (>2) without flattening H4/H5", () => {
    const pages = [
      page(1, null, [
        { id: "a", k: "heading", lvl: 1, t: "Title", z: "body" },
        { id: "b", k: "heading", lvl: 4, t: "Deep detail", z: "body" },
      ]),
    ];
    const { pages: keeps, review } = resolveHierarchy(
      pages,
      pages.map((p) => p.keep),
    );
    const lvls = keeps
      .flat()
      .filter((b): b is Extract<typeof b, { k: "heading" }> => b.k === "heading")
      .map((b) => b.lvl);
    assert.deepEqual(lvls, [1, 4]);
    assert.ok(review.jumpFixes >= 1);
  });

  it("stabilizes template label levels", () => {
    const pages = [
      page(1, "Play > Alpha", [
        { id: "a", k: "heading", lvl: 2, t: "What it is", z: "body" },
        { id: "b", k: "heading", lvl: 2, t: "Why it works", z: "body" },
      ]),
      page(2, "Play > Beta", [
        { id: "c", k: "heading", lvl: 4, t: "What it is", z: "body" },
        { id: "d", k: "heading", lvl: 5, t: "Why it works", z: "body" },
      ]),
    ];
    const { pages: keeps } = resolveHierarchy(pages, pages.map((p) => p.keep));
    const what = keeps
      .flat()
      .filter((b) => b.k === "heading" && /what it is/i.test(b.t))
      .map((b) => (b.k === "heading" ? b.lvl : 0));
    assert.ok(what.length >= 2);
    assert.ok(what.every((l) => l === what[0]));
  });

  it("removes duplicate adjacent headings", () => {
    const pages = [
      page(1, null, [
        { id: "a", k: "heading", lvl: 2, t: "Overview", z: "body" },
        { id: "b", k: "heading", lvl: 2, t: "Overview", z: "body" },
        { id: "c", k: "paragraph", t: "Body text here.", z: "body" },
      ]),
    ];
    const { pages: keeps, review } = resolveHierarchy(
      pages,
      pages.map((p) => p.keep),
    );
    const overs = keeps
      .flat()
      .filter((b) => b.k === "heading" && b.t === "Overview");
    assert.equal(overs.length, 1);
    assert.ok(review.duplicateHeadingsRemoved >= 1);
  });

  it("recheck with PDF text demotes false heading", () => {
    const candidates: LineCandidate[] = [
      {
        page: 1,
        text: "a. Solve larger goals",
        proposed: [
          { role: "heading", level: 4, score: 0.5 },
          { role: "ol_item", score: 0.48 },
        ],
        chosen: { role: "heading", level: 4 },
        uncertain: true,
        reasons: ["close_scores"],
      },
    ];
    const n = recheckCandidatesWithPdfText(candidates, {
      1: "Intro\na. Solve larger goals\nb. Next item\n",
    });
    assert.ok(n >= 1);
    assert.equal(candidates[0]!.chosen?.role, "ol_item");
  });

  it("applyVisionRecheckAnswers updates uncertain rows", () => {
    const candidates: LineCandidate[] = [
      {
        page: 1,
        text: "Supporting Plays",
        proposed: [
          { role: "heading", level: 4, score: 0.5 },
          { role: "paragraph", score: 0.45 },
        ],
        chosen: { role: "heading", level: 4 },
        uncertain: true,
      },
    ];
    const n = applyVisionRecheckAnswers(candidates, [
      { text: "Supporting Plays", role: "heading", level: 3 },
    ]);
    assert.equal(n, 1);
    assert.equal(candidates[0]!.chosen?.level, 3);
    assert.equal(candidates[0]!.uncertain, false);
  });

  it("strips shuffled TOC catalog numbers from injected headings", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [1],
      pageOffset: 0,
      entries: [
        {
          title: "Modular Plays",
          level: 1,
          children: [
            { title: "1. Commitment", level: 2, page: 10 },
            { title: "28. Success Moments", level: 2, page: 20 },
            { title: "7. Fail Safe", level: 2, page: 30 },
          ],
        },
      ],
    };
    assert.deepEqual(pathFromToc(toc, 20, ["Success Moments"]), [
      "Modular Plays",
      "Success Moments",
    ]);

    const pages = [
      page(20, null, [
        { id: "a", k: "heading", lvl: 1, t: "Success Moments", z: "body" },
      ]),
      page(30, null, [
        { id: "b", k: "heading", lvl: 1, t: "Fail Safe", z: "body" },
      ]),
    ];
    const { pages: keeps, review } = resolveHierarchy(
      pages,
      pages.map((p) => p.keep),
      { toc },
    );
    const headings = keeps
      .flat()
      .filter((b) => b.k === "heading")
      .map((b) => b.t);
    assert.ok(headings.includes("Success Moments"));
    assert.ok(headings.includes("Fail Safe"));
    assert.ok(!headings.some((t) => /^\d+\.\s/.test(t)));
    assert.equal(review.catalogNumbersPreserved, false);
  });

  it("keeps coherent chapter numbers from TOC when page order matches", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [1],
      pageOffset: 0,
      entries: [
        {
          title: "Book",
          level: 1,
          children: [
            { title: "1. Introduction", level: 2, page: 1 },
            { title: "2. Methods", level: 2, page: 2 },
            { title: "3. Results", level: 2, page: 3 },
          ],
        },
      ],
    };
    const pages = [
      page(1, null, [
        { id: "a", k: "heading", lvl: 1, t: "Introduction", z: "body" },
      ]),
      page(2, null, [
        { id: "b", k: "heading", lvl: 1, t: "Methods", z: "body" },
      ]),
      page(3, null, [
        { id: "c", k: "heading", lvl: 1, t: "Results", z: "body" },
      ]),
    ];
    const { pages: keeps, review } = resolveHierarchy(
      pages,
      pages.map((p) => p.keep),
      { toc },
    );
    const headings = keeps
      .flat()
      .filter((b) => b.k === "heading")
      .map((b) => b.t);
    assert.ok(headings.includes("1. Introduction") || headings.includes("Introduction"));
    // Path inject should carry chapter numbers when coherent
    assert.equal(review.catalogNumbersPreserved, true);
    assert.ok(
      headings.some((t) => t === "1. Introduction" || t === "2. Methods" || t === "3. Results"),
      `expected numbered chapter headings, got ${JSON.stringify(headings)}`,
    );
  });
});

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { assembleDocumentFromIr } from "./assemble.js";
import {
  diffPathHeadings,
  parseBreadcrumbText,
  parseSectionPathFromDiscard,
  rebasePageHeadings,
} from "./sectionPath.js";
import {
  estimatePageOffset,
  pathFromToc,
  resolveSectionPath,
  type DocumentToc,
} from "./toc.js";
import type { PageIr } from "./types.js";

describe("parseBreadcrumbText", () => {
  it("parses Play > Fail Safe trails", () => {
    assert.deepEqual(parseBreadcrumbText("Play > Fail Safe"), ["Play", "Fail Safe"]);
  });

  it("parses Combo Play depth-3 trails", () => {
    assert.deepEqual(parseBreadcrumbText("Combo Play > Trust Building > Insights & Metrics"), [
      "Combo Play",
      "Trust Building",
      "Insights & Metrics",
    ]);
  });
});

describe("diffPathHeadings", () => {
  it("injects full path on first page", () => {
    assert.deepEqual(diffPathHeadings([], ["Play", "Fail Safe"]), [
      { lvl: 1, t: "Play" },
      { lvl: 2, t: "Fail Safe" },
    ]);
  });

  it("extends path on continuation page", () => {
    assert.deepEqual(
      diffPathHeadings(["Play", "Fail Safe"], ["Play", "Fail Safe", "Make it Yours"]),
      [{ lvl: 3, t: "Make it Yours" }],
    );
  });

  it("swaps sibling on new play page", () => {
    assert.deepEqual(
      diffPathHeadings(["Play", "Fail Safe", "Make it Yours"], ["Play", "Limited Offer"]),
      [{ lvl: 2, t: "Limited Offer" }],
    );
  });
});

describe("assembleDocumentFromIr section paths", () => {
  const page = (
    n: number,
    breadcrumb: string,
    headings: Array<{ lvl: 1 | 2 | 3; t: string }>,
  ): PageIr => ({
    v: 1,
    page: n,
    pages: 6,
    keep: headings.map((h, i) => ({
      id: `k${i}`,
      k: "heading" as const,
      lvl: h.lvl,
      t: h.t,
      z: "body" as const,
    })),
    discard: [
      {
        id: "d1",
        k: "running_header",
        t: breadcrumb,
        z: "top",
      },
    ],
  });

  it("builds Play > Fail Safe > Make it Yours hierarchy across pages", () => {
    const pages = [
      page(94, "Play > Fail Safe", [{ lvl: 2, t: "Overview" }]),
      page(95, "Play > Fail Safe > Make it Yours", [{ lvl: 2, t: "Steps" }]),
      page(96, "Play > Limited Offer", [{ lvl: 2, t: "Intro" }]),
    ];
    const md = assembleDocumentFromIr(pages);
    const lines = md.split("\n").filter((l) => l.startsWith("#"));
    // Body nests under leaf (depth+1); H4 under a depth-3 path is fine
    assert.deepEqual(lines, [
      "# Play",
      "## Fail Safe",
      "### Overview",
      "### Make it Yours",
      "#### Steps",
      "## Limited Offer",
      "### Intro",
    ]);
  });

  it("nests Combo Play subsections with H4/H5 under the leaf", () => {
    const pages = [
      page(25, "Combo Play > Trust Building > Insights & Metrics", [
        { lvl: 2, t: "KPIs" },
        { lvl: 3, t: "North star" },
      ]),
    ];
    const md = assembleDocumentFromIr(pages);
    assert.match(md, /^# Combo Play/m);
    assert.match(md, /^## Trust Building/m);
    assert.match(md, /^### Insights & Metrics/m);
    assert.match(md, /^#### KPIs/m);
    assert.match(md, /^##### North star/m);
  });
});

describe("rebasePageHeadings", () => {
  it("offsets under path depth", () => {
    const rebased = rebasePageHeadings(
      [
        { id: "a", k: "heading", lvl: 2, t: "A", z: "body" },
        { id: "b", k: "heading", lvl: 3, t: "B", z: "body" },
      ],
      2,
    );
    assert.equal((rebased[0] as { lvl: number }).lvl, 3);
    assert.equal((rebased[1] as { lvl: number }).lvl, 4);
  });
});

describe("parseSectionPathFromDiscard", () => {
  it("reads running_header breadcrumbs", () => {
    const path = parseSectionPathFromDiscard([
      { id: "d1", k: "running_header", t: "Play > Fail Safe", z: "top" },
    ]);
    assert.deepEqual(path, ["Play", "Fail Safe"]);
  });
});

describe("prevPath carry + TOC", () => {
  const pageNoCrumb = (
    n: number,
    headings: Array<{ lvl: 1 | 2 | 3; t: string }>,
  ): PageIr => ({
    v: 1,
    page: n,
    pages: 100,
    keep: headings.map((h, i) => ({
      id: `k${i}`,
      k: "heading" as const,
      lvl: h.lvl,
      t: h.t,
      z: "body" as const,
    })),
    discard: [],
  });

  const pageWithCrumb = (
    n: number,
    breadcrumb: string,
    headings: Array<{ lvl: 1 | 2 | 3; t: string }> = [],
  ): PageIr => ({
    v: 1,
    page: n,
    pages: 100,
    keep: headings.map((h, i) => ({
      id: `k${i}`,
      k: "heading" as const,
      lvl: h.lvl,
      t: h.t,
      z: "body" as const,
    })),
    discard: [
      { id: "d1", k: "running_header", t: breadcrumb, z: "top" },
    ],
  });

  it("carries prevPath when page 95 has no breadcrumb", () => {
    const pages = [
      pageWithCrumb(94, "Play > Fail Safe", [{ lvl: 2, t: "Overview" }]),
      pageNoCrumb(95, [{ lvl: 2, t: "More tips" }]),
    ];
    const md = assembleDocumentFromIr(pages);
    const lines = md.split("\n").filter((l) => l.startsWith("#"));
    // Page 95 should stay under Fail Safe (carry), not reset to flat H2 at root
    assert.deepEqual(lines, [
      "# Play",
      "## Fail Safe",
      "### Overview",
      "### More tips",
    ]);
  });

  it("uses TOC page match for Combo Play when breadcrumb missing", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [2],
      pageOffset: 0,
      entries: [
        {
          title: "Combo Play",
          level: 1,
          page: 20,
          children: [
            {
              title: "Trust Building",
              level: 2,
              page: 22,
              children: [
                {
                  title: "Insights & Metrics",
                  level: 3,
                  page: 25,
                },
              ],
            },
          ],
        },
      ],
    };
    const pages = [
      pageNoCrumb(25, [
        { lvl: 2, t: "KPIs" },
        { lvl: 3, t: "North star" },
      ]),
    ];
    const md = assembleDocumentFromIr(pages, { toc });
    assert.match(md, /^# Combo Play/m);
    assert.match(md, /^## Trust Building/m);
    assert.match(md, /^### Insights & Metrics/m);
    assert.match(md, /^#### KPIs/m);
  });

  it("estimatePageOffset matches TOC titles to early headings", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [1],
      entries: [
        { title: "Introduction", level: 1, page: 3 },
        { title: "Onboarding", level: 1, page: 10 },
      ],
    };
    const pages: PageIr[] = [
      {
        v: 1,
        page: 5,
        pages: 20,
        keep: [{ id: "k1", k: "heading", lvl: 1, t: "Introduction", z: "top" }],
        discard: [],
      },
    ];
    // printed 3 appears on raster 5 → offset 2
    assert.equal(estimatePageOffset(toc, pages), 2);
  });

  it("resolveSectionPath prefers breadcrumb over TOC and carry", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [1],
      pageOffset: 0,
      entries: [{ title: "Wrong", level: 1, page: 1 }],
    };
    const page: PageIr = {
      v: 1,
      page: 1,
      pages: 3,
      keep: [],
      discard: [
        { id: "d1", k: "running_header", t: "Play > Fail Safe", z: "top" },
      ],
    };
    assert.deepEqual(
      resolveSectionPath({ page, prevPath: ["Old"], toc }),
      ["Play", "Fail Safe"],
    );
  });

  it("pathFromToc fuzzy-matches keep heading to TOC leaf", () => {
    const toc: DocumentToc = {
      v: 1,
      sourcePages: [1],
      entries: [
        {
          title: "Play",
          level: 1,
          children: [
            {
              title: "Fail Safe",
              level: 2,
              children: [{ title: "Make it Yours", level: 3 }],
            },
          ],
        },
      ],
    };
    assert.deepEqual(pathFromToc(toc, 99, ["Make it Yours"]), [
      "Play",
      "Fail Safe",
      "Make it Yours",
    ]);
  });
});

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { compileKeepMarkdown, splitEmbeddedListLines } from "./compile.js";

describe("splitEmbeddedListLines", () => {
  it("splits newline-embedded bullets", () => {
    const { head, nested } = splitEmbeddedListLines(
      "1. Activation Rate\n• Formula: x\n• What it measures: y",
    );
    assert.equal(head, "1. Activation Rate");
    assert.deepEqual(nested, ["Formula: x", "What it measures: y"]);
  });
});

describe("compileKeepMarkdown lists", () => {
  it("emits numbered parents with nested bullets on their own lines", () => {
    const md = compileKeepMarkdown([
      {
        id: "l1",
        k: "list",
        ordered: true,
        items: [
          "1. Activation Rate\n• Formula: (a/b)×100\n• What it measures: first value",
          "2. Time to Value\n• Formula: average time",
        ],
        z: "body",
        t: "",
      },
    ]);
    assert.match(md, /^1\. Activation Rate$/m);
    assert.match(md, /^   - Formula: \(a\/b\)×100$/m);
    assert.match(md, /^   - What it measures: first value$/m);
    assert.match(md, /^2\. Time to Value$/m);
    assert.match(md, /^   - Formula: average time$/m);
  });

  it("treats majority-numbered unordered lists as ordered", () => {
    const md = compileKeepMarkdown([
      {
        id: "l1",
        k: "list",
        items: ["1. Alpha", "2. Beta", "3. Gamma"],
        z: "body",
        t: "",
      },
    ]);
    assert.match(md, /^1\. Alpha$/m);
    assert.match(md, /^2\. Beta$/m);
    assert.match(md, /^3\. Gamma$/m);
  });

  it("nests plain questions under interleaved numbered prompt heads", () => {
    const md = compileKeepMarkdown([
      {
        id: "l1",
        k: "list",
        items: [
          "1. To spot where personalisation could be added:",
          "Where in your product does every user currently see the same thing?",
          "Which screens feel one-size-fits-all?",
          "2. To gather smart signals:",
          "What data has the user already given us?",
        ],
        z: "body",
        t: "",
      },
    ]);
    assert.match(md, /^1\. To spot where personalisation could be added:$/m);
    assert.match(
      md,
      /^   - Where in your product does every user currently see the same thing\?$/m,
    );
    assert.match(md, /^   - Which screens feel one-size-fits-all\?$/m);
    assert.match(md, /^2\. To gather smart signals:$/m);
    assert.match(md, /^   - What data has the user already given us\?$/m);
    assert.doesNotMatch(md, /^- 1\. /m);
  });

  it("coalesces consecutive prompt-section list blocks and renumbers", () => {
    const md = compileKeepMarkdown([
      {
        id: "l1",
        k: "list",
        items: [
          "To understand the user’s job:",
          "What outcome is the user trying to achieve?",
        ],
        z: "body",
        t: "",
      },
      {
        id: "l2",
        k: "list",
        items: [
          "2. To evaluate existing copy:",
          "Does this text explain the benefit?",
        ],
        z: "body",
        t: "",
      },
    ]);
    assert.match(md, /^1\. To understand the user’s job:$/m);
    assert.match(md, /^   - What outcome is the user trying to achieve\?$/m);
    assert.match(md, /^2\. To evaluate existing copy:$/m);
    assert.match(md, /^   - Does this text explain the benefit\?$/m);
    assert.doesNotMatch(md, /^- /m);
  });
});

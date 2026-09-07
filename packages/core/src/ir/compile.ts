import type { KeepBlock, PageIr } from "./types.js";
import { stripOrderedPrefix } from "./hierarchy.js";

function isNumberedListHead(text: string): boolean {
  return /^\d+[.)]\s+\S/.test(text.trim());
}

/** Prompt-style section head: "1. To …:" or unnumbered "To understand…:" */
function isPromptSectionHead(text: string): boolean {
  const t = text.trim();
  if (isNumberedListHead(t)) {
    const body = stripOrderedPrefix(t);
    return /^To\s+/i.test(body) || /:$/.test(body);
  }
  return /^To\s+\S.+:$/i.test(t);
}

function isPromptSectionList(
  block: Extract<KeepBlock, { k: "list" }>,
): boolean {
  if (block.items.length < 2) return false;
  const first = splitEmbeddedListLines(block.items[0] ?? "").head;
  if (!isPromptSectionHead(first) && !isNumberedListHead(first)) return false;
  // Remaining items should be plain questions / bullets, not numbered peers.
  const rest = block.items.slice(1);
  const numberedRest = rest.filter((it) =>
    isNumberedListHead(splitEmbeddedListLines(it).head),
  );
  return numberedRest.length === 0;
}

function escapeCell(s: string): string {
  return s.replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function compileTable(block: Extract<KeepBlock, { k: "table" }>): string {
  if (block.headers?.length && block.rows) {
    const headers = block.headers.map(escapeCell);
    const sep = headers.map(() => "---");
    const rows = block.rows.map(
      (r) => `| ${headers.map((_, i) => escapeCell(r[i] ?? "")).join(" | ")} |`,
    );
    return [
      `| ${headers.join(" | ")} |`,
      `| ${sep.join(" | ")} |`,
      ...rows,
    ].join("\n");
  }
  return block.t.trim();
}

/** Split a list item that embeds nested bullets after the first line. */
export function splitEmbeddedListLines(item: string): {
  head: string;
  nested: string[];
} {
  const raw = item.replace(/\r\n/g, "\n").trim();
  if (!raw) return { head: "", nested: [] };

  // Prefer explicit newlines; also split dense "Head • a • b" patterns.
  let parts = raw
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean);

  if (parts.length === 1 && /•/.test(parts[0]!)) {
    const bits = parts[0]!.split(/\s*•\s*/).map((s) => s.trim()).filter(Boolean);
    if (bits.length >= 2) parts = bits;
  }

  if (parts.length <= 1) return { head: raw, nested: [] };

  const head = parts[0]!.replace(/^[•▪◦]\s+/, "");
  const nested = parts.slice(1).map((p) =>
    p
      .replace(/^[•▪◦*\-]\s+/, "")
      .replace(/^\d+[.)]\s+/, "")
      .trim(),
  );
  return { head, nested: nested.filter(Boolean) };
}

function looksNumberedItems(items: string[]): boolean {
  if (items.length < 2) return false;
  const numbered = items.filter((it) => isNumberedListHead(it));
  return numbered.length >= Math.ceil(items.length * 0.6);
}

/**
 * Make-it-Yours style: numbered (or "To …:") prompt heads interleaved with
 * plain question bullets in one flat IR list → numbered parents + nested `-`.
 */
function compileInterleavedNumberedSections(items: string[]): string | null {
  type Group = { head: string; nested: string[] };
  const groups: Group[] = [];
  let current: Group | null = null;

  for (const raw of items) {
    const { head, nested } = splitEmbeddedListLines(raw);
    if (!head && nested.length === 0) continue;

    if (isNumberedListHead(head) || isPromptSectionHead(head)) {
      current = { head: stripOrderedPrefix(head), nested: [...nested] };
      groups.push(current);
      continue;
    }

    if (!current) return null;
    current.nested.push(head.replace(/^[-*•]\s+/, ""), ...nested);
  }

  if (groups.length < 2) return null;
  if (!groups.some((g) => g.nested.length > 0)) return null;

  return emitNumberedGroups(groups);
}

function emitNumberedGroups(
  groups: Array<{ head: string; nested: string[] }>,
  startAt = 1,
): string {
  const lines: string[] = [];
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i]!;
    lines.push(`${startAt + i}. ${g.head}`.trimEnd());
    for (const n of g.nested) {
      if (!n.trim()) continue;
      lines.push(`   - ${n.trim()}`);
    }
  }
  return lines.join("\n");
}

/** One list block that is already a prompt head + question children. */
function compilePromptSectionList(
  block: Extract<KeepBlock, { k: "list" }>,
  ordinal: number,
): string {
  const { head, nested: embedded } = splitEmbeddedListLines(block.items[0] ?? "");
  const kids = [
    ...embedded,
    ...block.items.slice(1).flatMap((it) => {
      const { head: h, nested } = splitEmbeddedListLines(it);
      return [h.replace(/^[-*•]\s+/, ""), ...nested];
    }),
  ].filter((s) => s.trim());
  return emitNumberedGroups(
    [{ head: stripOrderedPrefix(head), nested: kids }],
    ordinal,
  );
}

function compileList(block: Extract<KeepBlock, { k: "list" }>): string {
  const interleaved = compileInterleavedNumberedSections(block.items);
  if (interleaved) return interleaved;

  // Lone prompt-section list (head + questions) still nests correctly.
  if (isPromptSectionList(block)) {
    const n = Number(/^(\d+)/.exec(block.items[0]!.trim())?.[1] ?? 1);
    return compilePromptSectionList(block, Number.isFinite(n) && n > 0 ? n : 1);
  }

  const ordered = Boolean(block.ordered) || looksNumberedItems(block.items);
  const lines: string[] = [];

  for (let i = 0; i < block.items.length; i++) {
    const { head, nested } = splitEmbeddedListLines(block.items[i] ?? "");
    if (!head && nested.length === 0) continue;
    const headText = ordered ? stripOrderedPrefix(head) : head.replace(/^[-*•]\s+/, "");
    const marker = ordered ? `${i + 1}.` : "-";
    lines.push(`${marker} ${headText}`.trimEnd());
    // Nested unordered bullets under this item (markdown needs indent).
    const indent = ordered ? "   " : "  ";
    for (const n of nested) {
      lines.push(`${indent}- ${n}`);
    }
  }

  return lines.join("\n");
}

/** Compile keep blocks only → Markdown (discard never included). */
export function compileKeepMarkdown(keep: KeepBlock[]): string {
  const parts: string[] = [];

  for (let i = 0; i < keep.length; ) {
    const block = keep[i]!;

    // Coalesce consecutive Make-it-Yours prompt lists into one numbered series.
    if (block.k === "list" && isPromptSectionList(block)) {
      const run: Extract<KeepBlock, { k: "list" }>[] = [];
      while (
        i < keep.length &&
        keep[i]!.k === "list" &&
        isPromptSectionList(keep[i] as Extract<KeepBlock, { k: "list" }>)
      ) {
        run.push(keep[i] as Extract<KeepBlock, { k: "list" }>);
        i += 1;
      }
      if (run.length >= 2) {
        parts.push(
          run.map((b, idx) => compilePromptSectionList(b, idx + 1)).join("\n"),
        );
      } else {
        parts.push(compileList(run[0]!));
      }
      continue;
    }

    switch (block.k) {
      case "heading": {
        const hashes = "#".repeat(block.lvl);
        parts.push(`${hashes} ${block.t.trim()}`);
        break;
      }
      case "paragraph":
        parts.push(block.t.trim());
        break;
      case "list":
        parts.push(compileList(block));
        break;
      case "table":
        parts.push(compileTable(block));
        break;
      case "callout": {
        const label = (block.tone ?? "note").toUpperCase();
        parts.push(`> **${label}:** ${block.t.trim()}`);
        break;
      }
      case "figure": {
        const alt = (block.caption ?? block.t).trim() || "Figure";
        if (block.artifact?.trim()) {
          parts.push(`![${alt}](${block.artifact.trim()})`);
        } else {
          // Avoid a fake artifacts/figure.png that 404s in the UI.
          parts.push(`*[Missing figure: ${alt}]*`);
        }
        if (block.caption?.trim()) parts.push(`*${block.caption.trim()}*`);
        else if (block.t.trim() && block.artifact?.trim()) {
          parts.push(`*${block.t.trim()}*`);
        }
        break;
      }
      case "mermaid":
        parts.push(`\`\`\`mermaid\n${block.src.trim()}\n\`\`\``);
        break;
      case "math":
        parts.push(
          block.display ? `$$\n${block.t.trim()}\n$$` : `$${block.t.trim()}$`,
        );
        break;
      case "code": {
        const lang = block.lang ?? "";
        parts.push(`\`\`\`${lang}\n${block.t.trimEnd()}\n\`\`\``);
        break;
      }
      case "caption":
        parts.push(`*${block.t.trim()}*`);
        break;
      default:
        break;
    }
    i += 1;
  }

  return parts.filter(Boolean).join("\n\n").trim() + (parts.length ? "\n" : "");
}

export function compilePageMarkdown(ir: PageIr): string {
  return compileKeepMarkdown(ir.keep);
}

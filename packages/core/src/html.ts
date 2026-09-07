import { marked } from "marked";

const PAGE_CSS = `
:root {
  --bg: #f4f7f2;
  --ink: #142018;
  --muted: #4a5c4e;
  --accent: #2f6b3a;
  --card: #ffffff;
  --border: #d5e0d6;
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(ellipse at 10% 0%, #dcefdc 0%, transparent 45%),
    radial-gradient(ellipse at 90% 10%, #cfe6d4 0%, transparent 40%),
    var(--bg);
  color: var(--ink);
  line-height: 1.65;
}
main {
  max-width: 48rem;
  margin: 0 auto;
  padding: 2.5rem 1.25rem 4rem;
}
h1, h2, h3 { line-height: 1.25; color: var(--accent); }
h1 { font-size: 2rem; margin-top: 0; }
pre, code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
}
pre {
  background: #122016;
  color: #e8f5e9;
  padding: 1rem 1.1rem;
  border-radius: 10px;
  overflow-x: auto;
}
pre.mermaid {
  background: var(--card);
  color: var(--ink);
  border: 1px solid var(--border);
  text-align: center;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.25rem 0;
  background: var(--card);
}
th, td {
  border: 1px solid var(--border);
  padding: 0.5rem 0.75rem;
  text-align: left;
}
img { max-width: 100%; height: auto; border-radius: 8px; }
blockquote {
  border-left: 4px solid var(--accent);
  margin-left: 0;
  padding-left: 1rem;
  color: var(--muted);
}
`;

export async function markdownToHtmlDocument(
  markdown: string,
  title = "Verdant Document",
): Promise<string> {
  const body = await marked.parse(markdown, { gfm: true });
  // Convert mermaid code blocks into mermaid divs for client rendering
  const withMermaid = body.replace(
    /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
    (_m, code: string) => `<pre class="mermaid">${code}</pre>`,
  );

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(title)}</title>
  <style>${PAGE_CSS}</style>
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
    mermaid.initialize({ startOnLoad: true, theme: "forest" });
  </script>
</head>
<body>
  <main>
${withMermaid}
  </main>
</body>
</html>`;
}

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

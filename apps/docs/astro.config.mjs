import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

const site = process.env.SITE || "http://localhost:4321";
const base = process.env.BASE || "/";
const withBase = (assetPath) =>
  `${base.endsWith("/") ? base : `${base}/`}${assetPath.replace(/^\//, "")}`;

export default defineConfig({
  site,
  base,
  integrations: [
    starlight({
      title: "Verdant",
      description: "Local image/PDF digestion to Markdown/HTML with Mermaid, Trigger.dev, and Langfuse evals.",
      logo: {
        src: "./src/assets/logo.png",
        alt: "Verdant",
      },
      favicon: "favicon-32.png",
      customCss: ["./src/styles/verdant.css"],
      head: [
        {
          tag: "link",
          attrs: {
            rel: "preconnect",
            href: "https://fonts.googleapis.com",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "preconnect",
            href: "https://fonts.gstatic.com",
            crossorigin: true,
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "stylesheet",
            href: "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Source+Sans+3:wght@400;600;700&display=swap",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "icon",
            href: withBase("favicon-32.png"),
            type: "image/png",
            sizes: "32x32",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "icon",
            href: withBase("favicon-192.png"),
            type: "image/png",
            sizes: "192x192",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "apple-touch-icon",
            href: withBase("apple-touch-icon.png"),
          },
        },
      ],
      social: {
        github: "https://github.com/DecisionNerd/verdant",
      },
      sidebar: [
        {
          label: "Start here",
          items: [
            { label: "Getting started", slug: "getting-started" },
            { label: "Configuration", slug: "configuration" },
          ],
        },
        {
          label: "Use Verdant",
          items: [
            { label: "Web UI", slug: "web-ui" },
            { label: "CLI", slug: "cli" },
            { label: "MCP for agents", slug: "mcp" },
            { label: "Evaluation", slug: "evaluation" },
          ],
        },
        {
          label: "Understand",
          items: [
            { label: "Architecture", slug: "architecture" },
            { label: "Custom docs & DocSlime", slug: "custom-docs" },
          ],
        },
      ],
    }),
  ],
});

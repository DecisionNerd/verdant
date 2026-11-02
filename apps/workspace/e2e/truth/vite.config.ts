import { defineConfig } from "vite";
export default defineConfig({ server: { allowedHosts: ["workspace.localhost"], proxy: Object.fromEntries(["/auth", "/api", "/health", "/mcp"].map(path => [path, "http://127.0.0.1:19287"])) } });

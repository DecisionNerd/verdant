import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/auth": "http://127.0.0.1:8788",
      "/api": "http://127.0.0.1:8788",
      "/health": "http://127.0.0.1:8788",
      "/mcp": "http://127.0.0.1:8788",
    },
  },
});

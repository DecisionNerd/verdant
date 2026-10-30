export default { server: { proxy: Object.fromEntries(["/auth", "/api", "/health", "/mcp"].map(path => [path, "http://127.0.0.1:19087"])) } };

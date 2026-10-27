# Web image preflight

Executed 2026-10-27 on Docker Desktop, native linux/arm64.

- Source: `infrastructure/compose/Dockerfile.web` and `web.conf`.
- Locked pnpm 10.26.2 installation and TypeScript/Vite production build passed.
- Candidate manifest: `sha256:05183b1395e416fc4a8376c5b5f5f14252d66bdfa40a83634a534ee3e0e0853a`.
- Runtime user: uid/gid 101; nginx runs as PID 1 on port 8080.
- Disposable container `verdant-issue12-web-probe`, published only on loopback 18791: `/health/web` returned 200 and `ready`; SPA root returned 200.
- Request containing synthetic code/state query markers produced only `GET /index.html 200` in access logs. Neither marker appeared.
- SIGTERM stopped container within the 10-second grace period with exit code 0. Container was removed.

This proves image build, static serving, query-safe access logs, non-root execution, and shutdown. The upstream API was deliberately absent in this preflight. It does not prove proxy authentication, browser login, Compose lifecycle, or production readiness; those remain required in the full bootstrap qualification.

"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function ServicesInner() {
  const params = useSearchParams();
  const error = params.get("error");
  const target = params.get("target");
  const fallback = params.get("fallback");

  return (
    <main className="services-page">
      <header className="page-head">
        <div className="page-head-copy">
          <h1>Open stack services</h1>
          <p className="lede">
            One click opens local Langfuse, Trigger, and OpenObserve with the Compose defaults.
            Digests do not require these UIs — they are for traces, evals, and job dashboards.
          </p>
        </div>
      </header>

      {error ? (
        <div className="alert alert-warning" role="alert">
          <div className="alert-body">
            <p className="alert-title">
              Couldn’t auto-open {target === "trigger" ? "Trigger" : "Langfuse"}
            </p>
            <div className="alert-text">
              <p>{error}</p>
            </div>
          </div>
          {fallback ? (
            <div className="alert-actions">
              <a className="button-link" href={fallback} target="_blank" rel="noreferrer">
                Open manually
              </a>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="services-grid">
        <section className="panel">
          <h2 className="panel-title">OpenObserve</h2>
          <p className="hint">
            Primary ops traces, logs, and metrics for Verdant digests (OTLP from the worker).
          </p>
          <div className="actions">
            <a href="/api/services/openobserve">Open OpenObserve</a>
          </div>
          <p className="hint mono">verdant@example.com · verdant1 · :18706</p>
        </section>

        <section className="panel">
          <h2 className="panel-title">Langfuse</h2>
          <p className="hint">
            Optional LLM traces, datasets, and eval experiments. Auto-login uses the seeded local
            user.
          </p>
          <div className="actions">
            <a href="/api/services/langfuse">Open Langfuse</a>
          </div>
          <p className="hint mono">
            verdant@example.com · verdant1
          </p>
        </section>

        <section className="panel">
          <h2 className="panel-title">Trigger.dev</h2>
          <p className="hint">
            Optional orchestration dashboard. Auto-login follows the local magic link (no
            email inbox needed).
          </p>
          <div className="actions">
            <a href="/api/services/trigger">Open Trigger</a>
          </div>
          <p className="hint mono">verdant@example.com</p>
        </section>

        <section className="panel">
          <h2 className="panel-title">Docs</h2>
          <p className="hint">Operator how-tos for Compose, Web UI, CLI, and MCP.</p>
          <div className="actions">
            <a href="http://localhost:18701" target="_blank" rel="noreferrer">
              Open docs
            </a>
          </div>
        </section>
      </div>
    </main>
  );
}

export default function ServicesPage() {
  return (
    <Suspense fallback={<main className="services-page"><p className="hint">Loading…</p></main>}>
      <ServicesInner />
    </Suspense>
  );
}

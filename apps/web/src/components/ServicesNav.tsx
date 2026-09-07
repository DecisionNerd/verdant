"use client";

import { useEffect, useId, useRef, useState } from "react";

export function ServicesNav({
  docsUrl = "http://localhost:18701",
}: {
  docsUrl?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="services-nav" ref={rootRef}>
      <button
        type="button"
        className="secondary services-nav-trigger"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((v) => !v)}
      >
        Services
      </button>
      {open ? (
        <div className="services-menu" id={menuId} role="menu">
          <a role="menuitem" href="/api/services/openobserve" onClick={() => setOpen(false)}>
            <span className="services-menu-title">OpenObserve</span>
            <span className="services-menu-meta">Traces & logs</span>
          </a>
          <a role="menuitem" href="/api/services/langfuse" onClick={() => setOpen(false)}>
            <span className="services-menu-title">Langfuse</span>
            <span className="services-menu-meta">Auto sign-in</span>
          </a>
          <a role="menuitem" href="/api/services/trigger" onClick={() => setOpen(false)}>
            <span className="services-menu-title">Trigger.dev</span>
            <span className="services-menu-meta">Auto sign-in</span>
          </a>
          <a
            role="menuitem"
            href={docsUrl}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
          >
            <span className="services-menu-title">Docs</span>
            <span className="services-menu-meta">New tab</span>
          </a>
          <a role="menuitem" href="/services" onClick={() => setOpen(false)}>
            <span className="services-menu-title">All services</span>
            <span className="services-menu-meta">Credentials & help</span>
          </a>
        </div>
      ) : null}
    </div>
  );
}

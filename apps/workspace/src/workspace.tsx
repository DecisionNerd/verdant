import { useCallback, useEffect, useRef, useState } from "react";
import "./style.css";
import { Catalog } from "./catalog";

type Session =
  | { authenticated: false }
  | { authenticated: true; principal_id: string; csrf_token: string; expires_at: number; display_name: string };

const messages: Record<string, string> = {
  denied: "Your account does not have workspace access. Contact your workspace administrator.",
  expired: "Sign-in expired or was already used. Start a new sign-in.",
  failed: "Sign-in could not be completed. Try again.",
  unavailable: "Organizational sign-in is unavailable. Try again shortly.",
};

function signalSessionChange() {
  try { localStorage.setItem("verdant-session-change", String(Date.now())); }
  catch { /* Session verification on focus remains available when storage is blocked. */ }
}

export function Workspace() {
  const sessionCheck = useRef(0);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const query = new URLSearchParams(window.location.search);
  const loginError = messages[query.get("auth_error") ?? ""];

  const refresh = useCallback(async () => {
    const attempt = ++sessionCheck.current;
    setVerifying(true);
    try {
      const response = await fetch("/auth/session", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("Session unavailable");
      const current: Session = await response.json();
      if (attempt !== sessionCheck.current) return;
      setSession(current);
      setVerifying(false);
      setError("");
      return current;
    } catch {
      if (attempt !== sessionCheck.current) return;
      setError("The workspace could not check your session. Retry to continue.");
    }
  }, []);

  useEffect(() => {
    const check = () => { void refresh(); };
    const visible = () => { if (document.visibilityState === "visible") check(); };
    const changed = (event: StorageEvent) => { if (event.key === "verdant-session-change") check(); };
    check();
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", check);
    window.addEventListener("storage", changed);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", check);
      window.removeEventListener("storage", changed);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  const principalId = session?.authenticated ? session.principal_id : "";
  useEffect(() => {
    // Only a change signal; no identity, token, or private data enters browser storage.
    signalSessionChange();
  }, [principalId]);
  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => {
      setExpired(true);
      setSession({ authenticated: false });
      void refresh();
    }, Math.max(0, session.expires_at * 1000 - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session, refresh]);

  async function logout() {
    if (!session?.authenticated || busy) return;
    setBusy(true);
    try {
      const response = await fetch("/auth/logout", {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": session.csrf_token },
      });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          const current = await refresh();
          if (current && !current.authenticated) { setBusy(false); return; }
        }
        throw new Error("Logout unavailable");
      }
      signalSessionChange();
      const result: { logout_url: string } = await response.json();
      window.location.assign(result.logout_url);
    } catch {
      setError("Sign-out could not be confirmed. Retry before leaving this device.");
      setBusy(false);
    }
  }

  return (
    <main>
      <header><span className="brand">Verdant</span><span>Dataset workspace</span></header>
      {session?.authenticated ? <>
        <div className="session-bar"><span>Signed in as {session.display_name}</span><button onClick={() => void logout()} disabled={busy}>Sign out</button></div>
        {error && <p role="alert">{error}</p>}
        {verifying && <p role="status">Checking current session…</p>}
        {verifying && error && <button onClick={() => void refresh()}>Retry session check</button>}
        <div hidden={verifying}><Catalog key={`${session.principal_id}:${session.csrf_token}`} principal={session.principal_id} csrf={session.csrf_token} /></div>
      </> : <section aria-labelledby="title">
        <h1 id="title">Your data.<br />A shared foundation.</h1>
        <div className="access" aria-live="polite">
          {error ? <><p role="alert">{error}</p><button onClick={() => void refresh()}>Retry session check</button></> : session === null ? <p role="status">Checking your session…</p> : <>
            <h2>{expired ? "Your session has expired" : query.has("signed_out") ? "You are signed out" : "Enter your workspace"}</h2>
            {loginError && <p role="alert">{loginError}</p>}
            {query.has("identity_unavailable") && <p role="alert">You are signed out here. Organizational sign-out could not be reached.</p>}
            <p>{expired ? "Sign in again to continue with your current workspace permissions." : "Use your organizational account to continue."}</p>
            <a className="button" href="/auth/login">Sign in with your organization</a>
            {query.get("auth_error") === "denied" && <p><a href="/auth/login?switch_account=1">Use another account</a></p>}
          </>}
        </div>
      </section>}
    </main>
  );
}


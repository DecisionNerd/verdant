import http from "node:http";

export function langfusePublicUrl() {
  return (process.env.LANGFUSE_BASE_URL || "http://localhost:18703").replace(/\/$/, "");
}

export function langfuseInternalUrl() {
  return (
    process.env.LANGFUSE_BASE_URL_INTERNAL ||
    process.env.LANGFUSE_BASE_URL ||
    "http://localhost:18703"
  ).replace(/\/$/, "");
}

export function triggerPublicUrl() {
  return (process.env.TRIGGER_API_URL || "http://localhost:18704").replace(/\/$/, "");
}

/** Reach Trigger from inside Compose (host-published port via host.docker.internal). */
export function triggerInternalUrl() {
  return (
    process.env.TRIGGER_API_URL_INTERNAL ||
    process.env.TRIGGER_API_URL ||
    "http://localhost:18704"
  ).replace(/\/$/, "");
}

export function langfuseCredentials() {
  return {
    email: process.env.LANGFUSE_INIT_USER_EMAIL || "verdant@example.com",
    password: process.env.LANGFUSE_INIT_USER_PASSWORD || "verdant1",
  };
}

export function triggerLoginEmail() {
  return process.env.TRIGGER_LOGIN_EMAIL || "verdant@example.com";
}

/** Collect Set-Cookie values from a fetch Response (Node 18+). */
export function getSetCookies(res: Response): string[] {
  const headers = res.headers as Headers & { getSetCookie?: () => string[] };
  if (typeof headers.getSetCookie === "function") {
    return headers.getSetCookie();
  }
  const single = res.headers.get("set-cookie");
  return single ? [single] : [];
}

export function cookieHeaderFromSetCookies(setCookies: string[]): string {
  return setCookies
    .map((c) => c.split(";")[0]?.trim())
    .filter(Boolean)
    .join("; ");
}

/** Forward upstream Set-Cookie to the browser (localhost cookies are not port-scoped). */
export function forwardSetCookies(setCookies: string[]): string[] {
  return setCookies.map((c) =>
    c
      // Host-only cookie for localhost so :18700 → :18703 works
      .replace(/;\s*Domain=[^;]+/gi, "")
      .replace(/;\s*Secure/gi, ""),
  );
}

export async function mintLangfuseSession(): Promise<{
  cookies: string[];
  redirectTo: string;
}> {
  const base = langfuseInternalUrl();
  const publicBase = langfusePublicUrl();
  const { email, password } = langfuseCredentials();

  const csrfRes = await fetch(`${base}/api/auth/csrf`, { cache: "no-store" });
  if (!csrfRes.ok) {
    throw new Error(`Langfuse CSRF failed (${csrfRes.status})`);
  }
  const csrfCookies = getSetCookies(csrfRes);
  const { csrfToken } = (await csrfRes.json()) as { csrfToken?: string };
  if (!csrfToken) throw new Error("Langfuse CSRF token missing");

  const signInRes = await fetch(`${base}/api/auth/callback/credentials`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: cookieHeaderFromSetCookies(csrfCookies),
    },
    body: new URLSearchParams({
      csrfToken,
      email,
      password,
      callbackUrl: publicBase,
      json: "true",
    }),
    redirect: "manual",
    cache: "no-store",
  });

  const sessionCookies = [...csrfCookies, ...getSetCookies(signInRes)];
  if (!sessionCookies.some((c) => c.includes("next-auth.session-token"))) {
    const body = await signInRes.text().catch(() => "");
    throw new Error(
      `Langfuse login failed (${signInRes.status}). Is the seed user present? ${body.slice(0, 180)}`,
    );
  }

  return { cookies: forwardSetCookies(sessionCookies), redirectTo: publicBase };
}

function dockerRequest(path: string): Promise<{ status: number; body: Buffer }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { socketPath: "/var/run/docker.sock", path, method: "GET" },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () =>
          resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks) }),
        );
      },
    );
    req.on("error", reject);
    req.setTimeout(8000, () => {
      req.destroy(new Error("Docker socket timeout"));
    });
    req.end();
  });
}

/** Demux Docker logs multiplex stream into plain text. */
function demuxDockerLogs(buf: Buffer): string {
  if (buf.length > 8 && buf[1] === 0 && buf[2] === 0 && buf[3] === 0) {
    let out = "";
    let i = 0;
    while (i + 8 <= buf.length) {
      const size = buf.readUInt32BE(i + 4);
      i += 8;
      out += buf.subarray(i, i + size).toString("utf8");
      i += size;
    }
    return out;
  }
  return buf.toString("utf8");
}

async function findTriggerWebappContainer(): Promise<string | null> {
  const configured = process.env.TRIGGER_WEBAPP_CONTAINER?.trim();
  if (configured) return configured;

  const { status, body } = await dockerRequest(
    "/containers/json?all=true&filters=" +
      encodeURIComponent(JSON.stringify({ name: ["trigger-webapp"] })),
  );
  if (status !== 200) return null;
  const list = JSON.parse(body.toString("utf8")) as { Names?: string[]; Id: string }[];
  const hit = list.find((c) => (c.Names ?? []).some((n) => n.includes("trigger-webapp")));
  if (!hit) return null;
  const name = (hit.Names ?? [])[0]?.replace(/^\//, "");
  return name || hit.Id;
}

export async function mintTriggerMagicRedirect(): Promise<string> {
  const publicBase = triggerPublicUrl();
  const base = triggerInternalUrl();
  const email = triggerLoginEmail();

  const sendRes = await fetch(`${base}/login/magic`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: publicBase,
      Referer: `${publicBase}/login/magic`,
    },
    body: new URLSearchParams({ email, action: "send" }),
    redirect: "manual",
    cache: "no-store",
  });

  if (sendRes.status !== 302 && sendRes.status !== 200) {
    throw new Error(`Trigger magic-link request failed (${sendRes.status})`);
  }

  // Give the webapp a moment to print the console email with the token URL.
  await new Promise((r) => setTimeout(r, 250));

  const container = await findTriggerWebappContainer();
  if (!container) {
    throw new Error(
      "Could not find Trigger webapp container (is the trigger service up? docker.sock mounted?)",
    );
  }

  const logs = await dockerRequest(
    `/containers/${encodeURIComponent(container)}/logs?stdout=true&stderr=true&tail=80`,
  );
  if (logs.status !== 200) {
    throw new Error(`Could not read Trigger logs (${logs.status})`);
  }

  const text = demuxDockerLogs(logs.body);
  const matches = [...text.matchAll(/https?:\/\/[^\s]+\/magic\?token=[^\s]+/g)].map(
    (m) => m[0],
  );
  const magicUrl = matches.at(-1);
  if (!magicUrl) {
    throw new Error(
      "Magic link not found in Trigger logs yet — open Trigger manually once, or retry.",
    );
  }

  // Prefer the configured public origin (ports may differ in forks).
  try {
    const u = new URL(magicUrl);
    const pub = new URL(publicBase);
    u.protocol = pub.protocol;
    u.host = pub.host;
    return u.toString();
  } catch {
    return magicUrl;
  }
}

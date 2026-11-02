import { test, expect, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { readFileSync, writeFileSync, mkdirSync, openSync, closeSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:net";

const root = resolve("../..");
const baseURL = "http://127.0.0.1:19185";
const project = `verdant-process-browser-${process.pid}`;
const compose = ["compose", "-p", project, "-f", resolve("e2e/process-compose.yaml")];
async function stop(child?: ChildProcess) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit"); process.kill(-child.pid, "SIGTERM"); await exited;
}
async function ready(url: string, timeout = 60_000) {
  await expect.poll(async () => { try { return (await fetch(url)).status; } catch { return 0; } }, { timeout }).toBe(200);
}
async function response(page: Page, suffix: string, click: () => Promise<unknown>) {
  const pending = page.waitForResponse(r => r.url().endsWith(suffix) && r.request().method() === "POST");
  await click(); const r = await pending; expect(await r.text()).not.toContain("Internal Server Error"); expect(r.ok()).toBeTruthy(); return r.json();
}

test("SSO flow versioning, profile switching, native Prefect/DataChain run, durable recovery and lineage", async ({ browser }, info) => {
  const home = info.outputPath("prefect"); mkdirSync(home, { recursive: true });
  const env = { ...process.env,
    VERDANT_DATABASE_URL: "postgresql+psycopg://postgres:process-e2e-database@127.0.0.1:19132/verdant_process_test",
    VERDANT_OIDC_ISSUER: "http://127.0.0.1:19181/realms/verdant-browser-e2e",
    VERDANT_OIDC_CLIENT_ID: "verdant-browser", VERDANT_OIDC_CLIENT_SECRET: "browser-e2e-client",
    VERDANT_APP_URL: baseURL, VERDANT_OIDC_GROUP: "/workspace", VERDANT_API_AUDIENCE: "verdant-api",
    VERDANT_SERVICE_CLIENTS: "verdant-worker", VERDANT_INSECURE_LOCAL_HTTP: "1",
    VERDANT_WORKER_CLIENT_ID: "verdant-worker", VERDANT_WORKER_CLIENT_SECRET: "process-fixture-worker",
    VERDANT_S3_ENDPOINT: "http://127.0.0.1:19133", VERDANT_S3_BROWSER_ENDPOINT: "http://127.0.0.1:19133",
    VERDANT_S3_BUCKET: "verdant-artifacts", VERDANT_S3_ACCESS_KEY: "browser-e2e", VERDANT_S3_SECRET_KEY: "browser-e2e-storage",
    PREFECT_API_URL: "http://127.0.0.1:19100/api", PREFECT_HOME: home,
    PREFECT_SERVER_ANALYTICS_ENABLED: "false", PREFECT_LOGGING_TO_API_ENABLED: "false",
    PREFECT_WORKER_QUERY_SECONDS: "1", DATACHAIN_NO_ANALYTICS: "1", VERDANT_MODEL_CALLS_ENABLED: "0",
  };
  const fixturePath = info.outputPath("fixture.json");
  const python = (...args: string[]) => execFileSync("uv", ["run", "--locked", "--group", "workflows", "python", ...args], { cwd: root, env, stdio: "pipe", timeout: 180_000 });
  const children: ChildProcess[] = [];
  function start(name: string, command: string, args: string[], cwd = root) {
    const fd = openSync(info.outputPath(`${name}.log`), "a");
    const child = spawn(command, args, { cwd, env, detached: true, stdio: ["ignore", fd, fd] }); closeSync(fd); children.push(child); return child;
  }
  const uv = (name: string, args: string[]) => start(name, "uv", ["run", "--locked", "--group", "workflows", ...args]);
  const cleanup = () => {
    for (const child of children) if (child.pid && child.exitCode === null && child.signalCode === null) { try { process.kill(-child.pid, "SIGTERM"); } catch { /* Own process already exited. */ } }
    execFileSync("docker", [...compose, "down", "--volumes", "--timeout", "2"], { stdio: "pipe", timeout: 60_000 });
  };
  process.once("exit", cleanup);
  try {
    for (const port of [19100, 19132, 19133, 19181, 19185, 19187]) await new Promise<void>((resolve, reject) => { const server = createServer(); server.once("error", reject); server.listen(port, "127.0.0.1", () => server.close(() => resolve())); });
    execFileSync("docker", [...compose, "up", "-d", "--wait", "--wait-timeout", "150"], { stdio: "pipe", timeout: 180_000 });
    await ready(`${env.VERDANT_OIDC_ISSUER}/.well-known/openid-configuration`, 90_000);
    python("scripts/seed_process_browser.py", fixturePath);
    const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
    let api = uv("api", ["uvicorn", "verdant.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "19187", "--no-access-log"]);
    start("web", "pnpm", ["exec", "vite", "--host", "127.0.0.1", "--port", "19185", "--strictPort", "--config", "e2e/process-vite.config.ts"], resolve("."));
    await ready("http://127.0.0.1:19187/health/ready"); await ready(baseURL);
    const context = await browser.newContext(); const page = await context.newPage();
    await page.goto(baseURL); await page.getByRole("link", { name: "Sign in with your organization" }).click();
    await page.getByRole("textbox", { name: "Username or email" }).fill("owner");
    await page.getByRole("textbox", { name: "Password", exact: true }).fill("browser-e2e-password");
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await expect(page.getByText("Signed in as Browser Owner")).toBeVisible();
    await page.goto(`${baseURL}/?namespace=${fixture.namespace_id}&dataset=${fixture.dataset_id}`);
    const region = page.getByRole("region", { name: "Process flows", exact: true });
    await region.getByRole("button", { name: "Add sequential step" }).click();
    await region.getByLabel("Transformation").nth(1).selectOption("lowercase");
    const flow1 = await response(page, "/process-flows", () => region.getByRole("button", { name: "Save flow version", exact: true }).click());
    expect(flow1.revision).toBe(1); expect(flow1.definition.steps).toHaveLength(2);
    await region.getByLabel("Pinned input").selectOption(fixture.candidate_id);
    const run = await response(page, "/process-runs", () => region.getByRole("button", { name: "Start pinned run", exact: true }).click());
    const selected = region.getByRole("article", { name: "Selected process run" });
    await response(page, "/actions", () => selected.getByRole("button", { name: "Pause at safe boundary" }).click());
    await expect(selected).toContainText("Status: paused");
    await stop(api); api = uv("api-restarted", ["uvicorn", "verdant.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "19187", "--no-access-log"]);
    await ready("http://127.0.0.1:19187/health/ready"); await page.reload();
    await expect(selected).toContainText("Status: paused");
    await response(page, "/actions", () => selected.getByRole("button", { name: "Resume run" }).click());
    await expect(selected).toContainText("attempt 2");
    uv("prefect", ["prefect", "server", "start", "--host", "127.0.0.1", "--port", "19100", "--no-ui", "--no-services"]);
    await ready("http://127.0.0.1:19100/api/health", 90_000);
    python("scripts/process_native_fixture.py", "register", fixturePath);
    python("scripts/process_native_fixture.py", "dispatch", fixturePath);
    uv("worker", ["prefect", "worker", "start", "--pool", "verdant-process-e2e", "--type", "process", "--limit", "1"]);
    await expect(selected).toContainText("Status: succeeded", { timeout: 120_000 });
    await expect(selected).toContainText("3/3 members");
    const detailResponse = await page.request.get(`/api/v1/namespaces/${fixture.namespace_id}/process-runs/${run.id}`);
    expect(detailResponse.ok()).toBeTruthy(); const detail = await detailResponse.json();
    const nativeId = detail.attempts.find((a: any) => a.epoch === 2).provider_run_id;
    expect(nativeId).toBeTruthy();
    const nativeResponse = await fetch(`http://127.0.0.1:19100/api/flow_runs/${nativeId}`);
    expect(nativeResponse.ok).toBeTruthy(); let nativeRun = await nativeResponse.json();
    expect(nativeRun.idempotency_key).toContain(run.id);
    await expect.poll(async () => (await (await fetch(`http://127.0.0.1:19100/api/flow_runs/${nativeId}`)).json()).state_type, { timeout: 30_000 }).toBe("COMPLETED");
    nativeRun = await (await fetch(`http://127.0.0.1:19100/api/flow_runs/${nativeId}`)).json();
    expect(detail.outputs).toHaveLength(6); expect(detail.input.generation_id).toBeTruthy();
    expect(detail.outputs.filter((o: any) => o.step_position === 1).map((o: any) => o.result.text).sort()).toEqual(["safe process text 0", "safe process text 1", "safe process text 2"]);
    expect(detail.outputs.every((o: any) => o.accepted_epoch === 2 && o.receipt_id && o.output_digest)).toBeTruthy();
    await selected.locator("summary").first().click(); await expect(selected).toContainText("Usage: input unknown");
    await region.getByText("Model profiles", { exact: true }).click();
    await region.getByLabel("Profile name", { exact: true }).fill("Comparison");
    await region.getByLabel("Model identifier", { exact: true }).fill("gemini-2.0-flash-001");
    await region.getByLabel("Operator credential reference").fill("VERDANT_MODEL_SECRET_FIXTURE");
    const gemini = await response(page, "/model-profiles", () => region.getByRole("button", { name: "Save profile version" }).click());
    expect(gemini.credential_status).toBe("worker_unverified"); expect(gemini.credential_ref).toBeUndefined();
    await region.getByLabel("Provider", { exact: true }).selectOption("glm");
    await region.getByLabel("Model identifier", { exact: true }).fill("glm-4");
    await region.getByLabel("Reproducibility").selectOption("mutable_alias");
    await region.getByLabel("Operator credential reference").fill("VERDANT_MODEL_SECRET_FIXTURE");
    const glm = await response(page, "/model-profiles", () => region.getByRole("button", { name: "Save profile version" }).click());
    expect(glm.revision).toBe(2); expect(glm.identity_kind).toBe("mutable_alias");
    await region.getByRole("button", { name: "Load selected flow for versioning" }).click();
    await region.getByLabel("Step type").nth(1).selectOption("model");
    await region.getByLabel("Step model profile").selectOption(gemini.id);
    const flow2 = await response(page, "/process-flows", () => region.getByRole("button", { name: "Save flow version", exact: true }).click());
    await region.getByLabel("Step model profile").selectOption(glm.id);
    const flow3 = await response(page, "/process-flows", () => region.getByRole("button", { name: "Save flow version", exact: true }).click());
    expect(flow2.definition.steps[1].profile_digest).toBe(gemini.config_digest);
    expect(flow3.definition.steps[1].profile_digest).toBe(glm.config_digest);
    expect(flow2.config_digest).not.toBe(flow3.config_digest);
    expect(detail.flow.id).toBe(flow1.id); // Original run retains original flow after switching.
    const disabled = await response(page, "/process-runs", () => region.getByRole("button", { name: "Start pinned run", exact: true }).click());
    python("scripts/process_native_fixture.py", "dispatch", fixturePath);
    await expect(selected).toContainText("Status: failed", { timeout: 120_000 });
    await expect(selected).toContainText("provider_calls_disabled");
    await response(page, "/actions", () => selected.getByRole("button", { name: "Recover same trial" }).click());
    await expect(selected).toContainText("attempt 2");
    await response(page, "/actions", () => selected.getByRole("button", { name: "Cancel run", exact: true }).click());
    await expect(selected).toContainText("Status: cancelled");
    await page.screenshot({ path: info.outputPath("process-browser.png"), fullPage: true });
    writeFileSync(info.outputPath("process-receipt.json"), JSON.stringify({ scenario: "real SSO + native Prefect process + actual DataChain", run, detail, native_run: nativeRun, profile_versions: [gemini, glm], flow_versions: [flow1, flow2, flow3], disabled_provider_run: disabled, paid_calls: 0 }, null, 2));
    await context.close();
  } finally {
    for (const child of [...children].reverse()) await stop(child);
    process.removeListener("exit", cleanup); cleanup();
  }
});

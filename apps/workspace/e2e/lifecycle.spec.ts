import { test, expect, type Page } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:net";
import { createHash } from "node:crypto";

const root = resolve("../..");
const baseURL = "http://127.0.0.1:19085";
const env = { ...process.env,
  VERDANT_DATABASE_URL: "postgresql+psycopg://postgres:browser-e2e-database@127.0.0.1:19032/verdant_browser_e2e_test",
  VERDANT_OIDC_ISSUER: "http://127.0.0.1:19081/realms/verdant-browser-e2e",
  VERDANT_OIDC_CLIENT_ID: "verdant-browser", VERDANT_OIDC_CLIENT_SECRET: "browser-e2e-client",
  VERDANT_APP_URL: baseURL, VERDANT_OIDC_GROUP: "/workspace", VERDANT_API_AUDIENCE: "verdant-api",
  VERDANT_SERVICE_CLIENTS: "verdant-worker", VERDANT_INSECURE_LOCAL_HTTP: "1",
  VERDANT_S3_ENDPOINT: "http://127.0.0.1:19033", VERDANT_S3_BROWSER_ENDPOINT: "http://127.0.0.1:19033",
  VERDANT_S3_BUCKET: "verdant-artifacts", VERDANT_S3_ACCESS_KEY: "browser-e2e", VERDANT_S3_SECRET_KEY: "browser-e2e-storage",
};
const project = `verdant-browser-${process.pid}`;
const compose = ["compose", "-p", project, "-f", resolve("e2e/compose.yaml")];
const python = (...args: string[]) => execFileSync("uv", ["run", "--locked", "--group", "workflows", "python", ...args], { cwd: root, env, stdio: "pipe", timeout: 180_000 });
function startApi() {
  return spawn("uv", ["run", "--locked", "--group", "workflows", "uvicorn", "verdant.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "19087", "--no-access-log"], { cwd: root, env, detached: true, stdio: "ignore" });
}
async function stop(child?: ChildProcess) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit");
  process.kill(-child.pid, "SIGTERM");
  await exited;
}
async function ready(url: string) {
  await expect.poll(async () => { try { return (await fetch(url)).status; } catch { return 0; } }, { timeout: 30_000 }).toBe(200);
}
async function login(page: Page, username: string, url: string) {
  await page.goto(baseURL);
  await page.getByRole("link", { name: "Sign in with your organization" }).click();
  await page.getByRole("textbox", { name: "Username or email" }).fill(username);
  await page.getByRole("textbox", { name: "Password", exact: true }).fill("browser-e2e-password");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page.getByText(`Signed in as Browser ${username === "owner" ? "Owner" : "Editor"}`)).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Automated lifecycle", exact: true })).toBeVisible();
}
async function response(page: Page, suffix: string, click: () => Promise<unknown>, method = "POST") {
  const pending = page.waitForResponse(r => r.url().endsWith(suffix) && r.request().method() === method);
  await click(); const r = await pending; expect(r.ok()).toBeTruthy(); return r.json();
}

test("two independent SSO sessions retain candidate pins, recovery, validation and published bytes", async ({ browser }, info) => {
  let api: ChildProcess | undefined, web: ChildProcess | undefined;
  const fixturePath = info.outputPath("fixture.json");
  const cleanup = () => {
    for (const child of [web, api]) if (child?.pid) { try { process.kill(-child.pid, "SIGTERM"); } catch { /* Already stopped. */ } }
    execFileSync("docker", [...compose, "down", "--timeout", "1"], { stdio: "pipe", timeout: 20_000 });
  };
  process.once("exit", cleanup);
  try {
    for (const port of [19032, 19033, 19081, 19085, 19087]) {
      await new Promise<void>((resolve, reject) => { const server = createServer(); server.once("error", reject); server.listen(port, "127.0.0.1", () => server.close(() => resolve())); });
    }
    execFileSync("docker", [...compose, "up", "-d", "--wait", "--wait-timeout", "150"], { stdio: "pipe", timeout: 180_000 });
    python("scripts/seed_lifecycle_browser.py", fixturePath);
    const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
    const route = `/?namespace=${fixture.namespace_id}&dataset=${fixture.dataset_id}`;
    const apiBase = `/api/v1/namespaces/${fixture.namespace_id}/datasets/${fixture.dataset_id}`;
    api = startApi(); await ready("http://127.0.0.1:19087/health/ready");
    web = spawn("pnpm", ["exec", "vite", "--host", "127.0.0.1", "--port", "19085", "--strictPort", "--config", "e2e/vite.config.ts"], { env, detached: true, stdio: "ignore" });
    await ready(baseURL);
    const first = await browser.newContext(), second = await browser.newContext();
    const owner = await first.newPage(), editor = await second.newPage();
    await login(owner, "owner", baseURL + route);
    await login(editor, "editor", baseURL + route);
    expect(await first.cookies()).not.toEqual(await second.cookies());
    const candidates = owner.getByRole("region", { name: "Frozen candidates", exact: true });
    await expect(candidates.getByText("Latest draft revision: 101", { exact: true })).toBeVisible();
    const captured = await response(owner, `${apiBase}/candidates`, () => candidates.getByRole("button", { name: "Capture latest draft", exact: true }).click());
    const selected = candidates.getByRole("article", { name: "Selected candidate", exact: true });
    await response(owner, "/prepare", () => selected.getByRole("button", { name: "Resume preparation" }).click());
    await expect(selected).toContainText("Preparation: preparing · 100 members");
    const capturedURL = owner.url();
    await stop(api); api = startApi(); await ready("http://127.0.0.1:19087/health/ready");
    await owner.reload();
    await expect(selected).toContainText(captured.generation_id);
    await expect(selected).toContainText("Preparation: preparing · 100 members");
    expect(owner.url()).toBe(capturedURL);

    // Edit the last UUID: it has not been materialized in the first 100-member checkpoint.
    const last = fixture.members.at(-1);
    const draft = editor.getByRole("region", { name: "Draft membership", exact: true });
    for (let page = 0; page < 4; page++) {
      await draft.getByRole("button", { name: "Next samples", exact: true }).click();
      await expect(draft.locator(`option[value="${fixture.members[(page + 1) * 25].sample_id}"]`)).toHaveCount(1);
    }
    await draft.getByRole("combobox", { name: "Available sample", exact: true }).selectOption(last.sample_id);
    await draft.getByLabel("Split", { exact: true }).fill("test");
    await draft.getByLabel("Interpretation evidence (JSON, optional)").fill(JSON.stringify(fixture.changed_pins));
    await response(editor, `/draft/members/${last.sample_id}`, () => draft.getByRole("button", { name: "Save membership", exact: true }).click(), "PUT");
    await expect(selected).toContainText("latest draft revision: 102");
    await response(owner, "/prepare", () => selected.getByRole("button", { name: "Resume preparation" }).click());
    await expect(selected).toContainText("Preparation: sealed · 101 members");
    const frozen: any[] = [];
    let after = "";
    do {
      const r = await owner.request.get(`${apiBase}/candidates/${captured.id}/members?limit=25${after ? `&after=${after}` : ""}`);
      expect(r.ok()).toBeTruthy(); const page = await r.json();
      expect(page.candidate.id).toBe(captured.id); expect(page.candidate.generation_id).toBe(captured.generation_id);
      frozen.push(...page.items); after = page.next_after ?? "";
    } while (after);
    expect(frozen).toHaveLength(101);
    for (const [index, member] of frozen.entries()) {
      expect(member.sample_id).toBe(fixture.members[index].sample_id);
      expect(member.sample_revision_id).toBe(fixture.members[index].sample_revision_id);
      expect(member.split).toBe("train"); expect(member.interpretation).toEqual(fixture.original_pins);
    }
    for (let page = 0; page < 4; page++) {
      await selected.getByRole("button", { name: "Next frozen members", exact: true }).click();
      await expect(selected.getByRole("cell", { name: fixture.members[(page + 1) * 25].sample_id, exact: true })).toBeVisible();
    }
    await expect(selected.getByRole("cell", { name: last.sample_id, exact: true })).toBeVisible();

    const quality = owner.getByRole("region", { name: "Quality policies and validation", exact: true });
    await quality.getByLabel("Policy name", { exact: true }).fill("browser_ready");
    await quality.getByLabel("Rule 1 minimum samples", { exact: true }).fill("101");
    await response(owner, "/quality/policies", () => quality.getByRole("button", { name: "Save policy revision", exact: true }).click());
    const report = await response(owner, "/quality/reports", () => quality.getByRole("button", { name: "Start new validation", exact: true }).click());
    for (let batch = 0; batch < 2; batch++) await response(owner, `/quality/reports/${report.id}/evaluate`, () => quality.getByRole("button", { name: "Resume validation", exact: true }).click());
    const validation = quality.getByRole("article", { name: "Selected validation report", exact: true });
    await expect(validation).toContainText("complete");
    await draft.getByLabel("Split", { exact: true }).fill("validation");
    // Reload edit revision after another write using the current exact sample selection.
    await draft.getByRole("combobox", { name: "Available sample", exact: true }).selectOption(last.sample_id);
    await response(editor, `/draft/members/${last.sample_id}`, () => draft.getByRole("button", { name: "Save membership", exact: true }).click(), "PUT");
    await expect(selected).toContainText("latest draft revision: 103");
    await expect(validation).toContainText(report.id);
    const pinnedReport = await owner.request.get(`${apiBase}/quality/reports/${report.id}`);
    expect((await pinnedReport.json()).source_revision).toBe(101);
    await selected.getByRole("combobox", { name: "Selection mode", exact: true }).selectOption("current_draft");
    const rejected = owner.waitForResponse(r => r.url().endsWith("/check-selection") && r.request().method() === "POST");
    await selected.getByRole("button", { name: "Check selection", exact: true }).click(); expect((await rejected).status()).toBe(409);
    await expect(selected).toContainText("Preparation: sealed · 101 members");
    await selected.getByRole("combobox", { name: "Selection mode", exact: true }).selectOption("selected_candidate");
    await response(owner, "/check-selection", () => selected.getByRole("button", { name: "Check selection", exact: true }).click());

    const publication = owner.getByRole("region", { name: "Dataset publication", exact: true });
    await publication.getByRole("combobox", { name: "Passing quality report", exact: true }).selectOption(report.id);
    await publication.getByLabel("Version label", { exact: true }).fill("browser-release");
    const started = await response(owner, "/publications", () => publication.getByRole("button", { name: "Start publication preparation", exact: true }).click());
    for (let batch = 0; batch < 5; batch++) await response(owner, "/prepare", () => publication.getByRole("button", { name: "Resume source checks", exact: true }).click());
    const published = await response(owner, "/finalize", async () => { await publication.getByRole("button", { name: "Publish immutable version", exact: true }).focus(); await owner.keyboard.press("Enter"); });
    await owner.getByRole("button", { name: "Refresh version list", exact: true }).click();
    await owner.getByRole("link", { name: "browser-release", exact: true }).click();
    const version = owner.getByRole("article", { name: "Exact published version", exact: true });
    const downloadEvent = owner.waitForEvent("download");
    await version.getByRole("link", { name: "Download exact manifest (verifies bytes)", exact: true }).click();
    const file = await (await downloadEvent).path(); expect(file).not.toBeNull();
    const manifest = readFileSync(file!);
    const manifestDigest = createHash("sha256").update(manifest).digest("hex");
    expect(manifestDigest).toBe(published.manifest_digest);
    const parsed = JSON.parse(manifest.toString());
    expect(parsed.source_revision).toBe(101); expect(parsed.members).toHaveLength(101);
    for (const member of parsed.members) { expect(member.interpretation).toEqual(fixture.original_pins); expect(member.split).toBe("train"); }
    const previewed = owner.waitForResponse(r => r.url().endsWith(`/samples/${fixture.members[0].sample_id}/preview`));
    await version.getByRole("button", { name: `Preview exact sample ${fixture.members[0].sample_id}`, exact: true }).click();
    const preview = await (await previewed).json();
    expect(preview.sha256).toBe(fixture.members[0].sha256);
    expect(createHash("sha256").update(preview.text).digest("hex")).toBe(preview.sha256);
    await expect(owner.getByRole("table", { name: `Accessible lineage evidence for sample ${fixture.members[0].sample_id}`, exact: true })).toBeVisible();
    await owner.getByRole("button", { name: "Archive dataset", exact: true }).click();
    await expect(owner.getByRole("button", { name: "Capture latest draft", exact: true })).toBeDisabled();
    const exactRead = await owner.request.get(`${apiBase}/versions/${published.version_id}/manifest`);
    expect(exactRead.ok()).toBeTruthy(); expect((await exactRead.json()).sha256).toBe(manifestDigest);
    await version.getByRole("button", { name: "Archive version", exact: true }).click();
    const retainedDownload = owner.waitForEvent("download");
    await version.getByRole("link", { name: "Download exact manifest (verifies bytes)", exact: true }).click();
    expect(readFileSync((await (await retainedDownload).path())!)).toEqual(manifest);
    await version.getByRole("button", { name: "Unarchive version", exact: true }).click();
    await owner.getByRole("button", { name: "Unarchive dataset", exact: true }).click();
    await expect(owner.getByRole("button", { name: "Capture latest draft", exact: true })).toBeEnabled();
    python("scripts/seed_lifecycle_browser.py", "--revoke-editor", fixturePath);
    await expect(editor.getByText("You do not have permission for this workspace.", { exact: false })).toBeVisible();
    await expect(editor.getByRole("article", { name: "Selected candidate", exact: true })).toHaveCount(0);
    await owner.screenshot({ path: info.outputPath("retained-version.png") });
    writeFileSync(info.outputPath("receipt.json"), JSON.stringify({ candidate_id: captured.id, generation_id: captured.generation_id,
      report_id: report.id, source_revision: 101, final_draft_revision: 103, frozen_members: frozen.length,
      restart_checkpoint: 100, pins_retained: true, independent_sessions: true, current_draft_conflict: 409,
      publication_id: started.id, version_id: published.version_id, manifest_sha256: manifestDigest,
      manifest_bytes: manifest.length, archived_exact_read: true, revoked_editor_hidden: true }, null, 2));
    await first.close(); await second.close();
  } finally {
    await stop(web); await stop(api);
    // This unique project has no named volumes and never reuses another project's data.
    cleanup(); process.off("exit", cleanup);
  }
});

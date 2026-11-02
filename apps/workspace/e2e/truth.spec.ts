import { test, expect, type Page, type BrowserContext, type Locator } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve("../..");
const local = resolve("e2e/truth/.local");
const app = "https://workspace.localhost:19243";
const labels = "https://labels.localhost:19243";
let phase = "prepare";
let nativeFailure: Record<string, unknown> | undefined;
let api: ChildProcess | undefined, web: ChildProcess | undefined;
function environment() { return { ...process.env, ...JSON.parse(readFileSync(resolve(local, "environment.json"), "utf8")) } as NodeJS.ProcessEnv; }
function python(script: string, ...args: string[]) { return execFileSync("uv", ["run", "--locked", "python", script, ...args], { cwd: root, env: existsSync(resolve(local, "environment.json")) ? environment() : process.env, stdio: "pipe", timeout: 300_000 }); }
async function stop(child?: ChildProcess) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit"); process.kill(-child.pid, "SIGTERM");
  await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 10_000))]);
  if (child.exitCode === null && child.signalCode === null) process.kill(-child.pid, "SIGKILL");
}
async function ready(url: string) { await expect.poll(async () => { try { return (await fetch(url)).status; } catch { return 0; } }, { timeout: 45_000 }).toBe(200); }
async function response(page: Page, suffix: string, click: () => Promise<unknown>, status = 200) {
  const pending = page.waitForResponse(r => r.url().endsWith(suffix) && r.request().method() === "POST", { timeout: 60_000 });
  await click(); const result = await pending; expect(result.status(), `phase ${phase}, ${suffix}`).toBe(status); return result.json();
}
async function login(page: Page, name: string, route: string) {
  const credentials = JSON.parse(readFileSync(resolve(local, "credentials.json"), "utf8"));
  await page.goto(app);
  await page.getByRole("link", { name: "Sign in with your organization" }).click();
  await page.getByRole("textbox", { name: "Username or email" }).fill(name);
  await page.getByRole("textbox", { name: "Password", exact: true }).fill(credentials[name]);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page.getByText(`Signed in as Browser ${name === "owner" ? "Owner" : "Editor"}`)).toBeVisible();
  await page.goto(app + route);
  await expect(page.getByRole("heading", { name: "Automated truth qualification", exact: true })).toBeVisible();
}
async function nativeApi(page: Page, path: string) {
  return page.evaluate(async path => { const result = await fetch(path); return { status: result.status, body: await result.json() }; }, path);
}
async function openNative(page: Locator, context: BrowserContext) {
  const link = page.getByRole("link", { name: "Open stock labeling editor" });
  await expect(link).toHaveAttribute("target", "_blank");
  const href = await link.getAttribute("href"); expect(new URL(href!).origin).toBe(labels);
  const native = await context.newPage(); await native.goto(href!);
  await native.waitForURL(`${labels}/**`, { timeout: 60_000 });
  await expect.poll(async () => { try { return (await nativeApi(native, "/api/current-user/whoami")).status; } catch { return 0; } }, { timeout: 60_000 }).toBe(200);
  return native;
}
async function nativeSubmit(page: Page, category: string, task: number, person: number) {
  await page.waitForFunction(() => !!(window as any).Htx && !(window as any).Htx.isLoading, null, { timeout: 45_000 });
  const listed = await nativeApi(page, `/api/tasks/${task}/annotations/`);
  expect(listed.status).toBe(200);
  const existing = listed.body.find((row: any) => (typeof row.completed_by === "object" ? row.completed_by.id : row.completed_by) === person && !row.was_cancelled);
  if (existing) {
    await page.locator(`[data-annotation-id="${existing.id}"]`).first().click();
    await page.waitForFunction(id => String((window as any).Htx.annotationStore.selected.pk) === String(id), existing.id);
  } else {
    const selected = await page.evaluate(() => {
      const current = (window as any).Htx.annotationStore.selected;
      return { pk: current.pk, completedBy: current.completed_by, userId: current.user?.id };
    });
    if (selected.pk || (selected.completedBy !== person && selected.userId !== person)) {
      await page.getByLabel("Create an annotation", { exact: true }).click();
      await page.waitForFunction(() => !(window as any).Htx.annotationStore.selected.pk);
    }
  }
  async function commit() {
    const submit = page.getByText(/^(Submit|Update)$/, { exact: true }).first();
    await expect(submit).toBeEnabled();
    const pending = page.waitForResponse(r => /\/api\/(?:tasks\/\d+\/annotations\/?|annotations\/\d+\/?)$/.test(new URL(r.url()).pathname) && ["POST", "PATCH"].includes(r.request().method()), { timeout: 45_000 });
    await submit.click(); const result = await pending;
    if (!result.ok()) {
      nativeFailure = { status: result.status(), path: new URL(result.url()).pathname,
                        response: await result.json().catch(() => null) };
      throw new Error(`Native annotation failed with HTTP ${result.status()} at ${new URL(result.url()).pathname}`);
    }
    const saved = await result.json(); expect(saved.completed_by).toBe(person); return saved;
  }
  if (existing) {
    const value = (name: string) => existing.result.find((entry: any) => entry.from_name === name)?.value.choices[0];
    const modifier = typeof existing.updated_by === "object" ? existing.updated_by?.id : existing.updated_by;
    if (value("label") === category && value("assistance") === "independent" && modifier === person) return existing;
    const previous = value("assistance");
    await page.getByRole("checkbox", { name: new RegExp(`^${previous === "assisted" ? "unknown" : "assisted"}`) }).check();
    await commit();
  }
  await page.getByRole("checkbox", { name: new RegExp(`^${category}`) }).check();
  await page.getByRole("checkbox", { name: /^independent/ }).check();
  return commit();
}

test("real independent HTTPS SSO, stock annotation, immutable agreement/review and truth audits", async ({ browser }, info) => {
  test.setTimeout(900_000);
  const receipt: Record<string, unknown> = { environment: "fresh-HTTPS-issuer-two-real-apps", assertions: [] };
  const assertions = receipt.assertions as string[];
  const fixturePath = resolve(local, "runs", `${process.pid}-${Date.now()}`, "fixture.json");
  let first: BrowserContext | undefined, second: BrowserContext | undefined;
  try {
    if (!existsSync(resolve(local, "environment.json"))) python("scripts/prepare_truth_fixture.py");
    // Start the retained deployment every time. Seed upgrades to the current
    // Alembic heads and creates new run-owned data in its existing cohort namespace.
    const project = environment().COMPOSE_PROJECT_NAME!;
    if (!/^verdant-truth-[a-z0-9]+$/.test(project)) throw new Error("Unowned truth Compose project");
    execFileSync("docker", ["compose", "--env-file", resolve(local, "compose.env"), "-p", project, "-f", resolve("e2e/truth/compose.yaml"), "up", "-d", "--wait", "--wait-timeout", "240"], { stdio: "pipe", timeout: 270_000 });
    python("scripts/seed_truth_browser.py", fixturePath);
    const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
    receipt.run_id = fixture.run_id; receipt.dataset_id = fixture.dataset_id; receipt.namespace_id = fixture.namespace_id; receipt.compose_project = fixture.compose_project;
    receipt.image = fixture.image; receipt.source = fixture.source_revision;
    const env = environment();
    api = spawn("uv", ["run", "--locked", "uvicorn", "verdant.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "19287", "--no-access-log"], { cwd: root, env, detached: true, stdio: "ignore" });
    await ready("http://127.0.0.1:19287/health/ready");
    web = spawn("pnpm", ["exec", "vite", "--host", "0.0.0.0", "--port", "19285", "--strictPort", "--config", "e2e/truth/vite.config.ts"], { env, detached: true, stdio: "ignore" });
    await ready("http://127.0.0.1:19285");
    const route = `/?namespace=${fixture.namespace_id}&dataset=${fixture.dataset_id}`;
    const base = `/api/v1/namespaces/${fixture.namespace_id}/datasets/${fixture.dataset_id}`;
    first = await browser.newContext({ ignoreHTTPSErrors: true, baseURL: app }); second = await browser.newContext({ ignoreHTTPSErrors: true, baseURL: app });
    const owner = await first.newPage(), editor = await second.newPage();
    phase = "independent organization logins";
    await login(owner, "owner", route); await login(editor, "editor", route);
    assertions.push("Independent authorization-code SSO in Verdant, no test auth bypass");
    const annotations = owner.getByRole("region", { name: "Annotations and agreement", exact: true });
    await expect(annotations).toContainText("Projects do not isolate users");
    await annotations.getByLabel("Trust cohort", { exact: true }).selectOption(fixture.cohort_id);
    for (const checkbox of await annotations.getByRole("group", { name: "Independent contributors", exact: true }).getByRole("checkbox").all()) await checkbox.check();
    await annotations.getByLabel("Native project ID", { exact: true }).fill(String(fixture.native_project_id));
    const assignments: any[] = [];
    const retained = await (await owner.request.get(`${base}/annotations?limit=100`)).json();
    for (const member of fixture.members) {
      phase = `assignment ${member.index}`;
      await annotations.getByLabel("Annotation sample", { exact: true }).selectOption(member.sample_id);
      await annotations.getByLabel("Native task ID", { exact: true }).fill(String(member.native_task_id));
      const assignment = retained.items.find((item: any) => item.native_task_id === member.native_task_id) ?? await response(owner, `${base}/annotations`, () => annotations.getByRole("button", { name: "Create assignment", exact: true }).click());
      assignments.push(assignment);
    }
    for (const checkbox of await annotations.getByRole("group", { name: "Overlap assignments for report", exact: true }).getByRole("checkbox").all()) await checkbox.check();
    receipt.assignments = assignments.map(item => item.id);
    assertions.push("UI assignments verify exact native schema/instructions/content/source revision and admitted contributor pins");
    const nativeIdentities: number[] = [];
    for (const [index, assignment] of assignments.entries()) {
      for (const [person, context, username] of [[owner, first, "owner"], [editor, second, "editor"]] as const) {
        phase = `stock editor ${username} sample ${index}`;
        const section = person.getByRole("region", { name: "Annotations and agreement", exact: true });
        if (person === editor && index === 0) await person.reload();
        await section.getByRole("button", { name: `Inspect assignment ${assignment.native_task_id}`, exact: true }).click();
        const native = await openNative(section, context);
        const identity = (await nativeApi(native, "/api/current-user/whoami")).body.id;
        const expected = fixture.native_people.find((item: any) => item.name === username).native_user_id;
        expect(identity).toBe(expected); if (index === 0) nativeIdentities.push(identity);
        const submitted = await nativeSubmit(native, index === 1 && username === "editor" ? "negative" : "positive", assignment.native_task_id, identity);
        expect(submitted.completed_by).toBe(identity);
        if (index === 0) {
          const proof = resolve(root, "docs/proofs/epic-5"); mkdirSync(proof, { recursive: true });
          await native.screenshot({ path: resolve(proof, `native-${username}.png`) });
        }
        await native.close();
      }
      phase = `authoritative import sample ${index}`;
      await response(owner, `/annotations/${assignment.id}/import`, () => annotations.getByRole("button", { name: "Return and import native annotations", exact: true }).click());
      await expect(annotations.getByRole("article", { name: "Selected annotation assignment" })).toContainText("Original revisions: 2");
    }
    const exported = await owner.request.get(`${labels}/api/projects/${fixture.native_project_id}/export?exportType=JSON&download_all_tasks=true`);
    expect(exported.status()).toBe(200);
    const nativeExport = await exported.json(); expect(nativeExport).toHaveLength(5);
    for (const task of nativeExport) expect(new Set(task.annotations.map((annotation: any) => typeof annotation.completed_by === "object" ? annotation.completed_by.id : annotation.completed_by))).toEqual(new Set(nativeIdentities));
    receipt.native_export = { tasks: nativeExport.length, annotation_count: nativeExport.reduce((count: number, task: any) => count + task.annotations.length, 0) };
    expect(new Set(nativeIdentities).size).toBe(2);
    receipt.native_user_ids = nativeIdentities;
    assertions.push("Both humans enter stock editor through shared HTTPS issuer SSO, click categories and independently submit native annotations");
    phase = "report and adjudication";
    const report = await response(owner, "/agreement", () => annotations.getByRole("button", { name: "Create agreement report", exact: true }).click());
    receipt.report = { id: report.id, digest: report.digest };
    const pair = report.payload.pairs[0]; expect(pair.overlap).toBe(5); expect(pair.agreed).toBe(4);
    await expect(annotations.getByRole("article", { name: "Agreement report", exact: true })).toContainText("Observed agreement 80.0%");
    const reviews: any[] = [];
    for (const assignment of assignments) {
      await annotations.getByRole("button", { name: `Inspect assignment ${assignment.native_task_id}`, exact: true }).click();
      await expect(annotations.getByRole("article", { name: "Selected annotation assignment" })).toContainText(assignment.id);
      await annotations.getByLabel("Canonical category", { exact: true }).selectOption("positive");
      await annotations.getByLabel("Adjudication rationale", { exact: true }).fill("Reviewed exact source and independent originals; selected canonical sentiment.");
      reviews.push(await response(owner, "/adjudications", () => annotations.getByRole("button", { name: "Save canonical review", exact: true }).click()));
    }
    receipt.canonical_ids = reviews.map(item => item.id);
    assertions.push("Persisted exact pre-adjudication report plus side-by-side disagreement queue and authorized rationale-bearing immutable canonical reviews");
    // Correction creates a new identity, leaving old report and original review unchanged.
    await annotations.getByLabel("Adjudication rationale", { exact: true }).fill("Version correction records a revised rationale without rewriting originals.");
    const corrected = await response(owner, "/adjudications", () => annotations.getByRole("button", { name: "Save canonical review", exact: true }).click());
    expect(corrected.id).not.toBe(reviews.at(-1).id);
    expect(corrected.payload.annotation.payload.prior_id).toBe(reviews.at(-1).id);
    expect(await (await owner.request.get(`${base}/agreement/${report.id}`)).json()).toEqual(report);
    expect(await (await owner.request.get(`${base}/adjudications/${reviews.at(-1).id}`)).json()).toEqual(reviews.at(-1));
    assertions.push("Browser version correction retains exact old report and canonical review identities");
    phase = "truth partitions";
    const truth = owner.getByRole("region", { name: "Truth partitions and production audits", exact: true });
    await truth.getByRole("button", { name: "Load current truth inputs", exact: true }).click();
    await truth.getByLabel(`Partition for ${fixture.members[3].sample_id}`, { exact: true }).selectOption("test");
    const firstTruth = await response(owner, "/truth", () => truth.getByRole("button", { name: "Publish truth version", exact: true }).click());
    expect(firstTruth.payload.validation_derived).toBe(true);
    expect(new Set(firstTruth.payload.members.map((member: any) => member.partition))).toEqual(new Set(["train", "validation", "test", "production"]));
    receipt.truth_before_audit = { id: firstTruth.id, digest: firstTruth.digest, pinned_revision: firstTruth.payload.pinned_revision };
    assertions.push("UI publishes typed train/validation/test/production with deterministic validation derivation and exact canonical/schema/review membership pins");
    phase = "reviewed production audit";
    await truth.getByRole("button", { name: "Load current truth inputs", exact: true }).click();
    const production = truth.getByRole("article", { name: `Truth member ${fixture.members[4].sample_id}`, exact: true });
    await production.getByRole("checkbox", { name: "Audit this production sample", exact: true }).check();
    await production.getByLabel("Sample stratum", { exact: true }).fill("daily-random");
    await production.getByLabel("Inclusion probability", { exact: true }).fill("0.25");
    await production.getByLabel("Selection method", { exact: true }).fill("Uniform random draw within daily-random stratum, seed recorded by operator.");

    const audited = await response(owner, "/truth", () => truth.getByRole("button", { name: "Publish truth version", exact: true }).click());
    const auditedMember = audited.payload.members.find((member: any) => member.sample_id === fixture.members[4].sample_id);
    expect(auditedMember.partition).toBe("production"); expect(auditedMember.audit.inclusion_probability).toBe(.25);
    expect(auditedMember.audit.source_truth_id).toBe(firstTruth.id); expect(auditedMember.canonical_id).toBe(corrected.id);
    expect(audited.payload.exploratory_test_reuse).toBe(false);
    expect(audited.payload.evaluation_use_declaration).toBe("not_declared");
    expect(audited.payload.members.filter((member: any) => member.partition === "test")).toEqual(firstTruth.payload.members.filter((member: any) => member.partition === "test"));
    expect(await (await owner.request.get(`${base}/truth/${firstTruth.id}`)).json()).toEqual(firstTruth);
    const pinned = await (await owner.request.get(`${base}/draft/members?revision=${firstTruth.payload.pinned_revision}`)).json();
    expect(pinned.members.find((member: any) => member.sample_id === fixture.members[4].sample_id).interpretation.annotation_revision).toBeNull();
    receipt.truth_after_audit = { id: audited.id, digest: audited.digest };
    assertions.push("Reviewed production audit records stratum/probability/source truth/review pins, remains production, preserves unchanged holdout without declaring evaluation reuse, preserves old immutable truth and registry revision");
    phase = "screenshots";
    const proof = resolve(root, "docs/proofs/epic-5"); mkdirSync(proof, { recursive: true });
    await truth.screenshot({ path: resolve(proof, "truth-browser.png") });
    await annotations.getByRole("article", { name: "Agreement report", exact: true }).screenshot({ path: resolve(proof, "agreement-browser.png") });
    receipt.status = "passed";
  } catch (error) {
    receipt.status = "failed"; receipt.failed_phase = phase; receipt.error_type = error instanceof Error ? error.name : "unknown";
    throw error;
  } finally {
    if (receipt.status === "failed") {
      const retained = resolve(local, "failures", String(Date.now())); mkdirSync(retained, { recursive: true });
      writeFileSync(resolve(retained, "receipt.json"), JSON.stringify({ ...receipt, nativeFailure }, null, 2)+"\n");
      for (const [index, context] of [first, second].entries()) {
        for (const [tab, page] of (context?.pages() ?? []).entries()) {
          await page.screenshot({ path: resolve(retained, `context-${index}-tab-${tab}.png`) }).catch(() => undefined);
        }
      }
    }
    await first?.close(); await second?.close(); await stop(web); await stop(api);
    writeFileSync(info.outputPath("truth-browser-receipt.json"), JSON.stringify(receipt, null, 2)+"\n");
    if (receipt.status === "passed") {
      writeFileSync(resolve(root, "docs/proofs/epic-5/browser-receipt.json"), JSON.stringify(receipt, null, 2)+"\n");
      writeFileSync(resolve(fixturePath, "..", "browser-receipt.json"), JSON.stringify(receipt, null, 2)+"\n");
    }
  }
});

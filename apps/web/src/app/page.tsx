"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Alert } from "../components/Alert";
import { DigestPipelineSteps } from "../components/DigestPipelineSteps";
import { JobsPanel } from "../components/JobsPanel";
import { MarkdownView } from "../components/MarkdownView";
import { OutputMeta } from "../components/OutputMeta";
import type {
  JobCounts,
  JobFilter,
  JobListItem,
  RunOutputsMeta,
} from "../lib/jobs";
import { explainJobError } from "../lib/jobs";
import { canReviewExtractedPages } from "../lib/pipelineSteps";

type JobEvent = {
  ts: string;
  message: string;
  phase?: string;
};

type PageWorkEntry = {
  pageNumber: number;
  status: "pending" | "running" | "done" | "skipped" | "failed" | "retrying";
  chars?: number;
  attempts?: number;
  error?: string;
};

type PageProgress = {
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  current?: number;
  phase?: string;
  pages: PageWorkEntry[];
};

type DigestPlan = {
  inputKind: "pdf" | "image";
  pageCount: number;
  digestPages: number;
  skippedBlank: number;
  concurrency: number;
  strategy: "single" | "multipage";
};

type DigestResponse = {
  runId?: string;
  status?: string;
  markdown?: string;
  html?: string;
  runDir?: string;
  error?: string;
  pageCount?: number;
  model?: string;
  events?: JobEvent[];
  plan?: DigestPlan;
  pageProgress?: PageProgress;
  progressPct?: number;
  inputPath?: string;
  inputName?: string;
  format?: string;
  outputs?: RunOutputsMeta | null;
  job?: JobListItem;
};

type ProviderId =
  | "gemini"
  | "openai"
  | "anthropic"
  | "openrouter"
  | "bedrock"
  | "custom";

type ProviderPublic = {
  id: ProviderId;
  label: string;
  auth?: "apiKey" | "aws";
  requiresBaseUrl: boolean;
  requiresRegion?: boolean;
  defaultBaseUrl: string;
  defaultRegion?: string;
  defaultModel: string;
  configured: boolean;
  apiKeyMasked: string;
  secretAccessKeyMasked?: string;
  sessionTokenConfigured?: boolean;
  model: string;
  baseUrl: string;
  region?: string;
  source: "ui" | "env" | "none";
};

type LlmPublic = {
  activeProvider: ProviderId;
  configured: boolean;
  apiKeyMasked: string;
  baseUrl: string;
  model: string;
  source: "ui" | "env" | "none";
  region?: string;
  providers: ProviderPublic[];
};

const BEDROCK_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-2",
  "eu-west-1",
  "eu-central-1",
  "ap-northeast-1",
  "ap-southeast-1",
  "ap-southeast-2",
];

type ModelOption = { id: string; name: string };

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function HomePage() {
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<"markdown" | "html" | "both">("markdown");
  const [stitchPreset, setStitchPreset] = useState<
    "default" | "too_short" | "too_much_garbage" | "bad_structure"
  >("default");
  const [stitchPrompt, setStitchPrompt] = useState("");
  const [reassembleBusy, setReassembleBusy] = useState(false);
  const [reassembleMsg, setReassembleMsg] = useState<string | null>(null);
  const openedRunRef = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DigestResponse | null>(null);
  const [previewMode, setPreviewMode] = useState<"markdown" | "html">("markdown");
  const [markdown, setMarkdown] = useState("");
  const [html, setHtml] = useState("");
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [plan, setPlan] = useState<DigestPlan | null>(null);
  const [pageProgress, setPageProgress] = useState<PageProgress | null>(null);
  const [outputs, setOutputs] = useState<RunOutputsMeta | null>(null);
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [jobCounts, setJobCounts] = useState<JobCounts>({
    all: 0,
    ongoing: 0,
    failed: 0,
    completed: 0,
  });
  const [jobFilter, setJobFilter] = useState<JobFilter>("all");
  /** Sidebar "New digest" vs inspecting a run in the main stage. */
  const [view, setView] = useState<"compose" | "run">("compose");
  const statusLogListRef = useRef<HTMLOListElement>(null);
  const pollGenRef = useRef(0);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [llm, setLlm] = useState<LlmPublic | null>(null);
  const [provider, setProvider] = useState<ProviderId>("gemini");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [secretAccessKeyInput, setSecretAccessKeyInput] = useState("");
  const [sessionTokenInput, setSessionTokenInput] = useState("");
  const [baseUrlInput, setBaseUrlInput] = useState("");
  const [regionInput, setRegionInput] = useState("us-east-1");
  const [modelInput, setModelInput] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState<string | null>(null);
  const [settingsOk, setSettingsOk] = useState(false);

  const [rerunModel, setRerunModel] = useState("");
  const [rerunFormat, setRerunFormat] = useState<"markdown" | "html" | "both">("markdown");
  const [rerunFresh, setRerunFresh] = useState(false);

  const activeProvider = useMemo(
    () => llm?.providers.find((p) => p.id === provider) ?? null,
    [llm, provider],
  );
  const isBedrock = provider === "bedrock";
  const keyModified = apiKeyInput.trim().length > 0;
  const secretModified = secretAccessKeyInput.trim().length > 0;
  const credsModified = isBedrock
    ? keyModified || secretModified || sessionTokenInput.trim().length > 0
    : keyModified;

  function applyProviderToForm(next: LlmPublic, id: ProviderId) {
    const p = next.providers.find((x) => x.id === id);
    if (!p) return;
    setProvider(id);
    setBaseUrlInput(p.baseUrl);
    setRegionInput(p.region || p.defaultRegion || "us-east-1");
    setModelInput(p.model);
    setApiKeyInput("");
    setSecretAccessKeyInput("");
    setSessionTokenInput("");
    setModels([]);
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/settings", { cache: "no-store" });
      const data = (await res.json()) as { llm?: LlmPublic; error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to load settings");
      if (data.llm) {
        setLlm(data.llm);
        applyProviderToForm(data.llm, data.llm.activeProvider);
        if (!data.llm.configured) setSettingsOpen(true);
        if (data.llm.configured) {
          void loadModels(data.llm.activeProvider);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function loadModels(
    forProvider: ProviderId,
    opts?: {
      apiKey?: string;
      baseUrl?: string;
      region?: string;
      secretAccessKey?: string;
      sessionToken?: string;
    },
  ) {
    try {
      const qs = new URLSearchParams({ provider: forProvider });
      if (opts?.apiKey) qs.set("apiKey", opts.apiKey);
      if (opts?.baseUrl) qs.set("baseUrl", opts.baseUrl);
      if (opts?.region) qs.set("region", opts.region);
      if (opts?.secretAccessKey) qs.set("secretAccessKey", opts.secretAccessKey);
      if (opts?.sessionToken) qs.set("sessionToken", opts.sessionToken);
      const res = await fetch(`/api/settings/models?${qs}`, { cache: "no-store" });
      const data = (await res.json()) as { models?: ModelOption[]; error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to list models");
      setModels(data.models ?? []);
    } catch (err) {
      setModels([]);
      if (opts?.apiKey || opts?.secretAccessKey || forProvider === "bedrock") {
        setSettingsMsg(err instanceof Error ? err.message : String(err));
        setSettingsOk(false);
      }
    }
  }

  async function loadJobs() {
    try {
      const res = await fetch("/api/jobs?limit=80", { cache: "no-store" });
      const data = (await res.json()) as {
        jobs?: JobListItem[];
        counts?: JobCounts;
        error?: string;
      };
      if (!res.ok) throw new Error(data.error ?? "Failed to load jobs");
      setJobs(data.jobs ?? []);
      if (data.counts) setJobCounts(data.counts);
    } catch {
      // non-fatal
    }
  }

  useEffect(() => {
    void loadSettings();
    void loadJobs();
  }, []);

  // Deep-link from Review pages: /?run=<runId>
  useEffect(() => {
    const run = new URLSearchParams(window.location.search).get("run");
    if (!run || openedRunRef.current === run) return;
    const job = jobs.find((j) => j.runId === run);
    if (!job) return;
    openedRunRef.current = run;
    void selectJob(job);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    window.history.replaceState(null, "", url.pathname + url.search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs]);

  // Keep pipeline scrolled to the latest entry while a digest is active.
  useEffect(() => {
    const active = busy || result?.status === "queued" || result?.status === "running";
    if (!active) return;
    const el = statusLogListRef.current;
    if (!el) return;
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [events, busy, result?.status]);

  // Keep job list fresh while anything is in flight
  useEffect(() => {
    const hasOngoing = jobs.some((j) => j.status === "queued" || j.status === "running");
    if (!hasOngoing && !busy) return;
    const id = setInterval(() => {
      void loadJobs();
    }, 2000);
    return () => clearInterval(id);
  }, [jobs, busy]);

  // Auto-clear successful settings feedback
  useEffect(() => {
    if (!settingsMsg || !settingsOk) return;
    const id = setTimeout(() => {
      setSettingsMsg(null);
      setSettingsOk(false);
    }, 4000);
    return () => clearTimeout(id);
  }, [settingsMsg, settingsOk]);

  function closeSettings() {
    setSettingsOpen(false);
    setSettingsMsg(null);
    setSettingsOk(false);
  }

  useEffect(() => {
    if (!settingsOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setSettingsOpen(false);
        setSettingsMsg(null);
        setSettingsOk(false);
      }
    }
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [settingsOpen]);

  async function selectProvider(id: ProviderId) {
    if (!llm) return;
    setSettingsMsg(null);
    applyProviderToForm(llm, id);
    // Persist active provider immediately
    setSettingsBusy(true);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ llm: { provider: id, makeActive: true } }),
      });
      const data = (await res.json()) as { llm?: LlmPublic; error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to switch provider");
      if (data.llm) {
        setLlm(data.llm);
        applyProviderToForm(data.llm, id);
        const p = data.llm.providers.find((x) => x.id === id);
        if (p?.configured) void loadModels(id);
      }
    } catch (err) {
      setSettingsMsg(err instanceof Error ? err.message : String(err));
      setSettingsOk(false);
    } finally {
      setSettingsBusy(false);
    }
  }

  async function saveModel(model: string) {
    setModelInput(model);
    setSettingsBusy(true);
    setSettingsMsg(null);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm: {
            provider,
            model,
            baseUrl: provider === "custom" ? baseUrlInput : undefined,
            region: isBedrock ? regionInput : undefined,
            makeActive: true,
          },
        }),
      });
      const data = (await res.json()) as { llm?: LlmPublic; error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to save model");
      if (data.llm) {
        setLlm(data.llm);
        setSettingsOk(true);
        setSettingsMsg(`Saved ${data.llm.providers.find((p) => p.id === provider)?.label} model.`);
      }
    } catch (err) {
      setSettingsOk(false);
      setSettingsMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  async function clearProviderKey() {
    setSettingsBusy(true);
    setSettingsMsg(null);
    setSettingsOk(false);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm: {
            provider,
            clearApiKey: true,
            clearSecretAccessKey: isBedrock,
            clearSessionToken: isBedrock,
            makeActive: true,
          },
        }),
      });
      const data = (await res.json()) as { llm?: LlmPublic; error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to clear key");
      if (data.llm) {
        setLlm(data.llm);
        applyProviderToForm(data.llm, provider);
        setModels([]);
        setSettingsOk(true);
        setSettingsMsg(
          isBedrock
            ? "AWS credentials cleared for Bedrock."
            : "API key cleared for this provider.",
        );
      }
    } catch (err) {
      setSettingsMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  async function testAndSaveApiKey() {
    if (isBedrock) {
      if (!regionInput.trim()) {
        setSettingsMsg("Bedrock requires an AWS region.");
        setSettingsOk(false);
        return;
      }
      if (!activeProvider?.configured && !(apiKeyInput.trim() && secretAccessKeyInput.trim())) {
        setSettingsMsg("Paste Access Key ID and Secret Access Key (or configure AWS env creds).");
        setSettingsOk(false);
        return;
      }
    } else if (!apiKeyInput.trim() && !activeProvider?.configured) {
      setSettingsMsg("Paste an API key first.");
      setSettingsOk(false);
      return;
    }
    if (provider === "custom" && !baseUrlInput.trim()) {
      setSettingsMsg("Custom provider requires a base URL.");
      setSettingsOk(false);
      return;
    }
    setSettingsBusy(true);
    setSettingsMsg(null);
    setSettingsOk(false);
    try {
      const res = await fetch("/api/settings/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm: {
            provider,
            apiKey: apiKeyInput.trim() || undefined,
            baseUrl: provider === "custom" ? baseUrlInput.trim() : undefined,
            region: isBedrock ? regionInput.trim() : undefined,
            secretAccessKey: isBedrock ? secretAccessKeyInput.trim() || undefined : undefined,
            sessionToken: isBedrock ? sessionTokenInput.trim() || undefined : undefined,
            model: modelInput || undefined,
            save: true,
          },
        }),
      });
      const data = (await res.json()) as {
        ok?: boolean;
        error?: string;
        llm?: LlmPublic;
        tested?: { model: string };
        models?: ModelOption[];
      };
      if (!res.ok || !data.ok) throw new Error(data.error ?? "Credential test failed");
      if (data.llm) {
        setLlm(data.llm);
        applyProviderToForm(data.llm, provider);
      }
      if (data.models?.length) {
        setModels(data.models);
        const current = modelInput || data.tested?.model;
        if (current && !data.models.some((m) => m.id === current)) {
          // keep typed model even if not in list
        } else if (!current && data.models[0]) {
          await saveModel(data.models[0].id);
        }
      } else {
        void loadModels(provider, {
          region: isBedrock ? regionInput : undefined,
        });
      }
      setSettingsOk(true);
      setSettingsMsg(
        isBedrock
          ? `Bedrock credentials work with ${data.tested?.model ?? modelInput}. Saved.`
          : `API key works with ${data.tested?.model ?? modelInput}. Saved for ${activeProvider?.label ?? provider}.`,
      );
    } catch (err) {
      setSettingsOk(false);
      setSettingsMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  function applyLiveStatus(data: DigestResponse) {
    setResult((prev) => ({
      ...prev,
      ...data,
      runId: data.runId ?? prev?.runId,
    }));
    if (data.events?.length) setEvents(data.events);
    if (data.plan) setPlan(data.plan);
    if (data.pageProgress) setPageProgress(data.pageProgress);
    if (data.outputs) setOutputs(data.outputs);
  }

  async function pollUntilDone(runId: string) {
    const gen = ++pollGenRef.current;
    const deadline = Date.now() + 90 * 60 * 1000;
    while (Date.now() < deadline) {
      if (pollGenRef.current !== gen) return;
      await sleep(700);
      if (pollGenRef.current !== gen) return;
      const stRes = await fetch(`/api/status/${encodeURIComponent(runId)}`, {
        cache: "no-store",
      });
      const raw = await stRes.text();
      let job: DigestResponse;
      try {
        job = JSON.parse(raw) as DigestResponse;
      } catch {
        throw new Error(`Failed to read job status (${stRes.status})`);
      }
      if (!stRes.ok) throw new Error(job.error ?? "Failed to read job status");
      if (pollGenRef.current !== gen) return;

      applyLiveStatus(job);
      void loadJobs();

      if (job.status === "completed") {
        await loadOutputs(job);
        setBusy(false);
        setJobFilter("completed");
        return;
      }
      if (job.status === "cancelled") {
        setError(job.error ?? "Digest cancelled");
        setBusy(false);
        setJobFilter("failed");
        if (job.markdown || job.html) await loadOutputs(job);
        return;
      }
      if (job.status === "failed") {
        setError(job.error ?? "Digest failed");
        setBusy(false);
        setJobFilter("failed");
        return;
      }
    }
    throw new Error("Digest timed out waiting for worker progress");
  }

  async function loadOutputs(paths: DigestResponse) {
    if (paths.outputs) setOutputs(paths.outputs);
    if (paths.markdown) {
      const mdRes = await fetch(`/api/output?path=${encodeURIComponent(paths.markdown)}`);
      const mdJson = (await mdRes.json()) as { content?: string; error?: string };
      if (mdJson.content) {
        setMarkdown(mdJson.content);
        setPreviewMode("markdown");
      }
    }
    if (paths.html) {
      const htmlRes = await fetch(`/api/output?path=${encodeURIComponent(paths.html)}`);
      const htmlJson = (await htmlRes.json()) as { content?: string; error?: string };
      if (htmlJson.content) {
        setHtml(htmlJson.content);
        if (!paths.markdown) setPreviewMode("html");
      }
    }
  }

  async function renderHtml(runId: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/render-html`, {
        method: "POST",
      });
      const data = (await res.json()) as {
        html?: string;
        markdown?: string;
        outputs?: RunOutputsMeta;
        error?: string;
      };
      if (!res.ok) throw new Error(data.error ?? "Failed to render HTML");
      if (data.outputs) setOutputs(data.outputs);
      await loadOutputs({
        runId,
        markdown: data.markdown,
        html: data.html,
        outputs: data.outputs,
      });
      setPreviewMode("html");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function reassemble(preset: typeof stitchPreset) {
    if (!result?.runId) return;
    setReassembleBusy(true);
    setReassembleMsg(null);
    setError(null);
    try {
      const res = await fetch(
        `/api/runs/${encodeURIComponent(result.runId)}/reassemble`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            preset,
            prompt: stitchPrompt.trim() || undefined,
            writeHtml: format === "html" || format === "both" || Boolean(html),
          }),
        },
      );
      const data = (await res.json()) as {
        markdown?: string;
        html?: string;
        markdownChars?: number;
        figurePagesFixed?: number[];
        outputs?: RunOutputsMeta;
        error?: string;
      };
      if (!res.ok) throw new Error(data.error ?? "Reassemble failed");
      if (data.outputs) setOutputs(data.outputs);
      await loadOutputs({
        runId: result.runId,
        markdown: data.markdown,
        html: data.html,
        outputs: data.outputs,
      });
      const figs = data.figurePagesFixed?.length
        ? ` · figures fixed on p.${data.figurePagesFixed.join(", ")}`
        : "";
      setReassembleMsg(
        `Reassembled · ${(data.markdownChars ?? 0).toLocaleString()} chars${figs}`,
      );
      void loadJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReassembleBusy(false);
    }
  }

  function startCompose() {
    pollGenRef.current += 1;
    setView("compose");
    setFile(null);
    setBusy(false);
    setError(null);
    setResult(null);
    setMarkdown("");
    setHtml("");
    setPlan(null);
    setPageProgress(null);
    setOutputs(null);
    setEvents([]);
  }

  async function selectJob(job: JobListItem) {
    pollGenRef.current += 1;
    setView("run");
    setError(job.status === "failed" || job.status === "cancelled" ? job.error ?? "Digest failed" : null);
    setBusy(job.status === "queued" || job.status === "running");
    setRerunModel(job.model ?? llm?.model ?? "");
    setRerunFormat((job.format as typeof rerunFormat) ?? "markdown");
    setRerunFresh(false);
    setResult({
      runId: job.runId,
      status: job.status,
      markdown: job.markdown,
      html: job.html,
      runDir: job.runDir,
      pageCount: job.pageCount,
      model: job.model,
      inputName: job.inputName,
      inputPath: job.inputPath,
      format: job.format,
      outputs: job.outputs,
      error: job.error,
      progressPct: job.progressPct,
    });
    setOutputs(job.outputs ?? null);
    setPlan(null);
    setPageProgress(null);
    setEvents([]);
    setMarkdown("");
    setHtml("");

    try {
      const stRes = await fetch(`/api/status/${encodeURIComponent(job.runId)}`, {
        cache: "no-store",
      });
      const raw = await stRes.text();
      let data: DigestResponse;
      try {
        data = JSON.parse(raw) as DigestResponse;
      } catch {
        throw new Error(`Failed to load job (${stRes.status})`);
      }
      if (!stRes.ok) throw new Error(data.error ?? "Failed to load job");

      applyLiveStatus(data);

      if (data.status === "completed") {
        await loadOutputs(data);
        setBusy(false);
        setError(null);
      } else if (data.status === "failed" || data.status === "cancelled") {
        setError(data.error ?? "Digest failed");
        setBusy(false);
        if (data.markdown || data.html) await loadOutputs(data);
      } else {
        setBusy(true);
        await pollUntilDone(job.runId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      setJobFilter("failed");
    } finally {
      void loadJobs();
    }
  }

  async function cancelJob(runId: string) {
    pollGenRef.current += 1;
    setError(null);
    setEvents((prev) => [
      ...prev,
      {
        ts: new Date().toISOString(),
        message: "Cancelling digest…",
        phase: "cancel",
      },
    ]);
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(runId)}/cancel`, {
        method: "POST",
      });
      const raw = await res.text();
      let data: DigestResponse;
      try {
        data = JSON.parse(raw) as DigestResponse;
      } catch {
        throw new Error(`Cancel failed (${res.status})`);
      }
      if (!res.ok) throw new Error(data.error ?? "Cancel failed");
      applyLiveStatus({ ...data, status: data.status ?? "cancelled" });
      if (data.status === "cancelled" || data.status === "failed") {
        setBusy(false);
        setError(data.error ?? "Digest cancelled");
        setJobFilter("failed");
      } else {
        await pollUntilDone(runId);
      }
      void loadJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function rerunJob(
    runId: string,
    opts?: { fresh?: boolean; model?: string; format?: typeof rerunFormat },
  ) {
    pollGenRef.current += 1;
    setView("run");
    setBusy(true);
    setError(null);
    setJobFilter("ongoing");
    setEvents((prev) => [
      ...prev,
      {
        ts: new Date().toISOString(),
        message: opts?.fresh ? "Rerunning from scratch…" : "Rerunning digest…",
        phase: "queue",
      },
    ]);

    const body: Record<string, unknown> = {};
    const model = opts?.model ?? rerunModel.trim();
    const fmt = opts?.format ?? rerunFormat;
    if (model) body.model = model;
    if (fmt) body.format = fmt;
    if (opts?.fresh ?? rerunFresh) body.fresh = true;

    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(runId)}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const raw = await res.text();
      let data: DigestResponse;
      try {
        data = JSON.parse(raw) as DigestResponse;
      } catch {
        throw new Error(`Rerun failed (${res.status})`);
      }
      if (!res.ok) throw new Error((data as { error?: string }).error ?? "Rerun failed");
      if (!data.runId) throw new Error("Worker did not return a runId");

      applyLiveStatus({ ...data, status: data.status ?? "queued" });
      void loadJobs();
      await pollUntilDone(data.runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      setJobFilter("failed");
    } finally {
      void loadJobs();
    }
  }

  async function retryJob(runId: string) {
    return rerunJob(runId);
  }

  async function onDigest() {
    if (!file) return;
    pollGenRef.current += 1;
    setView("run");
    setBusy(true);
    setError(null);
    setResult(null);
    setMarkdown("");
    setHtml("");
    setPlan(null);
    setPageProgress(null);
    setOutputs(null);
    setJobFilter("ongoing");
    setEvents([{ ts: new Date().toISOString(), message: "Uploading to worker…", phase: "upload" }]);

    try {
      const body = new FormData();
      body.append("file", file);
      body.append("format", format);
      if (stitchPreset !== "default") body.append("stitchPreset", stitchPreset);
      if (stitchPrompt.trim()) body.append("stitchPrompt", stitchPrompt.trim());

      const res = await fetch("/api/digest", { method: "POST", body });
      const raw = await res.text();
      let data: DigestResponse;
      try {
        data = JSON.parse(raw) as DigestResponse;
      } catch {
        throw new Error(
          res.ok
            ? "Digest response was not valid JSON"
            : `Digest failed (${res.status}). The file may be too large for the worker.`,
        );
      }
      if (!res.ok) throw new Error(data.error ?? "Digest failed");
      if (!data.runId) throw new Error("Worker did not return a runId");

      applyLiveStatus(data);
      void loadJobs();
      await pollUntilDone(data.runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      setJobFilter("failed");
    } finally {
      setBusy(false);
      void loadJobs();
    }
  }

  const progressPct = (() => {
    const total = pageProgress?.total;
    const donePages =
      pageProgress != null
        ? pageProgress.completed + pageProgress.skipped
        : undefined;
    let raw: number;
    if (total != null && total > 0 && donePages != null) {
      raw = Math.floor((donePages / total) * 100);
    } else if (result?.progressPct != null) {
      raw = Math.floor(result.progressPct);
    } else {
      return 0;
    }
    const finished = result?.status === "completed";
    // Never show 100% until the job is fully complete.
    if (!finished) return Math.min(99, Math.max(0, raw));
    return Math.min(100, Math.max(0, raw));
  })();

  const failureCopy = explainJobError(error || result?.error);

  const reviewPagesAvailable = canReviewExtractedPages({
    pageProgress: pageProgress ?? undefined,
    pageMdCount: outputs?.pageMdCount,
  });

  const extractProgressLine =
    pageProgress && pageProgress.total > 0
      ? `${pageProgress.completed} done · ${pageProgress.failed} failed · ${pageProgress.skipped} skipped / ${pageProgress.total}`
      : null;

  // Mirror sidebar progress into the open run when list polls ahead of the stage poll.
  useEffect(() => {
    if (view !== "run" || !result?.runId) return;
    const listed = jobs.find((j) => j.runId === result.runId);
    if (!listed) return;
    setResult((prev) => {
      if (!prev || prev.runId !== listed.runId) return prev;
      if (prev.status === listed.status && prev.progressPct === listed.progressPct) return prev;
      return {
        ...prev,
        status: listed.status,
        error: listed.error ?? prev.error,
        progressPct: listed.progressPct ?? prev.progressPct,
        pageCount: listed.pageCount ?? prev.pageCount,
      };
    });
    if (listed.status === "failed" && !error) {
      setError(listed.error ?? "Digest failed");
      setBusy(false);
    }
  }, [jobs, view, result?.runId, error]);

  const activeLabel = llm?.providers.find((p) => p.id === llm.activeProvider)?.label;
  const showSetupAlert = Boolean(llm && !llm.configured && !settingsOpen);

  const stage: "compose" | "processing" | "completed" | "failed" = (() => {
    if (view === "compose") return "compose";
    const status = result?.status;
    if (busy || status === "queued" || status === "running") return "processing";
    if (status === "failed" || status === "cancelled" || (error && !markdown && !html)) {
      return "failed";
    }
    if (markdown || html || status === "completed") return "completed";
    if (error) return "failed";
    return "compose";
  })();

  const stageTitle =
    stage === "compose"
      ? "New digest"
      : (result?.inputName ?? file?.name ?? "Digest");

  const rerunPanel =
    result?.runId && (stage === "failed" || stage === "completed") ? (
      <section className="rerun-panel reassemble-panel" aria-label="Rerun digest">
        <h2 className="reassemble-title">Rerun digest</h2>
        <p className="muted reassemble-help">
          Re-queue this run under the same ID. By default saved page digests resume from disk.
          Check <strong>Start fresh</strong> to re-digest every page. Change the model or output
          format before rerunning.
        </p>
        <div className="rerun-fields">
          <label className="field">
            <span>Model</span>
            {models.length > 0 ? (
              <select
                value={rerunModel}
                onChange={(e) => setRerunModel(e.target.value)}
                disabled={busy}
              >
                {!models.some((m) => m.id === rerunModel) && rerunModel ? (
                  <option value={rerunModel}>{rerunModel}</option>
                ) : null}
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name || m.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={rerunModel}
                onChange={(e) => setRerunModel(e.target.value)}
                placeholder={llm?.model ?? "Model id"}
                disabled={busy}
              />
            )}
          </label>
          <label className="field">
            <span>Output format</span>
            <select
              value={rerunFormat}
              onChange={(e) =>
                setRerunFormat(e.target.value as typeof rerunFormat)
              }
              disabled={busy}
            >
              <option value="markdown">Markdown</option>
              <option value="html">HTML</option>
              <option value="both">Both</option>
            </select>
          </label>
        </div>
        <label className="field rerun-fresh">
          <input
            type="checkbox"
            checked={rerunFresh}
            onChange={(e) => setRerunFresh(e.target.checked)}
            disabled={busy}
          />
          <span>Start fresh — ignore saved page digests</span>
        </label>
        <div className="reassemble-actions">
          <button
            type="button"
            disabled={busy || !llm?.configured}
            onClick={() => void rerunJob(result.runId!)}
          >
            {busy ? "Rerunning…" : "Rerun digest"}
          </button>
        </div>
      </section>
    ) : null;

  return (
    <div className="app-shell">
      <div className="alert-stack alert-stack-shell" aria-live="polite">
        {showSetupAlert ? (
          <Alert
            tone="warning"
            title="LLM not configured"
            action={
              <button type="button" onClick={() => setSettingsOpen(true)}>
                Open settings
              </button>
            }
          >
            <p>Choose a provider and add credentials before running a digest.</p>
          </Alert>
        ) : null}
      </div>

      <div className="app-body">
        <aside className="sidebar" aria-label="Digests">
          <button
            type="button"
            className={`sidebar-new${view === "compose" ? " active" : ""}`}
            onClick={() => startCompose()}
          >
            <span className="sidebar-new-mark" aria-hidden>
              +
            </span>
            New digest
          </button>

          <JobsPanel
            jobs={jobs}
            counts={jobCounts}
            filter={jobFilter}
            selectedRunId={view === "run" ? result?.runId : null}
            onFilterChange={setJobFilter}
            onSelect={(job) => void selectJob(job)}
            onRefresh={() => void loadJobs()}
          />

          <div className="sidebar-foot">
            {llm?.configured ? (
              <button
                type="button"
                className="status-chip ok sidebar-llm"
                title={`Key ${llm.apiKeyMasked} (${llm.source === "ui" ? "saved in UI" : "from .env"})`}
                onClick={() => setSettingsOpen(true)}
              >
                <span className="chip-dot" aria-hidden />
                {activeLabel} · {llm.model}
              </button>
            ) : llm ? (
              <button
                type="button"
                className="status-chip warn sidebar-llm"
                onClick={() => setSettingsOpen(true)}
              >
                <span className="chip-dot" aria-hidden />
                Needs setup
              </button>
            ) : null}
            <button
              type="button"
              className="secondary sidebar-settings"
              aria-expanded={settingsOpen}
              aria-controls="llm-settings"
              onClick={() => setSettingsOpen(true)}
            >
              Settings
            </button>
          </div>
        </aside>

        <section className="stage" aria-label="Digest workspace">
          <header className="stage-bar">
            <div className="stage-bar-main">
              <h1 className="stage-title">{stageTitle}</h1>
              {view === "run" && result?.runId ? (
                <p className="run-id mono muted">{result.runId}</p>
              ) : (
                <p className="stage-sub muted">
                  Image or PDF → structured Markdown for humans and agents
                </p>
              )}
            </div>
            {stage === "processing" ? (
              <div className="stage-bar-tools">
                {reviewPagesAvailable && result?.runId ? (
                  <a
                    className="button-link secondary"
                    href={`/runs/${encodeURIComponent(result.runId)}/pages`}
                    title="Review extracted pages while the digest continues"
                  >
                    Review pages
                  </a>
                ) : null}
                <button
                  type="button"
                  className="secondary"
                  disabled={!result?.runId || busy}
                  onClick={() => result?.runId && void cancelJob(result.runId)}
                >
                  Cancel digest
                </button>
              </div>
            ) : null}
            {stage === "completed" && result?.runId ? (
              <div className="stage-bar-tools">
                <div className="stage-bar-preview" role="tablist" aria-label="Preview format">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={previewMode === "markdown"}
                    className={previewMode === "markdown" ? undefined : "secondary"}
                    disabled={!markdown}
                    onClick={() => setPreviewMode("markdown")}
                  >
                    Markdown
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={previewMode === "html"}
                    className={
                      !html
                        ? "secondary is-unavailable"
                        : previewMode === "html"
                          ? undefined
                          : "secondary"
                    }
                    disabled={!html}
                    title={
                      html
                        ? "Preview HTML"
                        : "HTML not generated yet. Use Render HTML to convert Markdown only (no re-digest)."
                    }
                    onClick={() => {
                      if (html) setPreviewMode("html");
                    }}
                  >
                    HTML
                  </button>
                  {!html && markdown ? (
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      title="Convert document.md → document.html with the local Markdown renderer (no LLM)"
                      onClick={() => void renderHtml(result.runId!)}
                    >
                      Render HTML
                    </button>
                  ) : null}
                </div>
                <div className="stage-bar-actions">
                  {(outputs?.pageMdCount ?? 0) > 0 || (result.pageCount ?? 0) > 1 ? (
                    <a
                      className="button-link secondary"
                      href={`/runs/${encodeURIComponent(result.runId)}/pages`}
                    >
                      Review pages
                    </a>
                  ) : null}
                  {markdown || html ? (
                    <a
                      className="button-link"
                      href={`/api/download?runId=${encodeURIComponent(result.runId)}&format=${
                        previewMode === "html" && html
                          ? "html"
                          : "markdown"
                      }`}
                      title={
                        previewMode === "html" && html
                          ? "Download ZIP: index.html + images"
                          : "Download ZIP: document.md + images"
                      }
                    >
                      {previewMode === "html" && html
                        ? "Download HTML"
                        : "Download Markdown"}
                    </a>
                  ) : null}
                </div>
              </div>
            ) : null}
          </header>

          <div className="stage-scroll">
            {stage === "compose" ? (
                <div className="stage-compose">
                <div className="field">
                  <label htmlFor="file">Document</label>
                  <div className={`drop${file ? " has-file" : ""}`}>
                    <input
                      id="file"
                      type="file"
                      accept="image/png,image/jpeg,image/webp,application/pdf"
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    />
                    <p className="drop-title">{file ? file.name : "Drop a file or click to browse"}</p>
                    <p className="drop-meta">PNG, JPEG, WebP, or PDF</p>
                  </div>
                </div>

                <div className="compose-options">
                  <div className="field">
                    <label htmlFor="format">Format</label>
                    <select
                      id="format"
                      value={format}
                      onChange={(e) => setFormat(e.target.value as typeof format)}
                    >
                      <option value="markdown">Markdown</option>
                      <option value="html">HTML</option>
                      <option value="both">Both</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="stitchPreset">Assemble bias</label>
                    <select
                      id="stitchPreset"
                      value={stitchPreset}
                      onChange={(e) =>
                        setStitchPreset(e.target.value as typeof stitchPreset)
                      }
                    >
                      <option value="default">Balanced</option>
                      <option value="too_short">Completeness</option>
                      <option value="too_much_garbage">Cleaner output</option>
                      <option value="bad_structure">Rebuild structure</option>
                    </select>
                  </div>
                </div>

                <div className="field compose-guidance">
                  <label htmlFor="stitchPrompt">
                    Assemble guidance
                    <span className="field-optional">Optional</span>
                  </label>
                  <textarea
                    id="stitchPrompt"
                    rows={3}
                    value={stitchPrompt}
                    onChange={(e) => setStitchPrompt(e.target.value)}
                    placeholder="e.g. Keep the table of contents; strip repeating footers…"
                  />
                </div>

                <div className="compose-footer">
                  <button
                    type="button"
                    className={`compose-provider${llm?.configured ? " ok" : " warn"}`}
                    onClick={() => setSettingsOpen(true)}
                    title="Open Settings"
                  >
                    <span className="chip-dot" aria-hidden />
                    <span>
                      {llm?.configured
                        ? `${activeLabel ?? llm.activeProvider} · ${llm.model}`
                        : "Configure an LLM in Settings"}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="compose-digest"
                    disabled={!file || busy || !llm?.configured}
                    onClick={() => void onDigest()}
                  >
                    Digest
                  </button>
                </div>
              </div>
            ) : null}

            {stage === "processing" ? (
              <div className="stage-processing" aria-live="polite">
                <DigestPipelineSteps
                  events={events}
                  pageProgress={pageProgress}
                  plan={plan}
                  status={result?.status}
                />

                <div className="processing-hero">
                  <div className="processing-orb" aria-hidden />
                  <div className="processing-copy">
                    <p className="processing-kicker">Digesting</p>
                    <p className="processing-phase">
                      {pageProgress?.phase ||
                        events[events.length - 1]?.phase ||
                        result?.status ||
                        "working"}
                    </p>
                    <p className="processing-msg muted">
                      {extractProgressLine && pageProgress?.phase === "llm"
                        ? extractProgressLine
                        : events[events.length - 1]?.message || "Starting pipeline…"}
                    </p>
                  </div>
                  <span className="processing-pct mono">{progressPct}%</span>
                </div>

                <div
                  className="page-progress-bar processing-bar"
                  role="progressbar"
                  aria-valuenow={progressPct}
                >
                  <div style={{ width: `${Math.max(progressPct, busy ? 4 : 0)}%` }} />
                </div>

                {plan ? (
                  <p className="plan-summary">
                    Plan · {plan.strategy} · {plan.digestPages}/{plan.pageCount} pages
                    {plan.skippedBlank > 0 ? ` · ${plan.skippedBlank} blank skipped` : ""}
                    {` · concurrency ${plan.concurrency}`}
                  </p>
                ) : null}

                {pageProgress ? (
                  <div className="page-dots processing-dots">
                    {pageProgress.pages.map((p) => (
                      <span
                        key={p.pageNumber}
                        className={`page-dot ${p.status}`}
                        title={`Page ${p.pageNumber}: ${p.status}${p.chars != null ? ` (${p.chars} chars)` : ""}${p.error ? ` — ${p.error}` : ""}`}
                      />
                    ))}
                  </div>
                ) : null}

                {events.length > 0 ? (
                  <div className="status-log processing-log">
                    <p className="status-log-title">Pipeline</p>
                    <ol ref={statusLogListRef}>
                      {events.map((ev, i) => (
                        <li key={`${ev.ts}-${i}`} className={i === events.length - 1 ? "latest" : undefined}>
                          <span className="status-log-time mono">
                            {new Date(ev.ts).toLocaleTimeString()}
                          </span>
                          {ev.phase ? <span className="status-log-phase">{ev.phase}</span> : null}
                          <span className="status-log-msg">{ev.message}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </div>
            ) : null}

            {stage === "failed" ? (
              <div className="stage-failed">
                <Alert
                  tone="error"
                  title={failureCopy.title}
                  action={
                    result?.runId ? (
                      <button type="button" disabled={busy} onClick={() => void retryJob(result.runId!)}>
                        Retry job
                      </button>
                    ) : undefined
                  }
                >
                  <p>{failureCopy.detail}</p>
                  {failureCopy.hint ? <p className="hint">{failureCopy.hint}</p> : null}
                </Alert>
                <div className="actions">
                  {reviewPagesAvailable && result?.runId ? (
                    <a
                      className="button-link secondary"
                      href={`/runs/${encodeURIComponent(result.runId)}/pages`}
                    >
                      Review pages
                    </a>
                  ) : null}
                  {result?.runId ? (
                    <button type="button" disabled={busy} onClick={() => void retryJob(result.runId!)}>
                      Retry job
                    </button>
                  ) : null}
                  <button type="button" className="secondary" onClick={() => startCompose()}>
                    New digest
                  </button>
                </div>

                {pageProgress || progressPct > 0 ? (
                  <div className="failed-progress">
                    <DigestPipelineSteps
                      events={events}
                      pageProgress={pageProgress}
                      plan={plan}
                      status={result?.status}
                      compact
                    />
                    <div className="processing-hero compact">
                      <div className="processing-copy">
                        <p className="processing-kicker">Stopped</p>
                        <p className="processing-phase">
                          {pageProgress?.phase || result?.status || "failed"}
                        </p>
                        <p className="processing-msg muted">
                          {pageProgress
                            ? `${pageProgress.completed} done · ${pageProgress.failed} failed · ${pageProgress.skipped} skipped / ${pageProgress.total}`
                            : `${progressPct}% before failure`}
                        </p>
                      </div>
                      <span className="processing-pct mono">{progressPct}%</span>
                    </div>
                    <div
                      className="page-progress-bar processing-bar"
                      role="progressbar"
                      aria-valuenow={progressPct}
                    >
                      <div style={{ width: `${progressPct}%` }} />
                    </div>
                    {pageProgress ? (
                      <div className="page-dots processing-dots">
                        {pageProgress.pages.map((p) => (
                          <span
                            key={p.pageNumber}
                            className={`page-dot ${p.status}`}
                            title={`Page ${p.pageNumber}: ${p.status}${p.error ? ` — ${p.error}` : ""}`}
                          />
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {rerunPanel}

                {events.length > 0 ? (
                  <div className="status-log">
                    <p className="status-log-title">Pipeline · {events.length} events</p>
                    <ol>
                      {events.map((ev, i) => (
                        <li key={`${ev.ts}-${i}`} className={i === events.length - 1 ? "latest" : undefined}>
                          <span className="status-log-time mono">
                            {new Date(ev.ts).toLocaleTimeString()}
                          </span>
                          {ev.phase ? <span className="status-log-phase">{ev.phase}</span> : null}
                          <span className="status-log-msg">{ev.message}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </div>
            ) : null}

            {stage === "completed" ? (
              <div className="stage-completed">
                {error ? (
                  <Alert tone="warning" title="Finished with warnings" onDismiss={() => setError(null)}>
                    <p>{error}</p>
                  </Alert>
                ) : null}

                <OutputMeta
                  outputs={outputs}
                  model={result?.model}
                  format={result?.format ?? format}
                  pageCount={result?.pageCount}
                />

                {previewMode === "html" && html ? (
                  <div className="result-html">
                    <iframe
                      title="HTML preview"
                      srcDoc={
                        result?.runId
                          ? html.replaceAll(
                              'src="artifacts/',
                              `src="/api/runs/${encodeURIComponent(result.runId)}/artifacts/`,
                            )
                          : html
                      }
                    />
                  </div>
                ) : markdown ? (
                  <MarkdownView
                    markdown={markdown}
                    artifactBase={
                      result?.runId
                        ? `/api/runs/${encodeURIComponent(result.runId)}/artifacts`
                        : undefined
                    }
                  />
                ) : (
                  <p className="hint">No preview available for this run.</p>
                )}

                {rerunPanel}

                <section className="reassemble-panel" aria-label="Reassemble">
                  <h2 className="reassemble-title">Reassemble</h2>
                  <p className="muted reassemble-help">
                    Re-run structure + code stitch from saved page IR. Tag pages with{" "}
                    <span className="mono">figure issue</span> (or mention figures in the
                    guidance) to vision-crop missing images. No full text re-digest.
                  </p>
                  <div className="reassemble-actions">
                    <button
                      type="button"
                      className="secondary"
                      disabled={reassembleBusy}
                      onClick={() => void reassemble("too_short")}
                    >
                      Too short
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={reassembleBusy}
                      onClick={() => void reassemble("too_much_garbage")}
                    >
                      Too much garbage
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={reassembleBusy}
                      onClick={() => void reassemble("bad_structure")}
                    >
                      Bad structure
                    </button>
                    <button
                      type="button"
                      disabled={reassembleBusy}
                      onClick={() => void reassemble("default")}
                    >
                      {reassembleBusy ? "Reassembling…" : "Reassemble"}
                    </button>
                  </div>
                  <label className="field">
                    <span>Assemble guidance</span>
                    <textarea
                      rows={2}
                      value={stitchPrompt}
                      onChange={(e) => setStitchPrompt(e.target.value)}
                      placeholder="Optional prompt for the structure pass…"
                    />
                  </label>
                  {reassembleMsg ? <p className="hint">{reassembleMsg}</p> : null}
                </section>
              </div>
            ) : null}
          </div>
        </section>
      </div>

      {settingsOpen ? (
        <>
          <button
            type="button"
            className="drawer-backdrop"
            aria-label="Close settings"
            onClick={() => closeSettings()}
          />
          <aside
            className="settings-drawer"
            id="llm-settings"
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-title"
          >
            <div className="settings-head">
              <h2 id="settings-title">LLM settings</h2>
              <button type="button" className="secondary" onClick={() => closeSettings()}>
                Close
              </button>
            </div>

            {settingsMsg ? (
              <Alert
                tone={settingsOk ? "success" : "error"}
                title={settingsOk ? "Saved" : "Couldn’t save"}
                onDismiss={() => {
                  setSettingsMsg(null);
                  setSettingsOk(false);
                }}
              >
                <p>{settingsMsg}</p>
              </Alert>
            ) : null}

            <div className="settings-body">
              <div className="field">
                <label htmlFor="provider">Provider</label>
                <div className="provider-tabs" role="tablist">
                  {(llm?.providers ?? []).map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      role="tab"
                      className={p.id === provider ? undefined : "secondary"}
                      aria-selected={p.id === provider}
                      disabled={settingsBusy}
                      onClick={() => void selectProvider(p.id)}
                    >
                      {p.label}
                      {p.configured ? " ✓" : ""}
                    </button>
                  ))}
                </div>
              </div>

              {isBedrock ? (
                <>
                  <div className="field">
                    <label htmlFor="region">AWS region</label>
                    <select
                      id="region"
                      value={regionInput}
                      disabled={settingsBusy}
                      onChange={(e) => {
                        const region = e.target.value;
                        setRegionInput(region);
                        setSettingsMsg(null);
                        void fetch("/api/settings", {
                          method: "PUT",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            llm: { provider: "bedrock", region, makeActive: true },
                          }),
                        }).then(async (res) => {
                          const data = (await res.json()) as { llm?: LlmPublic };
                          if (data.llm) setLlm(data.llm);
                        });
                      }}
                    >
                      {!(BEDROCK_REGIONS as readonly string[]).includes(regionInput) &&
                      regionInput ? (
                        <option value={regionInput}>{regionInput}</option>
                      ) : null}
                      {BEDROCK_REGIONS.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="apiKey">AWS access key ID</label>
                    <input
                      id="apiKey"
                      type="password"
                      autoComplete="off"
                      placeholder={
                        activeProvider?.apiKeyMasked
                          ? `Stored: ${activeProvider.apiKeyMasked} — paste to replace`
                          : "AKIA… or leave blank to use AWS env / default chain"
                      }
                      value={apiKeyInput}
                      onChange={(e) => {
                        setApiKeyInput(e.target.value);
                        setSettingsMsg(null);
                        setSettingsOk(false);
                      }}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="secretAccessKey">AWS secret access key</label>
                    <input
                      id="secretAccessKey"
                      type="password"
                      autoComplete="off"
                      placeholder={
                        activeProvider?.secretAccessKeyMasked
                          ? `Stored: ${activeProvider.secretAccessKeyMasked} — paste to replace`
                          : "Secret access key"
                      }
                      value={secretAccessKeyInput}
                      onChange={(e) => {
                        setSecretAccessKeyInput(e.target.value);
                        setSettingsMsg(null);
                        setSettingsOk(false);
                      }}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="sessionToken">Session token (optional)</label>
                    <input
                      id="sessionToken"
                      type="password"
                      autoComplete="off"
                      placeholder={
                        activeProvider?.sessionTokenConfigured
                          ? "Stored session token — paste to replace"
                          : "Only needed for temporary STS credentials"
                      }
                      value={sessionTokenInput}
                      onChange={(e) => {
                        setSessionTokenInput(e.target.value);
                        setSettingsMsg(null);
                        setSettingsOk(false);
                      }}
                    />
                  </div>
                </>
              ) : (
                <>
                  <div className="field">
                    <label htmlFor="apiKey">{activeProvider?.label ?? "Provider"} API key</label>
                    <input
                      id="apiKey"
                      type="password"
                      autoComplete="off"
                      placeholder={
                        activeProvider?.apiKeyMasked
                          ? `Stored: ${activeProvider.apiKeyMasked} — paste to replace`
                          : `Paste ${activeProvider?.label ?? ""} API key`
                      }
                      value={apiKeyInput}
                      onChange={(e) => {
                        setApiKeyInput(e.target.value);
                        setSettingsMsg(null);
                        setSettingsOk(false);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && keyModified && !settingsBusy) {
                          e.preventDefault();
                          void testAndSaveApiKey();
                        }
                      }}
                    />
                  </div>

                  {provider === "custom" ? (
                    <div className="field">
                      <label htmlFor="baseUrl">Base URL</label>
                      <input
                        id="baseUrl"
                        type="url"
                        value={baseUrlInput}
                        onChange={(e) => setBaseUrlInput(e.target.value)}
                        placeholder="https://your-openai-compatible.example/v1"
                      />
                    </div>
                  ) : (
                    <p className="hint">
                      Base URL: <code>{activeProvider?.defaultBaseUrl || "—"}</code>
                    </p>
                  )}
                </>
              )}

              <div className="field">
                <label htmlFor="model">
                  {isBedrock ? "Model / inference profile ID" : "Model"}
                </label>
                {models.length > 0 ? (
                  <select
                    id="model"
                    value={modelInput}
                    disabled={settingsBusy}
                    onChange={(e) => void saveModel(e.target.value)}
                  >
                    {!models.some((m) => m.id === modelInput) && modelInput ? (
                      <option value={modelInput}>{modelInput}</option>
                    ) : null}
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="model"
                    type="text"
                    value={modelInput}
                    onChange={(e) => setModelInput(e.target.value)}
                    onBlur={() => {
                      if (modelInput && activeProvider?.configured) void saveModel(modelInput);
                    }}
                    placeholder={
                      activeProvider?.defaultModel ||
                      (isBedrock
                        ? "us.anthropic.claude-sonnet-4-20250514-v1:0"
                        : "model-id")
                    }
                  />
                )}
              </div>

              <div className="actions">
                {credsModified || (isBedrock && !activeProvider?.configured) ? (
                  <button
                    type="button"
                    disabled={settingsBusy}
                    onClick={() => void testAndSaveApiKey()}
                  >
                    {settingsBusy
                      ? "Testing…"
                      : isBedrock
                        ? "Test credentials"
                        : "Test API key"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="secondary"
                    disabled={settingsBusy || !activeProvider?.configured}
                    onClick={() =>
                      void loadModels(provider, {
                        region: isBedrock ? regionInput : undefined,
                      })
                    }
                  >
                    {settingsBusy ? "Loading…" : "Refresh models"}
                  </button>
                )}
                {isBedrock && activeProvider?.configured ? (
                  <button
                    type="button"
                    className="secondary"
                    disabled={settingsBusy}
                    onClick={() => void testAndSaveApiKey()}
                  >
                    {settingsBusy ? "Testing…" : "Re-test"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="secondary"
                  disabled={settingsBusy || !activeProvider?.configured}
                  onClick={() => void clearProviderKey()}
                >
                  {isBedrock ? "Clear credentials" : "Clear key"}
                </button>
              </div>

              <p className="hint">
                {isBedrock ? (
                  <>
                    Bedrock uses <strong>AWS credentials</strong> and the Converse API. Prefer
                    inference profile IDs like <code>us.anthropic…</code>. Creds persist in{" "}
                    <code>data/config.json</code>.
                  </>
                ) : (
                  <>
                    Each provider keeps its own key and model in <code>data/config.json</code>.
                    Test credentials to verify and load models.
                  </>
                )}
              </p>
            </div>
          </aside>
        </>
      ) : null}
    </div>
  );
}

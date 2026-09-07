import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";
/** Max source file size (before base64). Keep under worker JSON limit / 1.4. */
const MAX_UPLOAD_BYTES = Number(process.env.MAX_UPLOAD_BYTES ?? 100 * 1024 * 1024);

async function readWorkerJson(res: Response): Promise<{ data: Record<string, unknown>; ok: boolean }> {
  const text = await res.text();
  try {
    return { data: JSON.parse(text) as Record<string, unknown>, ok: true };
  } catch {
    const snippet = text.replace(/\s+/g, " ").slice(0, 160);
    return {
      data: {
        error: `Worker returned non-JSON (${res.status})${snippet ? `: ${snippet}` : ""}`,
      },
      ok: false,
    };
  }
}

export async function POST(req: Request) {
  try {
    const form = await req.formData();
    const file = form.get("file");
    const format = String(form.get("format") ?? "markdown");
    const stitchPrompt = String(form.get("stitchPrompt") ?? "").trim();
    const stitchPreset = String(form.get("stitchPreset") ?? "").trim();

    if (!(file instanceof File)) {
      return NextResponse.json({ error: "file is required" }, { status: 400 });
    }

    const bytes = Buffer.from(await file.arrayBuffer());
    if (bytes.length > MAX_UPLOAD_BYTES) {
      const mb = (bytes.length / (1024 * 1024)).toFixed(1);
      const maxMb = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));
      return NextResponse.json(
        { error: `File is too large (${mb} MB). Maximum upload is ${maxMb} MB.` },
        { status: 413 },
      );
    }

    // Async: worker returns runId immediately; UI polls /api/status/:runId for events.
    const res = await fetch(`${WORKER_URL}/digest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        format,
        fileName: file.name,
        fileBase64: bytes.toString("base64"),
        wait: false,
        ...(stitchPrompt ? { stitchPrompt } : {}),
        ...(stitchPreset ? { stitchPreset } : {}),
      }),
    });

    const { data } = await readWorkerJson(res);
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 500 },
    );
  }
}

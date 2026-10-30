import { useEffect, useRef, useState } from "react";
import { request } from "./api";

type Preview = {
  revision_id: string; sha256: string; size_bytes: number;
  kind: "text" | "image" | "download"; text: string | null;
  image_png_base64: string | null; truncated: boolean; reason: string | null;
  download_url: string; expires_in: number;
};

export function SamplePreview({ namespace, sample, revision, csrf }: {
  namespace: string; sample: string; revision: string; csrf: string;
}) {
  const [result, setResult] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setExpired(true), result.expires_in * 1000);
    return () => window.clearTimeout(timer);
  }, [result]);
  async function load() {
    const current = ++generation.current;
    setBusy(true); setResult(null); setError(""); setExpired(false);
    try {
      const value = await request<Preview>(
        `/api/v1/namespaces/${namespace}/samples/${sample}/revisions/${revision}/preview`, csrf,
      );
      if (generation.current === current) setResult(value);
    } catch (failure) {
      if (generation.current === current) setError((failure as Error).message);
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }
  return <section aria-label="Verified content preview">
    <h4>Verified content</h4>
    <button disabled={busy} onClick={() => void load()}>{busy ? "Verifying content…" : "Load verified preview"}</button>
    {error && <p role="alert">{error} Content may be unverified, unavailable, or inaccessible.</p>}
    {result && <>
      {result.kind === "text" && <><pre className="content-preview">{result.text}</pre>
        {result.truncated && <p>Text preview limited to 64 KiB. Download for complete content.</p>}</>}
      {result.kind === "image" && result.image_png_base64 && <img className="content-preview-image"
        src={`data:image/png;base64,${result.image_png_base64}`} alt="Verified image from the selected exact sample revision" />}
      {result.kind === "download" && <p>{result.reason}</p>}
      {expired ? <p>Download link expired. Load preview again for a fresh link.</p>
        : <a href={result.download_url} target="_blank" rel="noreferrer">Download verified original ({result.size_bytes} bytes)</a>}
    </>}
  </section>;
}

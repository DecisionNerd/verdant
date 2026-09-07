import { Suspense } from "react";
import RunPagesPage from "./PagesClient";

export default function Page() {
  return (
    <Suspense
      fallback={
        <main className="pages-review">
          <p className="hint">Loading…</p>
        </main>
      }
    >
      <RunPagesPage />
    </Suspense>
  );
}

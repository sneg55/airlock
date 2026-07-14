"use client";

import { useEffect } from "react";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="page-shell">
      <section className="empty-state error-state">
        <div className="empty-icon" aria-hidden="true">!</div>
        <h2>Can&rsquo;t reach the checkpoint</h2>
        <p>Airlock could not load the approval service. Check that the checkpoint is running, then try again.</p>
        <button className="button button-secondary" onClick={reset}>Try again</button>
      </section>
    </div>
  );
}

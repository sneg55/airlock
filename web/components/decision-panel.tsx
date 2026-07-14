"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

type Props = { approvalId: string; resourceId: string; highRisk: boolean; status: string };

export function DecisionPanel({ approvalId, resourceId, highRisk, status }: Props) {
  const router = useRouter();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const canApprove = status === "pending" && (!highRisk || confirmation === resourceId);

  async function decide(decision: "approve" | "reject") {
    setError(null);
    const response = await fetch(`/api/proposals/${encodeURIComponent(approvalId)}/${decision}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(decision === "approve" ? { confirmation } : {}),
    });
    const body = (await response.json().catch(() => null)) as { error?: string } | null;
    if (!response.ok) {
      setError(body?.error ?? "The checkpoint refused this decision.");
      return;
    }
    startTransition(() => {
      router.push("/");
      router.refresh();
    });
  }

  if (status !== "pending") {
    return <section className="decision-card"><p className="eyebrow">Decision</p><h2>Already {status}</h2><p>This proposal has already been decided. Decisions are final.</p></section>;
  }

  return (
    <section className={`decision-card ${highRisk ? "high-risk-decision" : ""}`}>
      <p className="eyebrow">Operator decision</p>
      <h2>{highRisk ? "Extra confirmation required" : "Ready for your review"}</h2>
      <p>{highRisk ? "Our review models disagreed on this action. To approve, retype the resource ID to confirm you have checked it yourself." : "Approving signs a single-use, time-limited token bound to the hash above. It authorizes this one action and nothing else."}</p>
      {highRisk ? (
        <label className="confirm-field">
          <span>Retype <code>{resourceId}</code> to confirm</span>
          <input autoComplete="off" spellCheck={false} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={resourceId} />
        </label>
      ) : null}
      {error ? <p className="decision-error" role="alert">{error}</p> : null}
      <div className="decision-actions">
        <button className="button button-approve" disabled={!canApprove || isPending} onClick={() => void decide("approve")}>{isPending ? "Signing…" : "Approve action"}</button>
        <button className="button button-reject" disabled={isPending} onClick={() => void decide("reject")}>Reject</button>
      </div>
      <p className="security-note"><span aria-hidden="true">◆</span> Signed on the server. The private key never reaches this browser.</p>
    </section>
  );
}

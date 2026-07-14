import Link from "next/link";
import { getPendingProposals } from "@/lib/checkpoint";
import { actionLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";

export default async function PendingPage() {
  const proposals = await getPendingProposals();

  return (
    <div className="page-shell">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Approval queue</p>
          <h1>Pending approvals</h1>
          <p>Nothing reaches the cloud until you approve it. Review each proposal and its evidence before you decide.</p>
        </div>
        <div className="count-card"><strong>{proposals.length}</strong><span>awaiting review</span></div>
      </section>

      {proposals.length === 0 ? (
        <section className="empty-state">
          <div className="empty-icon" aria-hidden="true">✓</div>
          <h2>You&rsquo;re all caught up</h2>
          <p>No proposals are waiting for your decision right now.</p>
        </section>
      ) : (
        <section className="proposal-list" aria-label="Pending proposals">
          {proposals.map((proposal) => {
            const action = proposal.action;
            return (
              <Link className="proposal-row" href={`/proposals/${encodeURIComponent(proposal.approval_id)}`} key={proposal.approval_id}>
                <div className={`stage-icon stage-${action?.stage ?? "unknown"}`} aria-hidden="true">
                  {action?.stage === "delete" ? "D" : "S"}
                </div>
                <div className="proposal-primary">
                  <strong>{action?.resource_id ?? "Details unavailable"}</strong>
                  <span>{action ? `${actionLabel(action.action)} · ${action.region}` : "Proposal details are unavailable"}</span>
                </div>
                <span className={`stage-pill stage-${action?.stage ?? "unknown"}`}>{action?.stage ?? "unknown"}</span>
                <code className="hash-short">{proposal.action_hash.slice(0, 12)}...</code>
                <span className="row-arrow" aria-hidden="true">›</span>
              </Link>
            );
          })}
        </section>
      )}
    </div>
  );
}

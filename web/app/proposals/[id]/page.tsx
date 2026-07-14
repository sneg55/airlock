import Link from "next/link";
import { notFound } from "next/navigation";
import { DecisionPanel } from "@/components/decision-panel";
import { DisagreementPanel } from "@/components/disagreement-panel";
import { EvidenceGrid } from "@/components/evidence-grid";
import { JsonBlock } from "@/components/json-block";
import { getProposal, isCheckpointNotFound } from "@/lib/checkpoint";
import { assessDisagreement } from "@/lib/disagreement";
import { actionLabel, stageLabel } from "@/lib/labels";

export const dynamic = "force-dynamic";

export default async function ProposalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let proposal;
  try {
    proposal = await getProposal(id);
  } catch (error) {
    if (isCheckpointNotFound(error)) notFound();
    throw error;
  }

  const action = proposal.action;
  if (!action) {
    return <div className="page-shell"><section className="empty-state"><h1>Details unavailable</h1><p>This proposal no longer has a reviewable action. It may have been approved, rejected, or expired.</p></section></div>;
  }
  const disagreement = assessDisagreement(action.evidence.disagreement);

  return (
    <div className="page-shell detail-shell">
      <Link className="back-link" href="/">‹ Back to pending</Link>
      <section className="detail-heading">
        <div>
          <div className="heading-pills">
            <span className={`stage-pill stage-${action.stage}`}>{stageLabel(action.stage)}</span>
            <span className="status-pill">{proposal.status}</span>
          </div>
          <h1>{action.resource_id}</h1>
          <p>{actionLabel(action.action)} · {action.region}</p>
        </div>
        <div className="account-label"><span>Cloud account</span><code>{action.cloud_account_id}</code></div>
      </section>

      <section className="binding-card">
        <div><p className="eyebrow">Cryptographic binding</p><h2>Action hash</h2></div>
        <code>{proposal.action_hash}</code>
        <p>Your approval signs this exact hash. If any field below changes, the signature stops matching and the executor refuses to run.</p>
      </section>

      {disagreement.present ? <DisagreementPanel assessment={disagreement} /> : null}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="content-card">
            <div className="section-heading"><div><p className="eyebrow">What you&rsquo;re approving</p><h2>Requested action</h2></div></div>
            <dl className="property-grid">
              <div><dt>Action</dt><dd>{action.action}</dd></div>
              <div><dt>Stage</dt><dd>{action.stage}</dd></div>
              <div><dt>Resource</dt><dd><code>{action.resource_id}</code></dd></div>
              <div><dt>Region</dt><dd>{action.region}</dd></div>
              <div><dt>Account</dt><dd><code>{action.cloud_account_id}</code></dd></div>
              <div><dt>Created</dt><dd>{formatDate(action.created_at)}</dd></div>
            </dl>
          </section>

          <section className="content-card">
            <div className="section-heading"><div><p className="eyebrow">Safety check</p><h2>Precondition</h2></div></div>
            <dl className="property-grid two-up">
              <div><dt>Expected status</dt><dd>{action.precondition.expected_status}</dd></div>
              <div><dt>Observed at</dt><dd>{formatDate(action.precondition.observed_at)}</dd></div>
            </dl>
          </section>

          <section className="content-card">
            <div className="section-heading"><div><p className="eyebrow">Why it&rsquo;s flagged idle</p><h2>Evidence</h2></div></div>
            <EvidenceGrid evidence={action.evidence} />
            <JsonBlock title="Raw monitor readings" value={action.evidence.monitor_provenance} />
          </section>

          <section className="content-card reason-card">
            <p className="eyebrow">Policy reason</p>
            <p>{action.policy_reason}</p>
          </section>
        </div>

        <aside className="decision-column">
          <DecisionPanel approvalId={proposal.approval_id} resourceId={action.resource_id} highRisk={disagreement.highRisk} status={proposal.status} />
        </aside>
      </div>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

import Link from "next/link";

export default function NotFound() {
  return <div className="page-shell"><section className="empty-state error-state"><div className="empty-icon">?</div><h1>Proposal not found</h1><p>It may have been approved, rejected, or expired, or the link is out of date.</p><Link className="button button-secondary" href="/">Back to queue</Link></section></div>;
}

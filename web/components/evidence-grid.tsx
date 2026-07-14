import type { Evidence } from "@/lib/types";

export function EvidenceGrid({ evidence }: { evidence: Evidence }) {
  return (
    <dl className="metric-grid">
      <Metric label="Idle window" value={`${evidence.idle_window_days} days`} />
      <Metric label="CPU average" value={`${evidence.cpu_avg}%`} />
      <Metric label="CPU maximum" value={`${evidence.cpu_max}%`} />
      <Metric label="Memory average" value={`${evidence.mem_avg}%`} />
      <Metric label="Samples" value={String(evidence.samples)} />
      <Metric label="Collected" value={formatDate(evidence.collected_at)} />
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

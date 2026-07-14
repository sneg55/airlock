import type { DisagreementAssessment } from "@/lib/disagreement";

export function DisagreementPanel({ assessment }: { assessment: DisagreementAssessment }) {
  const reviewed = assessment.votes.length;
  const dissent = assessment.votes.filter((vote) => !vote.safe).length;
  const tone = assessment.highRisk ? "disagreement-high" : dissent > 0 ? "" : "disagreement-clear";
  const heading = assessment.highRisk
    ? "High disagreement detected"
    : dissent > 0
      ? "Model disagreement recorded"
      : "Cross-model review passed";
  const roster = reviewed > 0 ? `${reviewed} model${reviewed === 1 ? "" : "s"} reviewed, ${dissent} flagged unsafe. ` : "";
  const summary =
    assessment.score === null
      ? `${roster}The jury recorded its review for this action.`
      : `${roster}Disagreement score: ${assessment.score.toFixed(2)}. High-risk threshold: ${assessment.threshold.toFixed(2)}.`;

  return (
    <section className={`disagreement-panel ${tone}`}>
      <div className="risk-icon" aria-hidden="true">{dissent > 0 ? "!" : "✓"}</div>
      <div>
        <p className="eyebrow">Cross-model review</p>
        <h2>{heading}</h2>
        <p>{summary}</p>
        {assessment.votes.length > 0 ? (
          <ul className="juror-roster">
            {assessment.votes.map((vote) => (
              <li key={vote.model}>
                <span className="juror-model">{vote.model}</span>
                <span className={`juror-pill ${vote.safe ? "juror-safe" : "juror-unsafe"}`}>{vote.safe ? "safe" : "unsafe"}</span>
                {vote.confidence !== null ? <span className="juror-conf">{Math.round(vote.confidence * 100)}% confident</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {assessment.rationales.length > 0 ? (
          <div className="rationales"><h3>Why a model objected</h3><ul>{assessment.rationales.map((rationale, index) => <li key={`${index}-${rationale}`}>{rationale}</li>)}</ul></div>
        ) : null}
      </div>
    </section>
  );
}

import type { JsonValue } from "@/lib/types";

export const HIGH_DISAGREEMENT_THRESHOLD = 0.5;

export type JurorVoteView = {
  model: string;
  safe: boolean;
  confidence: number | null;
  rationale: string;
};

export type DisagreementAssessment = {
  present: boolean;
  highRisk: boolean;
  score: number | null;
  threshold: number;
  rationales: string[];
  votes: JurorVoteView[];
};

export function assessDisagreement(value: Record<string, JsonValue> | null | undefined): DisagreementAssessment {
  if (!value) return { present: false, highRisk: false, score: null, threshold: HIGH_DISAGREEMENT_THRESHOLD, rationales: [], votes: [] };

  const score = firstNumber(value, ["score", "disagreement_score", "fraction_dissenting", "entropy"]);
  const threshold = firstNumber(value, ["threshold", "high_risk_threshold"]) ?? HIGH_DISAGREEMENT_THRESHOLD;
  const explicitHighRisk = firstBoolean(value, ["high_risk", "high_disagreement", "requires_strict_confirmation", "above_threshold"]);
  const level = typeof value.level === "string" ? value.level.toLowerCase() : "";
  const highRisk = explicitHighRisk ?? (level === "high" || (score !== null && score >= threshold));

  return { present: true, highRisk, score, threshold, rationales: collectRationales(value), votes: collectVotes(value) };
}

function collectVotes(value: Record<string, JsonValue>): JurorVoteView[] {
  const raw = value.votes;
  if (!Array.isArray(raw)) return [];
  const votes: JurorVoteView[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const model = typeof item.model === "string" ? item.model.trim() : "";
    if (!model || typeof item.safe !== "boolean") continue;
    const confidence = typeof item.confidence === "number" && Number.isFinite(item.confidence) ? item.confidence : null;
    const rationale = typeof item.rationale === "string" ? item.rationale.trim() : "";
    votes.push({ model, safe: item.safe, confidence, rationale });
  }
  return votes;
}

function firstNumber(value: Record<string, JsonValue>, keys: string[]) {
  for (const key of keys) if (typeof value[key] === "number" && Number.isFinite(value[key])) return value[key] as number;
  return null;
}

function firstBoolean(value: Record<string, JsonValue>, keys: string[]) {
  for (const key of keys) if (typeof value[key] === "boolean") return value[key] as boolean;
  return null;
}

function collectRationales(value: Record<string, JsonValue>) {
  const found: string[] = [];
  visit(value.rationales, found);
  visit(value.dissenting_rationales, found);
  visit(value.dissenting, found);
  visit(value.votes, found);
  visit(value.judgments, found);
  visit(value.reviews, found);
  visit(value.results, found);
  return [...new Set(found)];
}

function visit(value: JsonValue | undefined, found: string[]) {
  if (typeof value === "string") {
    if (value.trim()) found.push(value.trim());
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) visit(item, found);
    return;
  }
  if (value && typeof value === "object") {
    const safe = value.safe;
    const dissent = safe === false || value.dissenting === true || value.vote === "dissent";
    if (dissent && typeof value.rationale === "string" && value.rationale.trim()) found.push(value.rationale.trim());
    for (const [key, item] of Object.entries(value)) {
      if (key !== "rationale" && key !== "safe" && key !== "dissenting" && key !== "vote") visit(item, found);
    }
  }
}

import "server-only";
import type { ProposalView } from "@/lib/types";

export class CheckpointError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "CheckpointError";
  }
}

export function isCheckpointNotFound(error: unknown): error is CheckpointError {
  return error instanceof CheckpointError && error.status === 404;
}

export async function getPendingProposals(): Promise<ProposalView[]> {
  return request<ProposalView[]>("/proposals/pending");
}

export async function getProposal(approvalId: string): Promise<ProposalView> {
  return request<ProposalView>(`/proposals/${encodeURIComponent(approvalId)}`);
}

export async function submitDecision(approvalId: string, decision: "approve" | "reject"): Promise<unknown> {
  const token = process.env.OPERATOR_TOKEN;
  if (!token) throw new CheckpointError("OPERATOR_TOKEN is not configured on the web server", 500);
  return request(`/proposals/${encodeURIComponent(approvalId)}/${decision}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = process.env.CHECKPOINT_URL;
  if (!baseUrl) throw new CheckpointError("CHECKPOINT_URL is not configured on the web server", 500);

  let url: URL;
  try {
    url = new URL(path, ensureTrailingSlash(baseUrl));
  } catch {
    throw new CheckpointError("CHECKPOINT_URL is invalid", 500);
  }
  const response = await fetch(url, { ...init, cache: "no-store", signal: AbortSignal.timeout(10_000) });
  const body = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new CheckpointError(body?.detail ?? `Checkpoint request failed with status ${response.status}`, response.status);
  return body as T;
}

function ensureTrailingSlash(value: string) {
  return value.endsWith("/") ? value : `${value}/`;
}

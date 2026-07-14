import { NextRequest, NextResponse } from "next/server";
import { CheckpointError, getProposal, submitDecision } from "@/lib/checkpoint";
import { assessDisagreement } from "@/lib/disagreement";
import { rejectCrossOrigin } from "@/lib/route-security";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;
  const { id } = await params;

  try {
    const proposal = await getProposal(id);
    if (!proposal.action) return NextResponse.json({ error: "Proposal action is unavailable" }, { status: 409 });
    const assessment = assessDisagreement(proposal.action.evidence.disagreement);
    if (assessment.highRisk) {
      const body = await request.json().catch(() => null) as { confirmation?: unknown } | null;
      if (body?.confirmation !== proposal.action.resource_id) {
        return NextResponse.json({ error: "Type the exact resource ID to approve this high-disagreement action" }, { status: 400 });
      }
    }
    const result = await submitDecision(id, "approve");
    return NextResponse.json(result);
  } catch (error) {
    return checkpointErrorResponse(error);
  }
}

function checkpointErrorResponse(error: unknown) {
  if (error instanceof CheckpointError) return NextResponse.json({ error: error.message }, { status: error.status });
  console.error("Approval proxy failed", error);
  return NextResponse.json({ error: "Approval service unavailable" }, { status: 502 });
}

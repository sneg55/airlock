import { NextRequest, NextResponse } from "next/server";
import { CheckpointError, submitDecision } from "@/lib/checkpoint";
import { rejectCrossOrigin } from "@/lib/route-security";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const rejected = rejectCrossOrigin(request);
  if (rejected) return rejected;
  const { id } = await params;

  try {
    const result = await submitDecision(id, "reject");
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof CheckpointError) return NextResponse.json({ error: error.message }, { status: error.status });
    console.error("Rejection proxy failed", error);
    return NextResponse.json({ error: "Approval service unavailable" }, { status: 502 });
  }
}

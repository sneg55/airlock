import { NextRequest, NextResponse } from "next/server";

export function rejectCrossOrigin(request: NextRequest): NextResponse | null {
  const origin = request.headers.get("origin");
  const fetchSite = request.headers.get("sec-fetch-site");
  if (!origin || (fetchSite && fetchSite !== "same-origin")) {
    return NextResponse.json({ error: "Same-origin request required" }, { status: 403 });
  }

  try {
    const originHost = new URL(origin).host;
    const expectedHost = request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? request.nextUrl.host;
    if (originHost !== expectedHost) return NextResponse.json({ error: "Same-origin request required" }, { status: 403 });
  } catch {
    return NextResponse.json({ error: "Invalid request origin" }, { status: 403 });
  }
  return null;
}

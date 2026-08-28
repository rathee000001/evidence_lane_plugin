import { NextResponse } from "next/server";

import { publicBackendReadiness } from "../../_data/backend-readiness";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(publicBackendReadiness());
}

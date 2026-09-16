import { NextRequest, NextResponse } from "next/server";
import { createSession, verifyMagicLinkToken } from "@/lib/auth";

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get("token");
  if (!token) return NextResponse.redirect(new URL("/login?error=missing_token", req.url));

  const email = await verifyMagicLinkToken(token);
  if (!email) return NextResponse.redirect(new URL("/login?error=invalid_token", req.url));

  await createSession(email);
  return NextResponse.redirect(new URL("/dashboard", req.url));
}

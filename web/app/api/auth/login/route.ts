import { NextRequest, NextResponse } from "next/server";
import { createSession } from "@/lib/auth";
import { query } from "@/lib/db";
import { scryptSync, timingSafeEqual } from "crypto";

function verifyPassword(password: string, storedHash: string): boolean {
  const [salt, hash] = storedHash.split(":");
  if (!salt || !hash) return false;
  const derived = scryptSync(password, salt, 64);
  const stored = Buffer.from(hash, "hex");
  return derived.length === stored.length && timingSafeEqual(derived, stored);
}

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

  const rows = await query<{ email: string; password_hash: string }>(
    `select email, password_hash from users where email = $1`,
    [email]
  );

  if (rows.length === 0 || !verifyPassword(password, rows[0].password_hash)) {
    return NextResponse.json({ error: "Credenciais inválidas" }, { status: 401 });
  }

  await createSession(email);
  return NextResponse.json({ ok: true });
}

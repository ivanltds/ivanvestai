import { NextRequest, NextResponse } from "next/server";
import { createSession } from "@/lib/auth";
import { query } from "@/lib/db";
import { redis } from "@/lib/redis";
import { scrypt, timingSafeEqual } from "crypto";
import { promisify } from "util";

const scryptAsync = promisify(scrypt) as (password: string, salt: string, keylen: number) => Promise<Buffer>;

const MAX_ATTEMPTS = 5;
const WINDOW_SECONDS = 15 * 60;

// Hash "de mentira" pra gastar o mesmo tempo de scrypt quando o e-mail não
// existe (evita enumerar usuários pelo tempo de resposta).
const DUMMY_HASH = `${"0".repeat(32)}:${"0".repeat(128)}`;

async function verifyPassword(password: string, storedHash: string): Promise<boolean> {
  const [salt, hash] = storedHash.split(":");
  if (!salt || !hash) return false;
  const derived = await scryptAsync(password, salt, 64);
  const stored = Buffer.from(hash, "hex");
  return derived.length === stored.length && timingSafeEqual(derived, stored);
}

// Rate limit por IP no Redis (já usado pelo dashboard): 5 tentativas / 15 min.
// Se o Redis estiver fora do ar, NÃO bloqueia o login (falha aberta) -- o
// dashboard ficaria inacessível justamente quando algo está errado.
async function isRateLimited(ip: string): Promise<boolean> {
  try {
    const key = `ratelimit:login:${ip}`;
    const attempts = await redis.incr(key);
    if (attempts === 1) await redis.expire(key, WINDOW_SECONDS);
    return attempts > MAX_ATTEMPTS;
  } catch {
    return false;
  }
}

async function clearRateLimit(ip: string) {
  try {
    await redis.del(`ratelimit:login:${ip}`);
  } catch {
    // ignora
  }
}

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || "unknown";

  if (await isRateLimited(ip)) {
    return NextResponse.json({ error: "Muitas tentativas. Tente de novo em alguns minutos." }, { status: 429 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  const { email, password } = (body ?? {}) as { email?: unknown; password?: unknown };
  if (typeof email !== "string" || typeof password !== "string" || !email || !password) {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }

  const rows = await query<{ email: string; password_hash: string }>(
    `select email, password_hash from users where email = $1`,
    [email]
  );

  const valid = await verifyPassword(password, rows[0]?.password_hash ?? DUMMY_HASH);
  if (rows.length === 0 || !valid) {
    return NextResponse.json({ error: "Credenciais inválidas" }, { status: 401 });
  }

  await clearRateLimit(ip);
  try {
    await createSession(email);
  } catch (err) {
    // Tipicamente SESSION_SECRET ausente/curto em produção (lib/auth.ts). Sem este
    // aviso o usuário só via um 500 genérico e achava que a senha estava errada.
    console.error("Falha ao criar sessão:", err);
    return NextResponse.json(
      { error: "Servidor mal configurado: SESSION_SECRET ausente ou com menos de 32 caracteres." },
      { status: 500 }
    );
  }
  return NextResponse.json({ ok: true });
}

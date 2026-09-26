import { NextRequest, NextResponse } from "next/server";
import { createMagicLinkToken } from "@/lib/auth";
import { query } from "@/lib/db";
import { redis } from "@/lib/redis";

// Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 30): esta rota não tinha
// NENHUM rate limit -- diferente de /api/auth/login (senha), que já limita por
// IP. Sem isso dava pra bater aqui sem limite (custo de query no banco +
// geração de JWT a cada chamada, e um dia que o envio de e-mail for plugado,
// vira também um vetor de spam de e-mail pra qualquer endereço). Mesmo padrão
// já usado e testado em login/route.ts: 5 tentativas / 15 min por IP, falha
// ABERTA se o Redis estiver fora do ar (não pode travar login/recuperação de
// acesso justamente quando algo já está errado).
const MAX_ATTEMPTS = 5;
const WINDOW_SECONDS = 15 * 60;

async function isRateLimited(ip: string): Promise<boolean> {
  try {
    const key = `ratelimit:magic-link:${ip}`;
    const attempts = await redis.incr(key);
    if (attempts === 1) await redis.expire(key, WINDOW_SECONDS);
    return attempts > MAX_ATTEMPTS;
  } catch {
    return false;
  }
}

// Envio de e-mail em si fica a cargo do provedor escolhido na implementação
// (ex: reaproveitar o SMTP do bot, ou um serviço tipo Resend do lado do Next.js).
// Aqui só geramos e validamos o token; o TODO abaixo marca onde plugar o envio.
export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || "unknown";

  if (await isRateLimited(ip)) {
    return NextResponse.json({ error: "Muitas tentativas. Tente de novo em alguns minutos." }, { status: 429 });
  }

  let email: unknown;
  try {
    ({ email } = await req.json());
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  if (typeof email !== "string" || !email) {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }

  const rows = await query(`select 1 from users where email = $1`, [email]);
  if (rows.length === 0) {
    // Não revela se o e-mail existe ou não, por segurança.
    return NextResponse.json({ ok: true });
  }

  const token = await createMagicLinkToken(email);
  const link = `${process.env.NEXT_PUBLIC_APP_URL ?? "https://ivanvestai.vercel.app"}/api/auth/verify?token=${token}`;

  // TODO: enviar `link` por e-mail (provedor a definir na implementação).
  // O link é uma credencial de login (15 min) -- só é logado em desenvolvimento
  // local, nunca nos logs da Vercel em produção.
  if (process.env.NODE_ENV !== "production") {
    console.log("Magic link gerado:", link);
  }

  return NextResponse.json({ ok: true });
}

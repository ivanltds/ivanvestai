import { NextRequest, NextResponse } from "next/server";
import { createMagicLinkToken } from "@/lib/auth";
import { query } from "@/lib/db";

// Envio de e-mail em si fica a cargo do provedor escolhido na implementação
// (ex: reaproveitar o SMTP do bot, ou um serviço tipo Resend do lado do Next.js).
// Aqui só geramos e validamos o token; o TODO abaixo marca onde plugar o envio.
export async function POST(req: NextRequest) {
  const { email } = await req.json();

  const rows = await query(`select 1 from users where email = $1`, [email]);
  if (rows.length === 0) {
    // Não revela se o e-mail existe ou não, por segurança.
    return NextResponse.json({ ok: true });
  }

  const token = await createMagicLinkToken(email);
  const link = `${process.env.NEXT_PUBLIC_APP_URL ?? "https://ivanvestai.vercel.app"}/api/auth/verify?token=${token}`;

  // TODO: enviar `link` por e-mail (provedor a definir na implementação).
  console.log("Magic link gerado:", link);

  return NextResponse.json({ ok: true });
}

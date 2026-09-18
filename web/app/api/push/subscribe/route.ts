import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { pool } from "@/lib/db";

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  let subscription: { endpoint?: unknown; keys?: unknown };
  try {
    subscription = await req.json();
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  if (typeof subscription.endpoint !== "string" || !subscription.endpoint || !subscription.keys) {
    return NextResponse.json({ error: "subscription inválida" }, { status: 400 });
  }

  const userRows = await pool.query(`select id from users where email = $1`, [session.email]);
  if (userRows.rows.length === 0) return NextResponse.json({ error: "user not found" }, { status: 404 });

  // UUID gerado em JS (em vez de gen_random_uuid() no Postgres) pra não
  // depender da extensão pgcrypto estar habilitada no banco.
  // Idempotente por endpoint: reinscrever o mesmo navegador não duplica a linha
  // (senão cada alerta chegava N vezes).
  await pool.query(
    `insert into push_subscriptions (id, user_id, endpoint, keys_json, created_at)
     select $1::uuid, $2::uuid, $3::text, $4::json, now()
     where not exists (select 1 from push_subscriptions where endpoint = $3::text)`,
    [randomUUID(), userRows.rows[0].id, subscription.endpoint, JSON.stringify(subscription.keys)]
  );

  return NextResponse.json({ ok: true });
}

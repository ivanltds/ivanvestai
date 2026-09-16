import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { pool } from "@/lib/db";

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const subscription = await req.json();

  const userRows = await pool.query(`select id from users where email = $1`, [session.email]);
  if (userRows.rows.length === 0) return NextResponse.json({ error: "user not found" }, { status: 404 });

  // UUID gerado em JS (em vez de gen_random_uuid() no Postgres) pra não
  // depender da extensão pgcrypto estar habilitada no banco.
  await pool.query(
    `insert into push_subscriptions (id, user_id, endpoint, keys_json, created_at)
     values ($1, $2, $3, $4, now())`,
    [randomUUID(), userRows.rows[0].id, subscription.endpoint, JSON.stringify(subscription.keys)]
  );

  return NextResponse.json({ ok: true });
}

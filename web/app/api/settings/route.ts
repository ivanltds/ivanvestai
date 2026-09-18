import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { pool } from "@/lib/db";

// Só estas chaves são aceitas (as mesmas do formulário de /settings), cada uma
// com sua validação. Antes qualquer chave/valor era gravado -- inclusive
// `bot_status` (o formulário reenviava o valor de quando a página carregou e
// podia religar um bot pausado pelo kill switch) e valores absurdos como
// max_allocation_pct_per_trade=5. O bot também revalida as faixas
// (bot/core/config_store.py).
type Validator = (value: string) => boolean;

const num =
  (min: number, max: number, integer = false): Validator =>
  (value) => {
    const n = Number(value);
    return value.trim() !== "" && Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n));
  };

const VALIDATORS: Record<string, Validator> = {
  display_currency: (v) => v === "BRL" || v === "USDT",
  safety_stablecoin: (v) => /^[A-Z0-9]{2,10}$/.test(v),
  min_confidence_to_trade: num(0.5, 1),
  max_allocation_pct_per_trade: num(0.01, 1),
  daily_loss_alert_pct: num(0, 1),
  top_n_pairs: num(1, 500, true),
  cycle_interval_minutes: num(1, 1440, true),
  bypass_macro_risk_window: (v) => v === "true" || v === "false",
};

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }

  const entries: [string, string][] = [];
  const errors: string[] = [];
  for (const [key, raw] of Object.entries(body as Record<string, unknown>)) {
    const validator = VALIDATORS[key];
    if (!validator) continue; // chave não editável por aqui (ex: bot_status) -- ignorada
    const value = String(raw ?? "").trim();
    if (!validator(value)) errors.push(key);
    else entries.push([key, value]);
  }

  if (errors.length > 0) {
    return NextResponse.json({ error: "Valores inválidos", fields: errors }, { status: 400 });
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const [key, value] of entries) {
      await client.query(
        `insert into settings (key, value) values ($1, $2)
         on conflict (key) do update set value = excluded.value, updated_at = now()`,
        [key, value]
      );
    }
    await client.query("COMMIT");
  } catch {
    await client.query("ROLLBACK");
    return NextResponse.json({ error: "falha ao salvar" }, { status: 500 });
  } finally {
    client.release();
  }

  return NextResponse.json({ ok: true });
}

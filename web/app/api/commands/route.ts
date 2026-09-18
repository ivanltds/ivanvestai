import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { enqueueCommand } from "@/lib/redis";

// Ações manuais do dashboard: pausar/retomar (kill switch), forçar venda, ajustar
// a flag de venda. O bot local consome essa fila (core.redis_bridge.drain_commands)
// a cada ~15s. Só comandos que o bot de fato executa (bot/main.py SUPPORTED_COMMANDS)
// -- `open_manual_position` e `adjust_stop_take` eram aceitos aqui e ignorados em
// silêncio pelo bot, então foram removidos.
const ALLOWED = ["pause_bot", "resume_bot", "force_sell", "set_sell_flag"];
const SELL_FLAGS = ["none", "immediate", "optimized"];
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  let body: { command?: unknown; payload?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }

  const { command } = body;
  if (typeof command !== "string" || !ALLOWED.includes(command)) {
    return NextResponse.json({ error: "comando inválido" }, { status: 400 });
  }

  const payload = (body.payload && typeof body.payload === "object" ? body.payload : {}) as Record<string, unknown>;

  if (command === "force_sell" || command === "set_sell_flag") {
    if (typeof payload.position_id !== "string" || !UUID_RE.test(payload.position_id)) {
      return NextResponse.json({ error: "position_id inválido" }, { status: 400 });
    }
    if (command === "set_sell_flag" && !SELL_FLAGS.includes(String(payload.mode))) {
      return NextResponse.json({ error: "mode inválido" }, { status: 400 });
    }
  }

  await enqueueCommand(command, payload);
  return NextResponse.json({ ok: true });
}

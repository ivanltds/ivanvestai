import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { enqueueCommand } from "@/lib/redis";

// Ações manuais do dashboard: pausar/retomar (kill switch), forçar venda,
// abrir posição manual, ajustar stop/take. O bot local consome essa fila
// (core.redis_bridge.drain_commands) a cada ~15s, ou no início do próximo ciclo.
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const { command, payload } = await req.json();

  const allowed = ["pause_bot", "resume_bot", "force_sell", "set_sell_flag", "open_manual_position", "adjust_stop_take"];
  if (!allowed.includes(command)) {
    return NextResponse.json({ error: "comando inválido" }, { status: 400 });
  }

  await enqueueCommand(command, payload ?? {});
  return NextResponse.json({ ok: true });
}

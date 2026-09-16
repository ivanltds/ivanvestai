import { NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";

// Lido pelo KillSwitch (dashboard) pra saber o status ATUAL do bot (running/
// paused) direto da tabela `settings` -- mesma fonte de verdade que o bot
// (core.config_store.load_runtime_config) usa em cada ciclo. Usado tanto na
// carga inicial da página quanto no polling depois de enviar um comando de
// pausar/retomar, pra confirmar quando o bot de fato consumiu o comando
// (core.redis_bridge.drain_commands, a cada ~15s -- ver arquitetura-tecnica.md).
export async function GET() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const rows = await query<{ value: string }>(`select value from settings where key = 'bot_status' limit 1`);
  // Mesmo default do lado do bot (core/config_store.py) -- começa pausado
  // por segurança até a tabela ter uma linha explícita.
  const status = rows[0]?.value === "running" ? "running" : "paused";

  return NextResponse.json({ status });
}

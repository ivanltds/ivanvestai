import { NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";

// Alimenta a página /paper-trading com os dados que o run_paper_trading.py
// (rodando local no PC do Ivan, ver arquitetura-tecnica.md 9.5/9.6) grava na
// tabela `paper_trades` do mesmo Postgres compartilhado. Só leitura -- essa
// rota nunca escreve nada, é o script Python que grava.
export async function GET() {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  try {
    const trades = await query(
      `select id, timestamp, strategy, symbol, event, direction, price, reason, pnl_pct
       from paper_trades order by timestamp desc limit 200`
    );

    const stats = await query(
      `select strategy,
              count(*) filter (where event = 'exit') as n,
              count(*) filter (where event = 'exit' and pnl_pct > 0) as wins,
              avg(pnl_pct) filter (where event = 'exit') as avg_pnl_pct,
              sum(pnl_pct) filter (where event = 'exit') as sum_pnl_pct
       from paper_trades group by strategy order by strategy`
    );

    const latestPerPair = await query<{ strategy: string; symbol: string; event: string }>(
      `select distinct on (strategy, symbol) strategy, symbol, event, direction, price, timestamp
       from paper_trades order by strategy, symbol, timestamp desc`
    );
    const openPositions = latestPerPair.filter((r) => r.event === "entry");

    return NextResponse.json({
      trades,
      stats,
      openPositions,
      fetchedAt: new Date().toISOString(),
    });
  } catch (err: unknown) {
    const code = (err as { code?: string } | null)?.code;
    if (code === "42P01") {
      // tabela ainda não existe -- precisa rodar `python -m db.init_db` no bot/ uma vez
      return NextResponse.json({ tableMissing: true, trades: [], stats: [], openPositions: [] });
    }
    throw err;
  }
}

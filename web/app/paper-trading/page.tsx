import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";
import PaperTradingLive from "./paper-trading-live";

interface PaperTradeRow {
  id: string;
  timestamp: string;
  strategy: string;
  symbol: string;
  event: string;
  direction: string;
  price: number;
  reason: string | null;
  pnl_pct: number | null;
}

interface StrategyStat {
  strategy: string;
  n: string;
  wins: string;
  avg_pnl_pct: number | null;
  sum_pnl_pct: number | null;
}

// Renderização inicial no servidor (funciona mesmo sem JS, primeira carga
// rápida) -- depois disso, PaperTradingLive assume e faz polling em
// /api/paper-trades pra manter a tela atualizada enquanto run_paper_trading.py
// segue rodando no PC do Ivan. Ver arquitetura-tecnica.md 9.5/9.6.
export default async function PaperTradingPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  let trades: PaperTradeRow[] = [];
  let stats: StrategyStat[] = [];
  let openPositions: PaperTradeRow[] = [];
  let tableMissing = false;

  try {
    trades = await query<PaperTradeRow>(
      `select id, timestamp, strategy, symbol, event, direction, price, reason, pnl_pct
       from paper_trades order by timestamp desc limit 200`
    );

    stats = await query<StrategyStat>(
      `select strategy,
              count(*) filter (where event = 'exit') as n,
              count(*) filter (where event = 'exit' and pnl_pct > 0) as wins,
              avg(pnl_pct) filter (where event = 'exit') as avg_pnl_pct,
              sum(pnl_pct) filter (where event = 'exit') as sum_pnl_pct
       from paper_trades group by strategy order by strategy`
    );

    const latestPerPair = await query<PaperTradeRow>(
      `select distinct on (strategy, symbol) strategy, symbol, event, direction, price, timestamp
       from paper_trades order by strategy, symbol, timestamp desc`
    );
    openPositions = latestPerPair.filter((r) => r.event === "entry");
  } catch (err: unknown) {
    const code = (err as { code?: string } | null)?.code;
    if (code === "42P01") {
      tableMissing = true;
    } else {
      throw err;
    }
  }

  if (tableMissing) {
    return (
      <div className="card">
        <h2 style={{ fontSize: 16 }}>Paper trading (dry-run)</h2>
        <p style={{ color: "var(--muted)" }}>
          A tabela <code>paper_trades</code> ainda não existe nesse banco. No PC onde o bot roda, dentro
          da pasta <code>bot/</code>, rode uma vez:
        </p>
        <pre style={{ background: "#0f151d", padding: 12, borderRadius: 8, overflowX: "auto" }}>
          python -m db.init_db
        </pre>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Isso só cria a tabela nova (não mexe em nada que já existe). Depois é só recarregar esta página.
        </p>
      </div>
    );
  }

  return <PaperTradingLive initialTrades={trades} initialStats={stats} initialOpenPositions={openPositions} />;
}

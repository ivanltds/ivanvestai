import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";
import { getCached } from "@/lib/redis";
import KillSwitch from "./kill-switch";

interface Position {
  id: string;
  pair: string;
  quantity: number;
  avg_entry_price: number;
  status: string;
}

interface Trade {
  id: string;
  pair: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
}

export default async function DashboardPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const cachedBalance = await getCached<{ total_equity_usdt: number }>("balance");

  const openPositions = await query<Position>(
    `select id, pair, quantity, avg_entry_price, status from positions where status = 'open' order by opened_at desc`
  );

  const todayTrades = await query<Trade>(
    `select id, pair, side, quantity, price, timestamp from trades
     where timestamp >= date_trunc('day', now()) order by timestamp desc limit 50`
  );

  const totalEquity = cachedBalance?.total_equity_usdt ?? 0;

  return (
    <div>
      <div className="grid grid-2">
        <div className="card">
          <div style={{ color: "var(--muted)", fontSize: 13 }}>Balanço geral (BRL)</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {/* Conversão USDT -> BRL feita client-side ou via rota /api/fx — placeholder aqui */}
            {totalEquity.toLocaleString("pt-BR", { style: "currency", currency: "USD" })}
          </div>
        </div>
        <div className="card">
          <div style={{ color: "var(--muted)", fontSize: 13 }}>Status do bot</div>
          <KillSwitch />
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: 16 }}>Posições abertas</h2>
        {openPositions.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma posição aberta.</p>}
        {openPositions.map((p) => (
          <div key={p.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
            <span>{p.pair}</span>
            <span>{p.quantity} @ {p.avg_entry_price}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h2 style={{ fontSize: 16 }}>Operações de hoje</h2>
        {todayTrades.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma operação hoje.</p>}
        {todayTrades.map((t) => (
          <div key={t.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
            <span>{t.pair} — {t.side.toUpperCase()}</span>
            <span>{t.quantity} @ {t.price}</span>
          </div>
        ))}
        <a href="/api/export/trades" style={{ fontSize: 13 }}>Exportar CSV</a>
      </div>
    </div>
  );
}

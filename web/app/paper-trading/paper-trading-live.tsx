"use client";

import { useEffect, useState } from "react";

interface PaperTradeRow {
  id?: string;
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
  n: string; // bigint do Postgres chega como string no driver `pg`
  wins: string;
  avg_pnl_pct: number | string | null;
  sum_pnl_pct: number | string | null;
}

interface Props {
  initialTrades: PaperTradeRow[];
  initialStats: StrategyStat[];
  initialOpenPositions: PaperTradeRow[];
}

const REFRESH_MS = 20_000;

function fmtPct(v: number | string | null): string {
  if (v === null || v === undefined) return "--";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function pctClass(v: number | string | null): string {
  if (v === null || v === undefined) return "";
  return Number(v) >= 0 ? "positive" : "negative";
}

export default function PaperTradingLive({ initialTrades, initialStats, initialOpenPositions }: Props) {
  const [trades, setTrades] = useState(initialTrades);
  const [stats, setStats] = useState(initialStats);
  const [openPositions, setOpenPositions] = useState(initialOpenPositions);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const res = await fetch("/api/paper-trades", { cache: "no-store" });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        if (data.tableMissing) {
          setError("Tabela paper_trades ainda não existe no banco -- rode `python -m db.init_db` no bot/.");
          return;
        }
        setTrades(data.trades);
        setStats(data.stats);
        setOpenPositions(data.openPositions);
        setLastUpdate(new Date());
        setError(null);
      } catch {
        if (!cancelled) setError("Não consegui atualizar agora -- mantendo os últimos dados carregados.");
      }
    }

    const id = setInterval(refresh, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <div className="card">
        <h2 style={{ fontSize: 16 }}>Paper trading (dry-run) — 4 estratégias em paralelo</h2>
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Simulação ao vivo com dados reais da Binance, sem ordens reais nem capital envolvido
          (script local <code>run_paper_trading.py</code>). Atualiza sozinho a cada {REFRESH_MS / 1000}s
          — última atualização {lastUpdate.toLocaleTimeString("pt-BR")}.
        </p>
        {error && <p style={{ color: "var(--red)", fontSize: 13 }}>{error}</p>}
      </div>

      <div className="card">
        <h2 style={{ fontSize: 16 }}>Resumo por estratégia (trades fechados)</h2>
        {stats.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhum trade fechado ainda.</p>}
        {stats.map((s) => {
          const n = Number(s.n);
          const wins = Number(s.wins);
          const winRate = n > 0 ? (wins / n) * 100 : null;
          return (
            <div
              key={s.strategy}
              style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}
            >
              <span>{s.strategy}</span>
              <span style={{ fontSize: 13 }}>
                n={n}
                {winRate !== null && ` win_rate=${winRate.toFixed(1)}%`}{" "}
                <span className={pctClass(s.avg_pnl_pct)}>avg={fmtPct(s.avg_pnl_pct)}</span>{" "}
                <span className={pctClass(s.sum_pnl_pct)}>soma={fmtPct(s.sum_pnl_pct)}</span>
              </span>
            </div>
          );
        })}
      </div>

      <div className="card">
        <h2 style={{ fontSize: 16 }}>Posições simuladas abertas ({openPositions.length})</h2>
        {openPositions.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma no momento.</p>}
        {openPositions.map((p) => (
          <div
            key={`${p.strategy}-${p.symbol}`}
            style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}
          >
            <span>{p.strategy} — {p.symbol}</span>
            <span>{p.direction} @ {p.price}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h2 style={{ fontSize: 16 }}>Últimos eventos</h2>
        {trades.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhum evento registrado ainda.</p>}
        {trades.slice(0, 40).map((t) => (
          <div
            key={t.id ?? `${t.strategy}-${t.symbol}-${t.timestamp}`}
            style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)", fontSize: 13 }}
          >
            <span>
              {new Date(t.timestamp).toLocaleString("pt-BR")} — {t.strategy}/{t.symbol} — {t.event}
              {t.reason ? ` (${t.reason})` : ""}
            </span>
            <span>
              {t.price}
              {t.pnl_pct !== null && (
                <span className={pctClass(t.pnl_pct)} style={{ marginLeft: 8 }}>
                  {fmtPct(t.pnl_pct)}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

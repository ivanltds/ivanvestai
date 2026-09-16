"use client";

// Grade de cards da carteira real na Binance, um por ativo, com o ícone da
// moeda (ver lib/coin-icon.ts). Client Component porque o fallback pro ícone
// genérico usa onError -- Server Component não consegue anexar handler de
// evento a elemento nenhum.
import { coinIconUrl, GENERIC_COIN_ICON } from "@/lib/coin-icon";
import Money from "./money";

interface WalletGridRow {
  asset: string;
  quantity: number;
  valueUsdt: number;
}

export default function WalletGrid({ rows }: { rows: WalletGridRow[] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
        gap: 12,
        marginTop: 8,
      }}
    >
      {rows.map((r) => (
        <div
          key={r.asset}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            border: "1px solid var(--border)",
            borderRadius: 10,
            background: "var(--bg)",
          }}
        >
          <img
            src={coinIconUrl(r.asset)}
            alt={r.asset}
            width={32}
            height={32}
            style={{ borderRadius: "50%", flexShrink: 0 }}
            onError={(e) => {
              const img = e.currentTarget;
              if (img.src !== GENERIC_COIN_ICON) {
                img.src = GENERIC_COIN_ICON;
              }
            }}
          />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>{r.asset}</div>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>{r.quantity}</div>
            <div style={{ fontSize: 13 }}><Money usdt={r.valueUsdt} /></div>
          </div>
        </div>
      ))}
    </div>
  );
}

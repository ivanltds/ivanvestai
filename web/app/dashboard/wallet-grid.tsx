"use client";

// Grade de cards da carteira real na Binance, um por ativo, com o ícone da
// moeda (ver lib/coin-icon.ts). Client Component porque o fallback pro ícone
// genérico usa onError -- Server Component não consegue anexar handler de
// evento a elemento nenhum.
//
// Posições de baixa relevância (< ~1 BRL) ficam colapsadas por padrão --
// carteiras reais acumulam poeira (sobra de conversão, airdrop, etc.) que
// só teria custo de fricção pra vender e não ajuda a decisão de "o que
// fazer com o que importa". Um link discreto expande pra ver tudo.
import { useMemo, useState, type CSSProperties } from "react";
import { coinIconUrl, GENERIC_COIN_ICON } from "@/lib/coin-icon";
import { useCurrency } from "./currency-context";
import Money from "./money";

interface PositionReview {
  decision: "hold" | "sell";
  confidence: number;
  reasoning: string;
  acted: boolean;
}

interface WalletGridRow {
  asset: string;
  quantity: number;
  valueUsdt: number;
  review: PositionReview | null;
}

// Fallback quando a cotação BRL ainda não chegou do client (ver
// currency-context.tsx): mesmo corte aproximado (~1 BRL) que o
// PositionReviewAgent usa no bot (DUST_THRESHOLD_USDT, ver
// agents/position_review_agent.py) -- mantém os dois lados consistentes.
const DUST_THRESHOLD_USDT_FALLBACK = 0.2;
const DUST_THRESHOLD_BRL = 1;

function ReviewBadge({ review }: { review: PositionReview }) {
  if (review.decision === "hold") {
    return (
      <span
        title={review.reasoning}
        style={{ fontSize: 11, color: "var(--green)", cursor: "default" }}
      >
        IA: manter
      </span>
    );
  }
  return (
    <span
      title={review.reasoning}
      style={{ fontSize: 11, color: review.acted ? "var(--red)" : "var(--accent)", cursor: "default" }}
    >
      {review.acted ? "IA vendeu esta posição" : `IA: vender (${Math.round(review.confidence * 100)}%)`}
    </span>
  );
}

function WalletCard({ row }: { row: WalletGridRow }) {
  return (
    <div
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
        src={coinIconUrl(row.asset)}
        alt={row.asset}
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
        <div style={{ fontWeight: 600 }}>{row.asset}</div>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>{row.quantity}</div>
        <div style={{ fontSize: 13 }}><Money usdt={row.valueUsdt} /></div>
        {row.review && (
          <div style={{ marginTop: 2 }}>
            <ReviewBadge review={row.review} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function WalletGrid({ rows }: { rows: WalletGridRow[] }) {
  const { currency, brlRate } = useCurrency();
  const [showDust, setShowDust] = useState(false);

  const { relevant, dust } = useMemo(() => {
    const isDust = (usdt: number) => {
      if (currency === "BRL" && brlRate) return usdt * brlRate < DUST_THRESHOLD_BRL;
      return usdt < DUST_THRESHOLD_USDT_FALLBACK;
    };
    const relevant: WalletGridRow[] = [];
    const dust: WalletGridRow[] = [];
    for (const r of rows) {
      (isDust(r.valueUsdt) ? dust : relevant).push(r);
    }
    return { relevant, dust };
  }, [rows, currency, brlRate]);

  const gridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
    gap: 12,
    marginTop: 8,
  };

  return (
    <div>
      <div style={gridStyle}>
        {relevant.map((r) => (
          <WalletCard key={r.asset} row={r} />
        ))}
      </div>

      {dust.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button
            onClick={() => setShowDust((v) => !v)}
            style={{
              background: "none",
              border: "none",
              color: "var(--muted)",
              fontSize: 12,
              cursor: "pointer",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {showDust
              ? "ocultar posições sem relevância/liquidez"
              : `mostrar posições sem relevância/liquidez (${dust.length})`}
          </button>
          {showDust && (
            <div style={{ ...gridStyle, marginTop: 8 }}>
              {dust.map((r) => (
                <WalletCard key={r.asset} row={r} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

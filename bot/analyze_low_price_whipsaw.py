"""Diagnóstico só-leitura (nenhum UPDATE/DELETE/INSERT, nenhuma ordem enviada):
compara o desempenho de trades REAIS (is_paper=false) em pares de preço baixo
(potencialmente mais voláteis no curtíssimo prazo, ex: BANKUSDT, GUSDT, SAGAUSDT
vistos hoje) contra o resto da carteira, e por estratégia.

Metodologia (mesmo padrão de sweep/pooling/t-estatístico usado nos backtests
anteriores, ver arquitetura-tecnica.md 9.5): junta cada par compra+venda real
da mesma Position (via position_id) pra calcular pnl%, e tenta anexar a
estratégia olhando a Opportunity mais recente daquele par ANTES da compra
(mesma janela usada pelo comitê pra aprovar a entrada).

Rodar de dentro de bot/: `python analyze_low_price_whipsaw.py`
"""
from __future__ import annotations

import datetime as dt
import statistics

from config.settings import settings
from core.binance_client import binance_client
from db.session import get_session
from sqlalchemy import text

# Preço de entrada abaixo disso é tratado como "baixo preço" (proxy pra
# tokens pequenos/potencialmente mais voláteis no curtíssimo prazo) --
# ajuste esse corte se quiser refinar.
LOW_PRICE_THRESHOLD = 0.10

# Janela de busca da Opportunity mais recente antes da compra, pra
# recuperar a estratégia (trend_following/mean_reversion/breakout/scalping).
OPPORTUNITY_LOOKBACK_MINUTES = 20


def _fees_in_quote(legs: list, pair: str) -> tuple[float, bool]:
    """Converte a taxa registrada (fee/fee_asset) de cada trade (compra + venda)
    pra stablecoin de segurança e soma. Achado 24/09/2026 (arquitetura-tecnica.md
    9.21 item 21): esse script comparava pnl% SÓ de preço -- justamente o
    problema que o comentário de settings.min_stop_loss_pct já apontava
    qualitativamente ("net ~0 depois de 0,2% de taxas"), sem nunca ter sido
    corrigido aqui de fato. `fee_asset` pode ser a própria quote (USDT -- já
    pronto), o ativo base do par (usa o preço do próprio trade) ou BNB
    (desconto de taxa -- usa o preço ATUAL via get_last_price_via_bridge,
    aproximação aceitável já que a taxa é uma fração pequena do valor total,
    não o preço histórico exato no momento do trade). Devolve (total_usd, ok)
    -- ok=False se algum leg não deu pra converter, e nesse caso o chamador
    não descarta o trade, só não teria como descontar a taxa dele."""
    quote = settings.safety_stablecoin
    total = 0.0
    for leg in legs:
        fee = float(leg.fee or 0.0)
        if fee == 0.0:
            continue
        asset = (leg.fee_asset or "").upper()
        if not asset or asset == quote:
            total += fee
            continue
        if pair.endswith(quote) and asset == pair.removesuffix(quote):
            total += fee * float(leg.price)
            continue
        try:
            total += fee * binance_client.get_last_price_via_bridge(asset, quote)
        except Exception:
            return 0.0, False
    return total, True


def t_stat(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return None
    return mean / (stdev / (n ** 0.5))


def main() -> None:
    with get_session() as session:
        # Todas as compras e vendas reais vinculadas a uma Position (exclui
        # sell_wallet_asset, que não tem position_id -- essas não são
        # trades "abertas pelo comitê" no sentido usado aqui).
        buys = session.execute(
            text(
                "SELECT position_id, pair, price, quantity, fee, fee_asset, timestamp FROM trades "
                "WHERE is_paper=false AND side='buy' AND position_id IS NOT NULL "
                "ORDER BY timestamp"
            )
        ).fetchall()
        sells = session.execute(
            text(
                "SELECT position_id, pair, price, quantity, fee, fee_asset, timestamp, reason FROM trades "
                "WHERE is_paper=false AND side='sell' AND position_id IS NOT NULL "
                "ORDER BY timestamp"
            )
        ).fetchall()
        sells_by_pos = {s.position_id: s for s in sells}

        rows = []
        for b in buys:
            s = sells_by_pos.get(b.position_id)
            if s is None:
                continue  # posição ainda aberta, sem pnl realizado ainda
            pnl_gross_pct = (s.price - b.price) / b.price * 100.0

            # PnL LÍQUIDO de taxa (o número usado daqui pra baixo em todas as
            # estatísticas -- win rate, pnl médio, t-stat) -- ver _fees_in_quote acima.
            buy_value = b.quantity * b.price
            fee_usd, fee_ok = _fees_in_quote([b, s], b.pair)
            if fee_ok and buy_value:
                pnl_pct = ((s.price - b.price) * b.quantity - fee_usd) / buy_value * 100.0
            else:
                pnl_pct = pnl_gross_pct  # taxa não conversível -- fallback pro bruto, sem quebrar o script

            # Estratégia: Opportunity mais recente do mesmo par, antes da
            # compra, dentro da janela de lookback.
            opp = session.execute(
                text(
                    "SELECT strategy FROM opportunities WHERE pair = :pair "
                    "AND timestamp <= :buy_ts AND timestamp >= :cutoff "
                    "ORDER BY timestamp DESC LIMIT 1"
                ),
                {
                    "pair": b.pair,
                    "buy_ts": b.timestamp,
                    "cutoff": b.timestamp - dt.timedelta(minutes=OPPORTUNITY_LOOKBACK_MINUTES),
                },
            ).fetchone()
            strategy = opp.strategy if opp else "desconhecida"

            rows.append(
                {
                    "pair": b.pair,
                    "entry_price": b.price,
                    "exit_price": s.price,
                    "pnl_pct": pnl_pct,
                    "pnl_gross_pct": pnl_gross_pct,
                    "strategy": strategy,
                    "exit_reason": s.reason,
                    "duration_min": (s.timestamp - b.timestamp).total_seconds() / 60.0,
                    "buy_ts": b.timestamp,
                }
            )

        print(f"Total de posições reais fechadas com pnl calculável: {len(rows)}")
        print()

        print("=" * 78)
        print("TODAS AS OPERAÇÕES (ordenadas por data)")
        print("=" * 78)
        for r in sorted(rows, key=lambda r: r["buy_ts"]):
            tier = "BAIXO PREÇO" if r["entry_price"] < LOW_PRICE_THRESHOLD else "normal"
            print(
                f"{r['buy_ts']} | {r['pair']:12s} | {r['strategy']:16s} | tier={tier:11s} | "
                f"pnl_liquido={r['pnl_pct']:+.2f}% (bruto={r['pnl_gross_pct']:+.2f}%) | "
                f"dur={r['duration_min']:.1f}min | saida={r['exit_reason']}"
            )

        def segment_stats(label: str, subset: list[dict]) -> None:
            if not subset:
                print(f"{label}: sem dados")
                return
            pnls = [r["pnl_pct"] for r in subset]
            wins = sum(1 for p in pnls if p > 0)
            print(
                f"{label}: n={len(pnls)} win_rate={wins/len(pnls)*100:.1f}% "
                f"pnl_medio={statistics.mean(pnls):+.3f}% t={t_stat(pnls)}"
            )

        print()
        print("=" * 78)
        print("POR FAIXA DE PREÇO")
        print("=" * 78)
        low = [r for r in rows if r["entry_price"] < LOW_PRICE_THRESHOLD]
        normal = [r for r in rows if r["entry_price"] >= LOW_PRICE_THRESHOLD]
        segment_stats(f"Baixo preço (< ${LOW_PRICE_THRESHOLD})", low)
        segment_stats(f"Preço normal (>= ${LOW_PRICE_THRESHOLD})", normal)

        print()
        print("=" * 78)
        print("POR ESTRATÉGIA (todas as faixas de preço)")
        print("=" * 78)
        strategies = sorted(set(r["strategy"] for r in rows))
        for strat in strategies:
            segment_stats(strat, [r for r in rows if r["strategy"] == strat])

        print()
        print("=" * 78)
        print("POR ESTRATÉGIA x FAIXA DE PREÇO (célula com n>=2)")
        print("=" * 78)
        for strat in strategies:
            for tier_name, tier_rows in [("baixo preço", low), ("normal", normal)]:
                cell = [r for r in tier_rows if r["strategy"] == strat]
                if len(cell) >= 2:
                    segment_stats(f"{strat} / {tier_name}", cell)

        print()
        print("=" * 78)
        print("DURAÇÃO DA OPERAÇÃO (todas)")
        print("=" * 78)
        durations = [r["duration_min"] for r in rows]
        if durations:
            print(f"mediana={statistics.median(durations):.1f}min media={statistics.mean(durations):.1f}min "
                  f"min={min(durations):.1f}min max={max(durations):.1f}min")
            rapidas = [r for r in rows if r["duration_min"] < 5]
            print(f"Operações fechadas em menos de 5min: {len(rapidas)} de {len(rows)}")
            segment_stats("Fechadas em <5min", rapidas)


if __name__ == "__main__":
    main()

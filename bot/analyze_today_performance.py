"""Diagnóstico só-leitura (nenhum UPDATE/DELETE/INSERT): desempenho do bot
HOJE (data local da máquina onde este script roda) — reúne P&L de mercado
real (core.equity, que já desconta aportes/retiradas -- ver seu docstring
e arquitetura-tecnica.md 9.17), trades reais do dia, vereditos do
PositionReviewAgent, decisões do comitê e posições abertas no momento.

Rodar de dentro de bot/: `python analyze_today_performance.py`
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from config.settings import settings
from core.binance_client import binance_client
from core.equity import daily_market_pnl_usdt
from db.session import get_session


def usd(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}US$ {v:,.4f}"


def pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _fees_in_quote(legs: list, pair: str) -> tuple[float, bool]:
    """Converte a taxa registrada (fee/fee_asset) de cada trade pra stablecoin
    de segurança e soma. Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item
    21): o PnL% desta seção era só de preço, sem descontar a taxa de ~0,1%/lado
    que a Binance cobra (mesmo problema já apontado qualitativamente no
    comentário de settings.min_stop_loss_pct: "net ~0 depois de 0,2% de
    taxas"). `fee_asset` pode ser a própria quote (USDT -- já pronto), o ativo
    base do par (usa o preço do próprio trade) ou BNB (desconto de taxa -- usa
    o preço ATUAL via get_last_price_via_bridge, aproximação aceitável já que
    a taxa é uma fração pequena do valor total). Devolve (total_usd, ok) --
    ok=False se algum leg não deu pra converter."""
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


def main() -> None:
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    print("=" * 78)
    print(f"DESEMPENHO DE HOJE ({today.isoformat()}, data local da máquina)")
    print("=" * 78)

    with get_session() as session:
        # --- 1. P&L de mercado real (exclui aportes/retiradas -- ver core/equity.py) ---
        # Multi-conta (multi-conta-plano.md, Fase C): sem account_id, soma o P&L de
        # TODAS as contas juntas -- ok pra uma visão geral rápida, mas não separa
        # qual conta contribuiu com o quê (ver seção 2 abaixo, que já é por conta).
        try:
            mkt_pnl = daily_market_pnl_usdt(today)
            print(f"\nP&L de mercado hoje (todas as contas somadas, core.equity, exclui aportes/retiradas): {usd(mkt_pnl)}")
        except Exception as e:
            print(f"\nP&L de mercado hoje: falhou ao calcular ({e!r})")

        # --- 2. daily_equity: hoje vs ontem, POR CONTA -----------------------
        # Achado 25/09/2026 (multi-conta-plano.md, Fase C): daily_equity passou a
        # ter uma linha por (date, account_id) em vez de uma por date sozinho (ver
        # migrate_add_daily_equity_pk.py) -- com 2+ contas ativas, "hoje" e "ontem"
        # cada um pode ter mais de uma linha (uma por conta), então a query agrupa
        # por conta antes de calcular a variação.
        eq_rows = session.execute(
            text(
                "SELECT de.date, de.equity_usdt, de.equity_brl, de.account_id, a.label AS account_label "
                "FROM daily_equity de LEFT JOIN accounts a ON a.id = de.account_id "
                "WHERE de.date IN (:d1, :d2) ORDER BY de.account_id NULLS FIRST, de.date"
            ),
            {"d1": yesterday, "d2": today},
        ).fetchall()
        print("\n--- daily_equity (snapshot diário, por conta) ---")
        if not eq_rows:
            print("Nenhum registro de daily_equity pra hoje/ontem ainda.")
        else:
            by_account: dict[object, list] = {}
            for r in eq_rows:
                by_account.setdefault(r.account_id, []).append(r)
            for account_id, rows in by_account.items():
                label = rows[0].account_label or f"(sem conta, id={account_id})"
                print(f"  Conta: {label}")
                for r in rows:
                    print(f"    {r.date} | equity_usdt=US$ {r.equity_usdt:,.4f}  equity_brl=R$ {r.equity_brl:,.2f}")
                if len(rows) == 2:
                    delta = rows[1].equity_usdt - rows[0].equity_usdt
                    delta_pct = (delta / rows[0].equity_usdt * 100) if rows[0].equity_usdt else 0.0
                    print(f"    Variação equity_usdt (ontem -> hoje): {usd(delta)} ({pct(delta_pct)}) -- ATENÇÃO: isso inclui aportes/retiradas, ao contrário do P&L de mercado acima.")

        # --- 3. Trades reais de hoje (is_paper=false) ---
        print("\n--- Trades REAIS hoje (is_paper=false) ---")
        trades = session.execute(
            text(
                "SELECT id, position_id, pair, side, quantity, price, fee, fee_asset, reason, timestamp "
                "FROM trades WHERE is_paper=false "
                "AND date(timestamp AT TIME ZONE 'America/Sao_Paulo') = date(now() AT TIME ZONE 'America/Sao_Paulo') "
                "ORDER BY timestamp"
            )
        ).fetchall()
        if not trades:
            print("Nenhum trade real hoje.")
        else:
            for t in trades:
                value = t.quantity * t.price
                print(
                    f"{t.timestamp} | {t.side.upper():4s} {t.pair:12s} qty={t.quantity:.6f} "
                    f"preço={t.price:.6f} valor=US$ {value:,.2f} taxa={t.fee:.6f} {t.fee_asset} "
                    f"motivo={t.reason}"
                )
            n_buy = sum(1 for t in trades if t.side == "buy")
            n_sell = sum(1 for t in trades if t.side == "sell")
            print(f"Total: {len(trades)} trades reais hoje ({n_buy} compra(s), {n_sell} venda(s)).")

            # Pareamento simples: pra cada position_id com trade de venda hoje, busca a compra
            # mais recente (mesmo position_id, qualquer data) pra estimar pnl% de preço.
            sell_ids = {t.position_id for t in trades if t.side == "sell" and t.position_id is not None}
            if sell_ids:
                print("\nPnL% estimado das vendas reais de hoje (preço de saída vs. preço médio de entrada da posição):")
                for pid in sell_ids:
                    row = session.execute(
                        text("SELECT avg_entry_price, pair FROM positions WHERE id=:pid"), {"pid": pid}
                    ).fetchone()
                    sell_trade = next(t for t in trades if t.side == "sell" and t.position_id == pid)
                    if row and row.avg_entry_price:
                        pnl_gross = (sell_trade.price / row.avg_entry_price - 1) * 100
                        net_txt = ""
                        buy_trades = session.execute(
                            text(
                                "SELECT quantity, price, fee, fee_asset FROM trades "
                                "WHERE position_id=:pid AND side='buy'"
                            ),
                            {"pid": pid},
                        ).fetchall()
                        buy_value = sum(b.quantity * b.price for b in buy_trades)
                        if buy_value:
                            fee_usd, fee_ok = _fees_in_quote(list(buy_trades) + [sell_trade], row.pair)
                            if fee_ok:
                                sell_value = sell_trade.quantity * sell_trade.price
                                pnl_net_pct = ((sell_value - buy_value) - fee_usd) / buy_value * 100
                                net_txt = f"  |  líquido de taxa: {pct(pnl_net_pct)} (taxas=US$ {fee_usd:,.4f})"
                        print(
                            f"  {row.pair}: entrada={row.avg_entry_price:.6f} saída={sell_trade.price:.6f} "
                            f"-> bruto: {pct(pnl_gross)}{net_txt}"
                        )

        # --- 4. PositionReviewAgent hoje ---
        print("\n--- PositionReviewAgent hoje ---")
        reviews = session.execute(
            text(
                "SELECT asset, decision, confidence, acted, is_paper, timestamp FROM position_reviews "
                "WHERE date(timestamp AT TIME ZONE 'America/Sao_Paulo') = date(now() AT TIME ZONE 'America/Sao_Paulo') "
                "ORDER BY timestamp"
            )
        ).fetchall()
        if not reviews:
            print("Nenhum veredito hoje.")
        else:
            n_hold = sum(1 for r in reviews if r.decision == "hold")
            n_sell = sum(1 for r in reviews if r.decision == "sell")
            n_acted = sum(1 for r in reviews if r.acted)
            print(f"{len(reviews)} vereditos hoje: {n_hold} hold, {n_sell} sell, {n_acted} com venda executada.")
            for r in reviews:
                tag = " [VENDEU]" if r.acted else ""
                paper_tag = " (simulado)" if r.acted and r.is_paper else ""
                print(f"  {r.timestamp} | {r.asset:8s} {r.decision:4s} conf={r.confidence:.0%}{tag}{paper_tag}")

        # --- 5. Comitê (oportunidades de entrada) hoje ---
        print("\n--- Oportunidades avaliadas pelo comitê hoje ---")
        opps = session.execute(
            text(
                "SELECT status, count(*) AS n, avg(final_confidence) AS avg_conf FROM opportunities "
                "WHERE date(timestamp AT TIME ZONE 'America/Sao_Paulo') = date(now() AT TIME ZONE 'America/Sao_Paulo') "
                "GROUP BY status ORDER BY status"
            )
        ).fetchall()
        if not opps:
            print("Nenhuma oportunidade registrada hoje.")
        else:
            for o in opps:
                conf_txt = f", confiança média={o.avg_conf:.0%}" if o.avg_conf is not None else ""
                print(f"  {o.status}: {o.n}{conf_txt}")

        # --- 6. Posições abertas agora ---
        print("\n--- Posições abertas AGORA (status=open) ---")
        open_pos = session.execute(
            text(
                "SELECT pair, quantity, avg_entry_price, stop_price, take_price, is_paper, opened_at "
                "FROM positions WHERE status='open' ORDER BY opened_at"
            )
        ).fetchall()
        if not open_pos:
            print("Nenhuma posição aberta.")
        else:
            for p in open_pos:
                paper_tag = " [PAPER]" if p.is_paper else " [REAL]"
                stop_txt = f"{p.stop_price:.6f}" if p.stop_price else "-"
                take_txt = f"{p.take_price:.6f}" if p.take_price else "-"
                print(
                    f"  {p.pair:12s}{paper_tag} qty={p.quantity:.6f} entrada={p.avg_entry_price:.6f} "
                    f"stop={stop_txt} take={take_txt} aberta_em={p.opened_at}"
                )

        # --- 7. Custo de API hoje (contexto, não é o foco, mas é barato de mostrar) ---
        cost = session.execute(
            text(
                "SELECT count(*) AS n, coalesce(sum(estimated_cost_usd),0) AS cost FROM api_cost_log "
                "WHERE date(timestamp AT TIME ZONE 'America/Sao_Paulo') = date(now() AT TIME ZONE 'America/Sao_Paulo')"
            )
        ).fetchone()
        print(f"\n--- Custo de API hoje ---\n{cost.n} chamadas, {usd(cost.cost)}")


if __name__ == "__main__":
    main()

"""Relatório detalhado de performance dos dados de DRY-RUN acumulados até
agora -- pra alimentar a decisão de quando (ou se) sair do dry_run com
capital real (ver arquitetura-tecnica.md 9.5 e 9.9).

Junta 3 fontes, que são POPULAÇÕES DIFERENTES e não devem ser misturadas:

1. `paper_trades` -- o paper trading contínuo (run_paper_trading.py,
   rodando desde a sessão da seção 9.6), 4 configurações de estratégia
   (committee/kotegawa/rapf_filtros/rapf_sem_filtros) em paralelo, direto
   nos indicadores técnicos, SEM passar pelos agentes de LLM. É a fonte
   com mais volume de dados e mais tempo rodando.

2. `trades`/`positions` com is_paper=true -- o pipeline REAL dos 7 agentes
   (run_cycle_once.py, testado manualmente na seção 9.9). Amostra pequena
   e recente (só as execuções manuais de hoje), mas é o único dado que
   passou pelo RiskCommitteeAgent/ViabilityAgent de verdade (com LLM).

3. `position_reviews` -- vereditos do PositionReviewAgent sobre a carteira
   pré-existente (hold/sell). Desde 16/09/2026 (ver arquitetura-tecnica.md
   9.10) cada veredito grava `price_at_review` (preço unitário no momento),
   o que permite comparar com o preço ATUAL do ativo e dar uma leitura
   aproximada de "o veredito teria sido bom até agora" -- não é uma medida
   perfeita (não sabemos se você teria realocado o capital em algo melhor
   num "sell" certo, por exemplo), mas já é muito melhor que nada. Vereditos
   de ANTES dessa data ficam com `price_at_review = NULL` (não dá pra
   reconstruir retroativamente) e são ignorados nessa comparação.

Mesma metodologia estatística do backtest sweep (seção 9.5): win rate,
pnl médio, e um t-estatístico simples (média / erro-padrão) como proxy
de "isso parece sinal ou ruído" -- regra prática: |t| > ~2 sugere sinal
real. Com amostras pequenas (esperado neste estágio), o t vai ficar
baixo/instável -- isso É a informação: ainda não dá pra confiar.

Uso:
    python analyze_dry_run_performance.py
    python analyze_dry_run_performance.py --json   # saída também em JSON,
                                                     # pra colar de volta
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict

from config.settings import settings
from core.binance_client import binance_client
from db.session import get_session
from sqlalchemy import text


def _tstat(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return None
    return mean / (stdev / (n ** 0.5))


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}%"


def _fmt_t(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def section_paper_trades(session) -> dict:
    print("\n" + "=" * 78)
    print("1) PAPER TRADING CONTÍNUO (paper_trades) -- sem LLM, só indicadores")
    print("=" * 78)

    rows = session.execute(text(
        "select strategy, symbol, event, direction, price, reason, pnl_pct, timestamp "
        "from paper_trades order by strategy, symbol, timestamp"
    )).mappings().all()

    if not rows:
        print("Nenhum registro em paper_trades ainda.")
        return {"count": 0, "by_strategy": {}}

    # Agrupa por estratégia só os eventos de SAÍDA (pnl_pct só existe quando fecha)
    by_strategy: dict[str, list[float]] = defaultdict(list)
    open_count: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["event"] == "exit" and r["pnl_pct"] is not None:
            by_strategy[r["strategy"]].append(float(r["pnl_pct"]))
        elif r["event"] == "entry":
            open_count[r["strategy"]] += 1

    result = {"count": len(rows), "by_strategy": {}}
    print(f"\nTotal de eventos registrados: {len(rows)}")
    print(f"{'Estratégia':<20}{'Trades fechados':>16}{'Win rate':>12}{'PnL médio':>14}{'t-stat':>10}")
    print("-" * 78)
    for strategy in sorted(set(list(by_strategy.keys()) + list(open_count.keys()))):
        pnls = by_strategy.get(strategy, [])
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        win_rate = (wins / n * 100) if n else None
        avg_pnl = statistics.mean(pnls) if pnls else None
        t = _tstat(pnls) if n >= 2 else None
        print(f"{strategy:<20}{n:>16}{(f'{win_rate:.1f}%' if win_rate is not None else 'n/a'):>12}"
              f"{_fmt_pct(avg_pnl):>14}{_fmt_t(t):>10}")
        result["by_strategy"][strategy] = {
            "trades_fechados": n, "win_rate_pct": win_rate,
            "pnl_medio_pct": avg_pnl, "t_stat": t,
            "entradas_registradas": open_count.get(strategy, 0),
        }
    return result


def section_real_pipeline(session) -> dict:
    print("\n" + "=" * 78)
    print("2) PIPELINE REAL DOS 7 AGENTES (trades/positions, is_paper=true)")
    print("=" * 78)

    trades = session.execute(text(
        "select pair, side, quantity, price, reason, timestamp "
        "from trades where is_paper = true order by timestamp"
    )).mappings().all()

    positions = session.execute(text(
        "select pair, quantity, avg_entry_price, stop_price, take_price, status, "
        "opened_at, closed_at from positions where is_paper = true order by opened_at"
    )).mappings().all()

    if not trades:
        print("Nenhum trade simulado (is_paper=true) do pipeline real ainda.")
        return {"count": 0}

    print(f"\nTotal de trades simulados: {len(trades)}  |  Posições simuladas: {len(positions)}")

    # Calcula pnl% por posição fechada, casando buy (open_position) com sell (sell_position)
    closed_pnls = []
    open_now = 0
    for p in positions:
        if p["status"] == "closed":
            # acha o trade de venda correspondente pelo par mais próximo em tempo
            sells = [t for t in trades if t["pair"] == p["pair"] and t["side"] == "sell"
                     and t["timestamp"] >= (p["opened_at"] or t["timestamp"])]
            if sells and p["avg_entry_price"]:
                exit_price = float(sells[0]["price"])
                pnl_pct = (exit_price - float(p["avg_entry_price"])) / float(p["avg_entry_price"]) * 100
                closed_pnls.append(pnl_pct)
        else:
            open_now += 1

    print(f"Posições fechadas com PnL calculável: {len(closed_pnls)}  |  Ainda abertas: {open_now}")
    if closed_pnls:
        wins = sum(1 for p in closed_pnls if p > 0)
        print(f"Win rate: {wins/len(closed_pnls)*100:.1f}%  |  PnL médio: {_fmt_pct(statistics.mean(closed_pnls))}"
              f"  |  t-stat: {_fmt_t(_tstat(closed_pnls))}")
        print("\n⚠ Amostra pequena e recente (só execuções manuais de hoje via run_cycle_once.py) --")
        print("  não trate esse número como validação estatística, só como primeiro sinal de vida.")

    return {
        "trades_total": len(trades),
        "posicoes_total": len(positions),
        "posicoes_abertas_agora": open_now,
        "posicoes_fechadas_com_pnl": len(closed_pnls),
        "win_rate_pct": (sum(1 for p in closed_pnls if p > 0) / len(closed_pnls) * 100) if closed_pnls else None,
        "pnl_medio_pct": statistics.mean(closed_pnls) if closed_pnls else None,
        "t_stat": _tstat(closed_pnls) if len(closed_pnls) >= 2 else None,
    }


def section_position_reviews(session) -> dict:
    print("\n" + "=" * 78)
    print("3) POSITIONREVIEWAGENT (position_reviews) -- vereditos hold/sell")
    print("=" * 78)

    rows = session.execute(text(
        "select asset, decision, confidence, acted, is_paper, price_at_review, timestamp "
        "from position_reviews order by timestamp"
    )).mappings().all()

    if not rows:
        print("Nenhum registro em position_reviews ainda.")
        return {"count": 0}

    total = len(rows)
    holds = sum(1 for r in rows if r["decision"] == "hold")
    sells = sum(1 for r in rows if r["decision"] == "sell")
    acted = sum(1 for r in rows if r["acted"])
    avg_conf = statistics.mean(float(r["confidence"]) for r in rows)

    print(f"\nTotal de vereditos: {total}  |  hold: {holds}  |  sell: {sells}  |  vendas executadas: {acted}")
    print(f"Confiança média: {avg_conf:.0%}")

    # Leitura aproximada de "o veredito parece ter sido bom até agora" --
    # só pra vereditos com price_at_review preenchido (ver arquitetura-
    # tecnica.md 9.10). Compara com o preço ATUAL (não o preço no momento
    # exato em que faria sentido reavaliar) -- é uma aproximação, não uma
    # medida definitiva.
    with_price = [r for r in rows if r["price_at_review"] is not None]
    outcome = {"avaliaveis": len(with_price), "hold_bom": None, "sell_bom": None}
    if with_price:
        current_price_cache: dict[str, float | None] = {}
        hold_outcomes, sell_outcomes = [], []
        for r in with_price:
            pair = f"{r['asset']}{settings.safety_stablecoin}"
            if pair not in current_price_cache:
                try:
                    current_price_cache[pair] = binance_client.get_last_price(pair)
                except Exception:
                    current_price_cache[pair] = None
            current = current_price_cache[pair]
            if current is None:
                continue
            change_pct = (current - r["price_at_review"]) / r["price_at_review"] * 100
            if r["decision"] == "hold":
                hold_outcomes.append(change_pct)
            else:
                sell_outcomes.append(change_pct)

        if hold_outcomes:
            good_holds = sum(1 for c in hold_outcomes if c > 0)
            outcome["hold_bom"] = good_holds / len(hold_outcomes) * 100
            print(f"\n'hold' com preço rastreável: {len(hold_outcomes)}  |  "
                  f"preço subiu desde então em {outcome['hold_bom']:.0f}% deles "
                  f"(variação média: {_fmt_pct(statistics.mean(hold_outcomes))})")
        if sell_outcomes:
            good_sells = sum(1 for c in sell_outcomes if c < 0)
            outcome["sell_bom"] = good_sells / len(sell_outcomes) * 100
            print(f"'sell' com preço rastreável: {len(sell_outcomes)}  |  "
                  f"preço caiu desde então em {outcome['sell_bom']:.0f}% deles "
                  f"(variação média: {_fmt_pct(statistics.mean(sell_outcomes))})")
        print("\n⚠ Isso é uma aproximação (compara com o preço ATUAL, não necessariamente o")
        print("  momento certo de reavaliar) -- útil como tendência ao longo do tempo, não")
        print("  como veredito definitivo de acerto/erro por avaliação individual.")
    else:
        print("\n⚠ Nenhum veredito com price_at_review ainda (só os daqui pra frente terão --")
        print("  ver migrate_add_position_review_price.py). Rode de novo daqui a alguns dias.")

    return {"total": total, "holds": holds, "sells": sells, "vendas_executadas": acted,
            "confianca_media": avg_conf, "outcome_aproximado": outcome}


def main() -> None:
    as_json = "--json" in sys.argv
    print("=" * 78)
    print("  RELATÓRIO DE PERFORMANCE -- DADOS DE DRY-RUN (IvanVestAI)")
    print("=" * 78)

    with get_session() as session:
        r1 = section_paper_trades(session)
        r2 = section_real_pipeline(session)
        r3 = section_position_reviews(session)

    print("\n" + "=" * 78)
    print("Fim do relatório.")
    print("=" * 78)

    if as_json:
        print("\n--- JSON (cole de volta se for pedir análise) ---")
        print(json.dumps({"paper_trades": r1, "pipeline_real": r2, "position_reviews": r3},
                          indent=2, default=str))


if __name__ == "__main__":
    main()

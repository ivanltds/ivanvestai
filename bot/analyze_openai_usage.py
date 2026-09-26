"""Diagnóstico só-leitura (nenhum UPDATE/DELETE/INSERT): avalia o consumo real
de API da OpenAI a partir de `api_cost_log` (gravada em toda chamada por
`core/llm_client.call_structured`, ver arquitetura-tecnica.md seção 4).

Mostra: custo total, custo/tokens por agente, por modelo (barato vs robusto),
por dia (com projeção simples de custo mensal com base na média diária), e
o preço por 1M tokens atualmente hardcoded em `core/llm_client._PRICING`
(vale conferir se ainda bate com o pricing vigente da OpenAI).

Rodar de dentro de bot/: `python analyze_openai_usage.py`
"""
from __future__ import annotations

import datetime as dt

from db.session import get_session
from sqlalchemy import text


def usd(v: float) -> str:
    return f"US$ {v:,.4f}"


def main() -> None:
    with get_session() as session:
        total = session.execute(
            text(
                "SELECT count(*) AS n, coalesce(sum(input_tokens),0) AS in_tok, "
                "coalesce(sum(output_tokens),0) AS out_tok, coalesce(sum(estimated_cost_usd),0) AS cost, "
                "min(timestamp) AS first_ts, max(timestamp) AS last_ts FROM api_cost_log"
            )
        ).fetchone()

        print("=" * 78)
        print("RESUMO GERAL (api_cost_log)")
        print("=" * 78)
        if not total or total.n == 0:
            print("Nenhuma chamada registrada ainda em api_cost_log.")
            return

        print(f"Total de chamadas: {total.n}")
        print(f"Tokens de entrada: {total.in_tok:,} | Tokens de saída: {total.out_tok:,}")
        print(f"Custo total estimado: {usd(total.cost)}")
        print(f"Período coberto: {total.first_ts} até {total.last_ts}")

        days_span = max((total.last_ts - total.first_ts).total_seconds() / 86400.0, 1e-9)
        print(f"Custo médio por dia (no período coberto): {usd(total.cost / days_span)}")
        print(f"Projeção simples pra 30 dias (extrapolando a média acima): {usd(total.cost / days_span * 30)}")

        print()
        print("=" * 78)
        print("POR AGENTE")
        print("=" * 78)
        rows = session.execute(
            text(
                "SELECT agent_name, count(*) AS n, sum(input_tokens) AS in_tok, "
                "sum(output_tokens) AS out_tok, sum(estimated_cost_usd) AS cost "
                "FROM api_cost_log GROUP BY agent_name ORDER BY cost DESC"
            )
        ).fetchall()
        for r in rows:
            print(
                f"{r.agent_name:28s} n={r.n:5d}  in={r.in_tok:>9,}  out={r.out_tok:>9,}  "
                f"custo={usd(r.cost):>14s}  ({r.cost/total.cost*100:.1f}% do total)"
            )

        print()
        print("=" * 78)
        print("POR MODELO")
        print("=" * 78)
        rows = session.execute(
            text(
                "SELECT model, count(*) AS n, sum(input_tokens) AS in_tok, "
                "sum(output_tokens) AS out_tok, sum(estimated_cost_usd) AS cost, "
                "avg(estimated_cost_usd) AS avg_cost "
                "FROM api_cost_log GROUP BY model ORDER BY cost DESC"
            )
        ).fetchall()
        for r in rows:
            print(
                f"{r.model:20s} n={r.n:5d}  in={r.in_tok:>9,}  out={r.out_tok:>9,}  "
                f"custo={usd(r.cost):>14s}  media/chamada={usd(r.avg_cost)}"
            )

        print()
        print("=" * 78)
        print("POR DIA")
        print("=" * 78)
        rows = session.execute(
            text(
                "SELECT date(timestamp AT TIME ZONE 'America/Sao_Paulo') AS dia, count(*) AS n, "
                "sum(input_tokens) AS in_tok, sum(output_tokens) AS out_tok, sum(estimated_cost_usd) AS cost "
                "FROM api_cost_log GROUP BY dia ORDER BY dia"
            )
        ).fetchall()
        for r in rows:
            print(f"{r.dia} | n={r.n:5d} | in={r.in_tok:>9,} | out={r.out_tok:>9,} | custo={usd(r.cost)}")

        print()
        print("=" * 78)
        print("POR AGENTE x DIA (só os 3 dias mais recentes, pra ver a tendência)")
        print("=" * 78)
        recent_days = [r.dia for r in rows[-3:]]
        if recent_days:
            rows2 = session.execute(
                text(
                    "SELECT date(timestamp AT TIME ZONE 'America/Sao_Paulo') AS dia, agent_name, "
                    "count(*) AS n, sum(estimated_cost_usd) AS cost FROM api_cost_log "
                    "WHERE date(timestamp AT TIME ZONE 'America/Sao_Paulo') = ANY(:dias) "
                    "GROUP BY dia, agent_name ORDER BY dia, cost DESC"
                ),
                {"dias": recent_days},
            ).fetchall()
            for r in rows2:
                print(f"{r.dia} | {r.agent_name:28s} n={r.n:4d}  custo={usd(r.cost)}")

        print()
        print("=" * 78)
        print("PRICING ATUALMENTE HARDCODED EM core/llm_client._PRICING (conferir se ainda bate)")
        print("=" * 78)
        from core.llm_client import _PRICING

        for model, prices in _PRICING.items():
            print(f"{model}: US$ {prices['input']}/1M tokens de entrada, US$ {prices['output']}/1M de saída")


if __name__ == "__main__":
    main()

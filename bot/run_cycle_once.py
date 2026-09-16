"""Roda UM ciclo completo do comitê real (orchestrator.cycle_runner.run_cycle)
-- os 7 agentes de verdade, Postgres, Redis, tudo -- fora do scheduler de
15 min do main.py, pra testar a orquestração ponta a ponta antes de cogitar
deixar o bot rodando sozinho.

Isso é DIFERENTE do run_paper_trading.py: aquele script reimplementa a
lógica técnica isolada (sem LLM, sem passar pelos agentes/orquestrador de
verdade). Este aqui é o caminho de PRODUÇÃO completo (NewsAgent,
PortfolioAgent, MarketScannerAgent, ViabilityAgent,
PortfolioComparisonAgent, RiskCommitteeAgent, ExecutionAgent) rodando ponta
a ponta pela primeira vez nesta sessão -- só que sem o dinheiro.

SEGURANÇA: controlado por settings.dry_run (config/settings.py), default
True. Com dry_run=True (o padrão -- não mude isso sem ter certeza absoluta),
o ExecutionAgent NUNCA chama a Binance pra abrir/fechar ordem de verdade;
ele simula o fill pelo preço atual (ticker) e marca a Position/Trade
resultante com is_paper=True (ver migrate_add_is_paper.py -- rode ele antes
de usar este script, uma vez só). O dashboard (/dashboard) já foi ajustado
pra filtrar is_paper=false, então esses registros de teste não aparecem lá
misturados com dado real.

Isso GASTA um pouco de crédito de API da OpenAI de verdade (NewsAgent usa o
modelo barato; ViabilityAgent e RiskCommitteeAgent usam o modelo robusto,
um por oportunidade aprovada pelo scanner) -- normalmente poucos centavos
por ciclo, registrado em api_cost_log.

`run_cycle()` só executa se bot_status == "running" (checagem de segurança
do próprio cycle_runner) -- este script liga isso temporariamente antes de
rodar e devolve pra "paused" depois, inclusive se der erro no meio.

Uso:
    python run_cycle_once.py
"""
from __future__ import annotations

import asyncio
import logging

from config.settings import settings
from core import vlog
from db.models import Setting
from db.session import get_session
from orchestrator.cycle_runner import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SCRIPT_VERSION = "2026-09-16-v2-vlog"


def _set_bot_status(value: str) -> None:
    with get_session() as session:
        row = session.get(Setting, "bot_status")
        if row:
            row.value = value
        else:
            session.add(Setting(key="bot_status", value=value))


def main() -> None:
    print(f"[run_cycle_once.py versão: {SCRIPT_VERSION}]")
    if settings.dry_run:
        vlog.ok("dry_run = True -- seguro, nenhuma ordem real será enviada à Binance.")
    else:
        vlog.fail("ATENÇÃO: settings.dry_run = False -- ordens REAIS podem ser enviadas à Binance !!!")
        confirm = input("Digite 'CONFIRMO' pra continuar mesmo assim, ou qualquer outra coisa pra cancelar: ")
        if confirm.strip() != "CONFIRMO":
            print("Cancelado.")
            return

    _set_bot_status("running")
    try:
        asyncio.run(run_cycle())
        vlog.ok("Ciclo concluído sem exceções.")
        print("  Confira no Postgres (ou no dashboard, com uma query manual filtrando is_paper=true):")
        print("  opportunities, committee_decisions, positions/trades (is_paper=true).")
    finally:
        _set_bot_status("paused")


if __name__ == "__main__":
    main()

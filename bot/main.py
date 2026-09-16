"""Entrypoint do bot: sobe o APScheduler rodando o ciclo do comitê a
cada N minutos (config.settings.cycle_interval_minutes, sobrescrito
por config_store se o dashboard mudar o valor) e consome comandos
enfileirados pelo dashboard (pausar/retomar, forçar venda, etc.) entre
os ciclos.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core import redis_bridge
from core.config_store import load_runtime_config
from orchestrator.cycle_runner import run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ivanvestai.main")


async def process_pending_commands() -> None:
    """Roda com mais frequência que o ciclo principal, pra ações urgentes
    do dashboard (ex: kill switch) não esperarem até 15 min."""
    from db.models import Position
    from db.session import get_session

    commands = redis_bridge.drain_commands()
    if not commands:
        return

    for cmd in commands:
        logger.info("Processando comando do dashboard: %s", cmd)
        name = cmd.get("command")
        payload = cmd.get("payload", {})

        with get_session() as session:
            if name == "pause_bot":
                _upsert_setting(session, "bot_status", "paused")
            elif name == "resume_bot":
                _upsert_setting(session, "bot_status", "running")
            elif name == "force_sell":
                position = session.get(Position, payload.get("position_id"))
                if position:
                    position.sell_flag = "immediate"
            elif name == "set_sell_flag":
                position = session.get(Position, payload.get("position_id"))
                if position:
                    position.sell_flag = payload.get("mode", "immediate")


def _upsert_setting(session, key: str, value: str) -> None:
    from db.models import Setting

    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def main() -> None:
    config = load_runtime_config()
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

    # `next_run_time=None` no APScheduler NÃO dispara já na largada -- na
    # verdade faz o oposto: adiciona o job PAUSADO (só roda depois de um
    # scheduler.resume_job() explícito, que este código nunca chamava). Bug
    # achado em 16/09/2026 rodando o main.py pela primeira vez: o processo
    # ficava de pé, consumindo comandos normalmente, mas o run_cycle nunca
    # disparava sozinho -- nem na largada, nem no intervalo. Corrigido
    # passando o "agora" de verdade, que é o comportamento que o comentário
    # original já descrevia (dispara já, depois a cada N min).
    scheduler.add_job(run_cycle, "interval", minutes=config.cycle_interval_minutes, id="committee_cycle",
                       next_run_time=dt.datetime.now(dt.timezone.utc))
    scheduler.add_job(process_pending_commands, "interval", seconds=15, id="command_drain")

    scheduler.start()
    logger.info(
        "IvanVestAI bot iniciado. Ciclo a cada %s min. bot_status atual: %s",
        config.cycle_interval_minutes, config.bot_status,
    )

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Encerrando bot...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()

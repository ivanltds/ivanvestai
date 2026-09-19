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
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.settings import settings
from core import redis_bridge
from core.config_store import load_runtime_config
from core.logging_setup import configure_logging
from orchestrator.cycle_runner import monitor_open_positions, run_cycle

configure_logging()  # console + arquivo diário (bot/logs) + tabela bot_logs no Postgres
logger = logging.getLogger("ivanvestai.main")

CYCLE_JOB_ID = "committee_cycle"

# Comandos que o bot sabe executar -- o dashboard (web/app/api/commands/route.ts)
# só aceita estes mesmos. `open_manual_position` e `adjust_stop_take` eram
# aceitos pela API mas ignorados aqui em silêncio; foram removidos dos dois lados.
SUPPORTED_COMMANDS = {"pause_bot", "resume_bot", "force_sell", "set_sell_flag"}
VALID_SELL_FLAGS = {"none", "immediate", "optimized"}


def _process_pending_commands_sync() -> None:
    from db.models import Position
    from db.session import get_session

    commands = redis_bridge.drain_commands()
    if not commands:
        return

    for cmd in commands:
        logger.info("Processando comando do dashboard: %s", cmd)
        name = cmd.get("command")
        payload = cmd.get("payload") or {}

        # Cada comando isolado: um que falha não pode descartar os demais (todos
        # já foram retirados da fila do Redis por drain_commands).
        try:
            if name not in SUPPORTED_COMMANDS:
                logger.warning("Comando desconhecido/não suportado ignorado: %s", name)
                continue

            with get_session() as session:
                if name == "pause_bot":
                    _upsert_setting(session, "bot_status", "paused")
                elif name == "resume_bot":
                    _upsert_setting(session, "bot_status", "running")
                elif name in ("force_sell", "set_sell_flag"):
                    try:
                        position_id = uuid.UUID(str(payload.get("position_id")))
                    except ValueError:
                        logger.warning("Comando %s com position_id inválido: %s", name, payload)
                        continue
                    position = session.get(Position, position_id)
                    if position is None:
                        logger.warning("Comando %s: posição %s não encontrada.", name, position_id)
                        continue
                    mode = "immediate" if name == "force_sell" else payload.get("mode", "immediate")
                    if mode not in VALID_SELL_FLAGS:
                        logger.warning("Comando set_sell_flag com mode inválido: %s", mode)
                        continue
                    position.sell_flag = mode
        except Exception:
            logger.exception("Falha ao processar comando do dashboard: %s", cmd)


async def process_pending_commands() -> None:
    """Roda com mais frequência que o ciclo principal, pra ações urgentes
    do dashboard (ex: kill switch) não esperarem até 15 min. Redis/DB são
    síncronos -- rodam em thread pra não bloquear o event loop do scheduler."""
    await asyncio.to_thread(_process_pending_commands_sync)


def _upsert_setting(session, key: str, value: str) -> None:
    from db.models import Setting

    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


async def sync_cycle_interval(scheduler: AsyncIOScheduler) -> None:
    """Reagenda o ciclo se o intervalo mudou no dashboard -- antes o valor só
    era lido uma vez, na partida do processo."""
    try:
        config = await asyncio.to_thread(load_runtime_config)
    except Exception:
        logger.exception("Falha ao ler configuração pra sincronizar o intervalo do ciclo.")
        return

    job = scheduler.get_job(CYCLE_JOB_ID)
    if job is None:
        return
    current_minutes = job.trigger.interval.total_seconds() / 60
    if abs(current_minutes - config.cycle_interval_minutes) > 1e-9:
        logger.info("Intervalo do ciclo mudou (%s -> %s min) — reagendando.", current_minutes, config.cycle_interval_minutes)
        scheduler.reschedule_job(CYCLE_JOB_ID, trigger="interval", minutes=config.cycle_interval_minutes)


async def amain() -> None:
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
    # misfire_grace_time/coalesce: o event loop fica ocupado durante o ciclo e o
    # padrão do APScheduler (1s de tolerância) descartava rodadas dos jobs curtos
    # ("Run time of job ... was missed by ...", visto nos logs de 18/09/2026).
    scheduler.add_job(run_cycle, "interval", minutes=config.cycle_interval_minutes, id=CYCLE_JOB_ID,
                       next_run_time=dt.datetime.now(dt.timezone.utc), misfire_grace_time=300, coalesce=True)
    scheduler.add_job(process_pending_commands, "interval", seconds=15, id="command_drain",
                       misfire_grace_time=30, coalesce=True)
    scheduler.add_job(sync_cycle_interval, "interval", seconds=60, id="interval_sync", args=[scheduler],
                       misfire_grace_time=60, coalesce=True)
    # Monitor rápido: stop/take/trailing entre os ciclos de 15 min (0 desliga).
    if settings.risk_monitor_seconds > 0:
        scheduler.add_job(monitor_open_positions, "interval", seconds=settings.risk_monitor_seconds,
                           id="position_monitor", misfire_grace_time=30, coalesce=True, max_instances=1)

    scheduler.start()
    logger.info(
        "IvanVestAI bot iniciado. Ciclo a cada %s min, monitor de stop/take a cada %ss. bot_status atual: %s. dry_run=%s",
        config.cycle_interval_minutes, settings.risk_monitor_seconds, config.bot_status, settings.dry_run,
    )

    try:
        await asyncio.Event().wait()  # roda até KeyboardInterrupt/cancelamento
    finally:
        logger.info("Encerrando bot...")
        scheduler.shutdown()


def main() -> None:
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()

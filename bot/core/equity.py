"""Variação de patrimônio SEM aportes/retiradas -- base do circuit breaker.

Achado em 19/09/2026: o circuit breaker comparava o patrimônio total do dia com o
do início do dia e alertou "-28%" quando, na verdade, ~US$26,6 em NEAR tinham sido
movidos pra fora da conta manualmente (sem trade nenhum no banco). Aportes e
retiradas não são perda de trading.

A medida usada aqui é o P&L de MERCADO: entre dois snapshots consecutivos da
carteira, só conta a variação de PREÇO sobre a quantidade que já estava na carteira
no snapshot anterior. Quantidade que entra/sai (compra, venda, depósito, retirada)
não gera P&L por si só.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from zoneinfo import ZoneInfo


def group_batches(rows: Iterable[tuple], gap_seconds: float = 60.0) -> list[dict]:
    """Agrupa linhas (timestamp, asset, quantity, value_usdt), em ordem de tempo, em
    "lotes": um lote = o snapshot de um ciclo (linhas gravadas em poucos segundos)."""
    batches: list[dict] = []
    current: dict | None = None
    for ts, asset, quantity, value in rows:
        if current is None or (ts - current["t0"]).total_seconds() > gap_seconds:
            current = {"t0": ts, "assets": {}}
            batches.append(current)
        current["assets"][asset] = (quantity, value)
    return batches


def market_pnl(batches: list[dict]) -> float:
    """Soma, entre lotes consecutivos, de quantidade_anterior x (preço_novo - preço_anterior)."""
    total = 0.0
    for prev, nxt in zip(batches, batches[1:]):
        for asset, (q0, v0) in prev["assets"].items():
            if asset not in nxt["assets"] or q0 <= 0 or v0 <= 0:
                continue
            q1, v1 = nxt["assets"][asset]
            if q1 <= 0 or v1 <= 0:
                continue
            total += q0 * (v1 / q1 - v0 / q0)
    return total


def daily_market_pnl_usdt(day: dt.date, account_id: "uuid.UUID | None" = None) -> float:
    """P&L de mercado acumulado no dia local `day` (a partir dos snapshots do banco).

    `account_id` (multi-conta-plano.md, Fase C): achado em 25/09/2026 -- com 2+
    contas ativas escrevendo WalletSnapshot no mesmo ciclo (uma por conta,
    aproximadamente ao mesmo tempo), a query original sem filtro de conta
    misturava snapshots de contas DIFERENTES no mesmo "lote" (group_batches
    agrupa só por proximidade de horário) e calculava o P&L de mercado
    comparando ativos de contas diferentes como se fossem a mesma carteira --
    um número sem sentido, usado direto pelo circuit breaker. Passar
    account_id filtra a query pra só considerar os snapshots DESSA conta;
    omitido (None), mantém o comportamento antigo (todas as contas juntas) --
    só usado hoje por chamadores legados/scripts manuais de uma conta só."""
    from sqlalchemy import select

    from config.settings import settings
    from db.models import WalletSnapshot
    from db.session import get_session

    # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 5): antes usava
    # `.astimezone()` sem argumento, que assume o fuso do SISTEMA OPERACIONAL da
    # máquina, não settings.timezone (o valor explícito que main.py usa pro
    # AsyncIOScheduler desde o item 6 da Fase 5, 9.25). Se o fuso do Windows do
    # Ivan alguma vez divergir de settings.timezone, o corte de "dia" usado aqui
    # (e por date.today() em DailyEquity) desalinhava silenciosamente do fuso que
    # o resto do bot assume. Agora usa settings.timezone explicitamente.
    start = dt.datetime.combine(day, dt.time.min, tzinfo=ZoneInfo(settings.timezone))  # meia-noite local
    since = start - dt.timedelta(minutes=30)  # o último lote de ontem serve de base pro 1º de hoje
    with get_session() as session:
        query = (
            select(
                WalletSnapshot.timestamp, WalletSnapshot.asset, WalletSnapshot.quantity, WalletSnapshot.value_usdt
            )
            .where(WalletSnapshot.timestamp >= since)
        )
        if account_id is not None:
            query = query.where(WalletSnapshot.account_id == account_id)
        rows = session.execute(query.order_by(WalletSnapshot.timestamp)).all()
    return market_pnl(group_batches([tuple(r) for r in rows]))

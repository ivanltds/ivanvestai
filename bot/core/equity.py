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


def daily_market_pnl_usdt(day: dt.date) -> float:
    """P&L de mercado acumulado no dia local `day` (a partir dos snapshots do banco)."""
    from sqlalchemy import select

    from db.models import WalletSnapshot
    from db.session import get_session

    start = dt.datetime.combine(day, dt.time.min).astimezone()  # meia-noite local
    since = start - dt.timedelta(minutes=30)  # o último lote de ontem serve de base pro 1º de hoje
    with get_session() as session:
        rows = session.execute(
            select(
                WalletSnapshot.timestamp, WalletSnapshot.asset, WalletSnapshot.quantity, WalletSnapshot.value_usdt
            )
            .where(WalletSnapshot.timestamp >= since)
            .order_by(WalletSnapshot.timestamp)
        ).all()
    return market_pnl(group_batches([tuple(r) for r in rows]))

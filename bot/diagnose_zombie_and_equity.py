"""Diagnóstico só-leitura (nenhum UPDATE/DELETE/INSERT) pra investigar:
1. A posição zumbi ZECUSDT (-2010 insufficient balance recorrente).
2. Todas as posições abertas no momento (paper vs real).
3. Histórico de daily_equity pra reconstruir a queda de ~US$86,83 -> ~US$60.
4. Trades reais (is_paper=false) dos últimos dias, pra ver o que realmente
   mexeu capital de verdade.

Rodar de dentro de bot/: `python diagnose_zombie_and_equity.py`
"""
from __future__ import annotations

import datetime as dt
import sys

from config.settings import settings
from db.session import get_session
from db.models import Position
from sqlalchemy import text

# ID da posição suspeita passado por argumento: `python diagnose_zombie_and_equity.py <position_id>`
ZOMBIE_ID = sys.argv[1] if len(sys.argv) > 1 else None


def main() -> None:
    with get_session() as session:
        print("=" * 70)
        print("1) POSIÇÃO ZUMBI ZECUSDT")
        print("=" * 70)
        pos = session.get(Position, ZOMBIE_ID) if ZOMBIE_ID else None
        if pos is None:
            print(f"Não encontrada com id={ZOMBIE_ID} (pode ter sido fechada/removida).")
        else:
            print(f"pair={pos.pair} status={pos.status} is_paper={pos.is_paper}")
            print(f"quantity={pos.quantity} avg_entry_price={pos.avg_entry_price}")
            print(f"stop_price={pos.stop_price} take_price={pos.take_price}")
            print(f"opened_at={pos.opened_at} closed_at={pos.closed_at}")

        print()
        print("=" * 70)
        print("2) TODAS AS POSIÇÕES ABERTAS (status='open')")
        print("=" * 70)
        rows = session.execute(
            text(
                "SELECT id, pair, is_paper, quantity, avg_entry_price, opened_at "
                "FROM positions WHERE status='open' ORDER BY opened_at"
            )
        ).fetchall()
        for r in rows:
            print(dict(r._mapping))

        print()
        print("=" * 70)
        print("3) DAILY_EQUITY (últimos registros)")
        print("=" * 70)
        try:
            rows = session.execute(
                text("SELECT * FROM daily_equity ORDER BY 1 DESC LIMIT 15")
            ).fetchall()
            for r in rows:
                print(dict(r._mapping))
        except Exception as e:
            print(f"Erro lendo daily_equity: {e!r}")

        print()
        print("=" * 70)
        print("4) TRADES REAIS (is_paper=false) DESDE 16/09")
        print("=" * 70)
        rows = session.execute(
            text(
                "SELECT id, position_id, pair, side, order_type, quantity, price, "
                "reason, timestamp FROM trades WHERE is_paper=false AND timestamp >= :since "
                "ORDER BY timestamp"
            ),
            {"since": dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc)},
        ).fetchall()
        for r in rows:
            print(dict(r._mapping))

        print()
        print("=" * 70)
        print("5) SALDO ATUAL NA BINANCE (leitura, sem ordens)")
        print("=" * 70)
        try:
            from core.binance_client import binance_client

            balances = binance_client.get_account_balances()
            for b in balances:
                print(b)
        except Exception as e:
            print(f"Erro lendo saldo Binance: {e!r}")

    print()
    print(f"dry_run atual (settings): {settings.dry_run}")


if __name__ == "__main__":
    main()

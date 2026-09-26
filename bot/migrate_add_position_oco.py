"""Migração pontual (MVP ainda sem Alembic): adiciona `oco_order_list_id` em `positions`.

Guarda o id da lista OCO (stop + take) que o bot cria NA BINANCE pra cada posição
real -- assim a saída não depende do bot estar de pé nem de checagem a cada N segundos.
NULL = posição sem proteção na exchange (paper, trailing, ou falha ao criar a ordem;
nesses casos o stop/take por software continua valendo).

Idempotente (IF NOT EXISTS). Uso: python migrate_add_position_oco.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS oco_order_list_id BIGINT"))
    print("Coluna oco_order_list_id garantida em positions (idempotente).")


if __name__ == "__main__":
    main()

"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
adiciona índices em positions.status, committee_decisions.opportunity_id e
trades.position_id.

Por quê (achado 21/09/2026, arquitetura-tecnica.md 9.20 item 24, corrigido
24/09/2026 na Fase 4 do plano da seção 9.21): `positions.status` é filtrado a
cada ciclo de 15 min, a cada 60s no monitor rápido, e no PositionReviewAgent
(`filter_by(status="open")`) -- é a query mais frequente do sistema e não
tinha índice, virando full table scan crescente conforme `positions` acumula
histórico. `committee_decisions.opportunity_id` e `trades.position_id` (FKs
usadas pra reconstruir histórico no dashboard) também sem índice.

`Base.metadata.create_all()` só cria tabelas que ainda não existem -- não
adiciona índice em tabela já criada, por isso este script separado.

Idempotente (IF NOT EXISTS) -- seguro rodar mais de uma vez.

Uso: python migrate_add_indexes.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_positions_status ON positions (status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_committee_decisions_opportunity_id "
            "ON committee_decisions (opportunity_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_trades_position_id ON trades (position_id)"
        ))
    print("Índices garantidos em positions.status, committee_decisions.opportunity_id "
          "e trades.position_id (idempotente).")


if __name__ == "__main__":
    main()

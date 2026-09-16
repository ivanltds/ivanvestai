"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
adiciona a coluna `is_paper` em `positions` e `trades`.

Por quê um script separado em vez de só rodar `db.init_db` de novo:
`Base.metadata.create_all()` só cria tabelas que ainda não existem -- não
altera colunas de tabelas já criadas. `positions` e `trades` já existem no
banco desde o setup inicial (seção 9.1 de arquitetura-tecnica.md), mas
vazias (o bot nunca operou de verdade), então é seguro adicionar a coluna
sem risco de perder dado nenhum.

Idempotente (IF NOT EXISTS) -- seguro rodar mais de uma vez.

Uso: python migrate_add_is_paper.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS is_paper BOOLEAN NOT NULL DEFAULT false"))
        conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS is_paper BOOLEAN NOT NULL DEFAULT false"))
    print("Coluna is_paper garantida em positions e trades (idempotente).")


if __name__ == "__main__":
    main()

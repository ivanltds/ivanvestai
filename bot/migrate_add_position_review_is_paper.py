"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
adiciona a coluna `is_paper` em `position_reviews`.

Por quê um script separado em vez de só rodar `db.init_db` de novo:
`Base.metadata.create_all()` só cria tabelas que ainda não existem -- não
altera colunas de tabelas já criadas. `position_reviews` já foi criada (seção
9.8 de arquitetura-tecnica.md) e já tem linhas reais do primeiro dry-run do
PositionReviewAgent -- mas sem saber se cada venda recomendada foi simulada
(dry-run) ou real, o que o dashboard precisa pra não mostrar "IA vendeu esta
posição" de um jeito enganoso quando foi só simulado.

Idempotente (IF NOT EXISTS) -- seguro rodar mais de uma vez.

Uso: python migrate_add_position_review_is_paper.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE position_reviews ADD COLUMN IF NOT EXISTS is_paper BOOLEAN NOT NULL DEFAULT false"
        ))
    print("Coluna is_paper garantida em position_reviews (idempotente).")


if __name__ == "__main__":
    main()

"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
adiciona a coluna `price_at_review` em `position_reviews`.

Guarda o preço unitário do ativo no momento de cada veredito do
PositionReviewAgent (hold/sell) -- sem isso não tinha como, mais adiante,
comparar com o preço futuro do ativo e medir se o veredito teria sido
acertado. Ver arquitetura-tecnica.md 9.10 (achado: "não dá pra medir
'acerto' desse agente ainda").

Idempotente (IF NOT EXISTS) -- seguro rodar mais de uma vez. Linhas já
existentes ficam com `price_at_review = NULL` (não dá pra reconstruir
retroativamente o preço exato daquele momento) -- só os vereditos daqui
pra frente vão ter esse dado.

Uso: python migrate_add_position_review_price.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE position_reviews ADD COLUMN IF NOT EXISTS price_at_review DOUBLE PRECISION"
        ))
    print("Coluna price_at_review garantida em position_reviews (idempotente).")


if __name__ == "__main__":
    main()

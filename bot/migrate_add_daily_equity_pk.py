"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
troca a PK de `daily_equity` de `date` sozinho pra um `id` substituto (UUID),
com um índice único em (date, account_id) -- ver a nova docstring da classe
DailyEquity em db/models.py.

Por quê (multi-conta-plano.md, Fase C): com `date` como PK única só cabia UMA
linha por dia -- suficiente enquanto só existia uma conta. Com 2+ contas
ativas ao mesmo tempo (CONTA IVAN e CONTA MICAEL, ambas operando com capital
real a partir de 25/09/2026), cada uma precisa do seu próprio "início de
dia" no mesmo calendário -- `_check_circuit_breaker` (orchestrator/
cycle_runner.py) passa a consultar por (date, account_id) em vez de só
`date`.

Passos (idempotente -- seguro rodar mais de uma vez):
  1. Adiciona a coluna `id` (UUID), se ainda não existir.
  2. Preenche `id` (gerado em Python -- não depende da extensão pgcrypto do
     Postgres) em toda linha histórica que ainda estiver com `id` NULL.
  3. Remove a PK antiga (em `date`), SE ainda for a PK atual -- o nome da
     constraint é buscado dinamicamente via information_schema (nunca
     hardcoded) e o passo só age se a PK encontrada for sobre a coluna
     `date` (evita derrubar a PK nova numa segunda execução).
  4. Define `id` como NOT NULL e, se a tabela ainda não tiver PK nenhuma,
     cria a nova PK em `id`.
  5. Cria o índice único em (date, account_id), se ainda não existir.

Requer que migrate_add_accounts.py já tenha rodado antes (account_id precisa
existir e estar preenchido nas linhas históricas).

Uso:
    python migrate_add_daily_equity_pk.py
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        # 1. Coluna id (UUID) -- nullable por enquanto (linhas antigas ainda
        #    não têm valor).
        conn.execute(text("ALTER TABLE daily_equity ADD COLUMN IF NOT EXISTS id UUID"))

        # 2. Preenche id em toda linha histórica sem id ainda. Nesse ponto
        #    `date` continua sendo a PK antiga (ou já não existe nenhuma linha
        #    sem id, numa segunda execução) -- então cada `date` é único.
        rows = conn.execute(text("SELECT date FROM daily_equity WHERE id IS NULL")).all()
        for row in rows:
            conn.execute(
                text("UPDATE daily_equity SET id = :id WHERE date = :date AND id IS NULL"),
                {"id": str(uuid.uuid4()), "date": row.date},
            )
        if rows:
            print(f"id preenchido em {len(rows)} linha(s) histórica(s) de daily_equity.")
        else:
            print("Nenhuma linha de daily_equity sem id (nada a preencher).")

        # 3. Remove a PK antiga -- só se ela ainda for sobre `date` (pra não
        #    derrubar a PK nova, em `id`, numa segunda execução deste script).
        pk_on_date = conn.execute(
            text(
                "SELECT tc.constraint_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name "
                "WHERE tc.table_name = 'daily_equity' AND tc.constraint_type = 'PRIMARY KEY' "
                "  AND kcu.column_name = 'date'"
            )
        ).scalar()
        if pk_on_date:
            conn.execute(text(f'ALTER TABLE daily_equity DROP CONSTRAINT "{pk_on_date}"'))
            print(f"PK antiga ({pk_on_date}, sobre `date`) removida.")
        else:
            print("PK antiga sobre `date` não encontrada (já migrado antes, ou tabela nova).")

        # 4. id vira NOT NULL; cria a nova PK só se a tabela ainda não tiver
        #    PK nenhuma (idempotente).
        conn.execute(text("ALTER TABLE daily_equity ALTER COLUMN id SET NOT NULL"))
        has_pk = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'daily_equity' AND constraint_type = 'PRIMARY KEY'"
            )
        ).scalar()
        if not has_pk:
            conn.execute(text("ALTER TABLE daily_equity ADD PRIMARY KEY (id)"))
            print("Nova PK (id) criada.")
        else:
            print("daily_equity já tem PK (id) -- nada a criar.")

        # 5. Índice único em (date, account_id) -- garante no máximo uma linha
        #    de "início de dia" por conta por dia (a mesma garantia que a PK
        #    antiga dava pra uma conta só).
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_equity_date_account "
            "ON daily_equity (date, account_id)"
        ))
        print("Índice único (date, account_id) garantido.")

    print("Migração de daily_equity concluída.")


if __name__ == "__main__":
    main()

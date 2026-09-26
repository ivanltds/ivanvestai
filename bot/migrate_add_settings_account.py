"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
adiciona `account_id` (NULLABLE) na tabela `settings` (multi-conta-plano.md,
Fase B) e etiqueta as linhas existentes.

`key` continua sendo a PK sozinha -- essa coluna ainda não muda o
comportamento de nenhuma leitura/escrita (bot/core/config_store.py e
web/app/api/settings/route.ts não filtram por account_id). É só um rótulo
informativo, preparando terreno pra quando a Fase C tiver uma segunda conta
de verdade pra testar contra e a Fase E tiver o seletor de conta no
dashboard -- aí sim `key` vira chave composta (account_id, key) e as leituras
passam a ser escopadas. Ver o comentário na classe Setting em db/models.py.

`bot_status` é a exceção: fica com account_id = NULL de propósito ("master",
seção 5.3 do plano) -- não é etiquetado com a conta única. Todas as outras
chaves existentes recebem o id da única conta ativa hoje.

Idempotente -- seguro rodar mais de uma vez.

Uso:
    python migrate_add_settings_account.py
"""
from __future__ import annotations

from sqlalchemy import text

from db.models import Account
from db.session import SessionLocal, engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE settings ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_settings_account_id ON settings (account_id)"
        ))
    print("Coluna settings.account_id (NULLABLE) garantida.")

    with SessionLocal() as session:
        account = session.query(Account).order_by(Account.display_order).first()

    if account is None:
        print(
            "AVISO: nenhuma conta encontrada em accounts -- rode migrate_add_accounts.py "
            "primeiro. Coluna criada, mas backfill não feito ainda."
        )
        return

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE settings SET account_id = :aid "
                "WHERE account_id IS NULL AND key <> 'bot_status'"
            ),
            {"aid": str(account.id)},
        )
        print(
            f"Backfill: {result.rowcount} linha(s) de settings etiquetada(s) com a conta "
            f"'{account.label}' (id={account.id}). 'bot_status' foi deixado com account_id "
            "NULL de propósito (master, seção 5.3 do plano)."
        )


if __name__ == "__main__":
    main()

"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
cria a tabela `accounts` (multi-conta-plano.md) e adiciona `account_id`
(NULLABLE) em wallet_snapshots, opportunities, positions, trades,
daily_equity, position_reviews e api_cost_log -- depois migra a conta única
já existente (lida do .env) pra virar a primeira linha de `accounts`, e
preenche `account_id` de todo registro histórico com o id dela.

Por que NULLABLE (não NOT NULL) por enquanto: nenhum agente ainda passa
account_id ao criar um registro novo -- isso só entra na Fase B do plano.
Se a coluna já fosse NOT NULL agora, o próximo INSERT do bot em produção
(ainda rodando o código de hoje, antes da Fase B) quebraria com violação de
constraint. Só vira NOT NULL numa migração futura, depois que a Fase B
estiver validada em produção por várias execuções reais.

Idempotente -- seguro rodar mais de uma vez. Rodar de novo antes da Fase B
até serve como rede de segurança: qualquer registro novo que o bot tiver
criado nesse meio-tempo (ainda sem account_id, porque os agentes não
preenchem isso ainda) é etiquetado pra conta única na próxima passada.

Uso:
    python migrate_add_accounts.py [--label "Principal"]

Requer ACCOUNTS_ENCRYPTION_KEY no .env (gere com `python -m core.crypto`)
antes de rodar pela primeira vez -- sem isso, para com instrução clara.
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from config.settings import settings
from core.crypto import AccountsEncryptionKeyMissing, encrypt_secret
from db.models import Account, Base
from db.session import SessionLocal, engine

_TABLES_WITH_ACCOUNT_ID = (
    "wallet_snapshots",
    "opportunities",
    "positions",
    "trades",
    "daily_equity",
    "position_reviews",
    "api_cost_log",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--label",
        default="Principal",
        help="Nome de exibição da conta única existente (default: 'Principal'). "
        "Só é usado se ainda não houver nenhuma conta cadastrada.",
    )
    args = parser.parse_args()

    # 1. Cria só a tabela accounts (as outras 7 já existem -- não uso
    #    Base.metadata.create_all() cheio pra não depender delas estarem em dia).
    Base.metadata.create_all(bind=engine, tables=[Account.__table__])
    print("Tabela accounts garantida.")

    # 2. Adiciona account_id NULLABLE (+ índice) nas tabelas que já existem.
    with engine.begin() as conn:
        for table in _TABLES_WITH_ACCOUNT_ID:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id)"
            ))
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_account_id ON {table} (account_id)"
            ))
    print(f"Coluna account_id (NULLABLE) garantida em: {', '.join(_TABLES_WITH_ACCOUNT_ID)}.")

    # 3. Garante que existe ao menos uma conta -- migra a conta única do .env
    #    se a tabela accounts ainda estiver vazia.
    with SessionLocal() as session:
        account = session.query(Account).order_by(Account.display_order).first()
        if account is None:
            if not settings.binance_api_key or not settings.binance_api_secret:
                print(
                    "AVISO: binance_api_key/binance_api_secret estão vazios no .env -- "
                    "criando a conta mesmo assim, com credenciais em branco. Rode "
                    "`python manage_accounts.py rotate-key <id>` depois pra corrigir."
                )
            try:
                account = Account(
                    label=args.label,
                    binance_api_key_encrypted=encrypt_secret(settings.binance_api_key),
                    binance_api_secret_encrypted=encrypt_secret(settings.binance_api_secret),
                    is_active=True,
                    dry_run=settings.dry_run,
                    display_order=0,
                )
            except AccountsEncryptionKeyMissing as exc:
                raise SystemExit(str(exc)) from exc
            session.add(account)
            session.commit()
            session.refresh(account)
            print(f"Conta '{account.label}' criada (id={account.id}), migrada do .env atual.")
        else:
            print(f"Conta existente encontrada: '{account.label}' (id={account.id}) -- não criei outra.")

    # 4. Backfill: todo registro histórico (e qualquer registro novo criado
    #    antes da Fase B) sem account_id passa a apontar pra essa conta.
    total = 0
    with engine.begin() as conn:
        for table in _TABLES_WITH_ACCOUNT_ID:
            result = conn.execute(
                text(f"UPDATE {table} SET account_id = :aid WHERE account_id IS NULL"),
                {"aid": str(account.id)},
            )
            if result.rowcount:
                total += result.rowcount
                print(f"  {table}: {result.rowcount} linha(s) etiquetada(s).")
    print(f"Backfill concluído ({total} linha(s) no total). account_id continua NULLABLE -- "
          "vira NOT NULL numa migração futura, depois da Fase B validada.")


if __name__ == "__main__":
    main()

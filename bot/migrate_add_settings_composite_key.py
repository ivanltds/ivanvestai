"""Migração pontual (MVP ainda sem Alembic, ver docstring de db/init_db.py):
Setting ganha chave composta (multi-conta-plano.md, Fase E, ver 10.8) -- ver
a nova docstring da classe Setting em db/models.py pro esquema final (`id`
substituto + dois índices únicos parciais, porque account_id é NULLABLE e
uma PK/UNIQUE composta comum não suportaria isso -- NULL nunca colide com
NULL numa constraint normal).

Por quê: `key` sozinha como PK só permitia 1 linha GLOBAL por chave -- as
Fases B/C já tinham `account_id` na tabela, mas só como rótulo informativo
(nunca usado pra filtrar escrita/leitura de verdade; cycle_runner.py sempre
chamou load_runtime_config() SEM account_id, então todo valor sempre valeu
igual pras duas contas). Pra CONTA MICAEL conseguir ter um teto de alocação,
confiança mínima etc DE VERDADE diferente da CONTA IVAN, cada conta precisa
poder ter sua PRÓPRIA linha por chave -- e é isso que orchestrator/
cycle_runner.py passa a usar a partir desta migração
(load_runtime_config(account_id=account.id) por conta, em vez do config
único e global de antes -- ver multi-conta-plano.md 10.8).

Passo crítico, roda UMA VEZ SÓ (nunca de novo -- veja o porquê abaixo): as 7
chaves de configuração hoje existentes (tudo exceto `bot_status`, que já é
account_id NULL desde a Fase B) estão etiquetadas com o id da CONTA IVAN --
só um rótulo informativo até aqui, nunca usado pra filtrar. Pra preservar o
comportamento atual no dia da migração (nenhuma mudança de config pra
nenhuma conta) e ainda assim destravar divergência de verdade dali em
diante, essas 7 linhas voltam pra account_id = NULL (viram config
"master"/default -- a mesma semântica de `bot_status`) -- as duas contas
continuam vendo os MESMOS valores até o Ivan criar um override específico
pra uma conta na tela nova de /settings. Esse reset só roda enquanto a PK
antiga ainda está sobre `key` (a MESMA checagem que decide se a PK precisa
ser trocada) -- rodar o script de novo depois NÃO repete o reset, porque
nesse ponto um override real que o Ivan já tenha criado por conta seria
apagado por engano se o reset rodasse de novo.

Passos (idempotente -- seguro rodar mais de uma vez, EXCETO o reset de
account_id acima, que só acontece na primeira execução, pelo motivo
explicado):
  1. Adiciona a coluna `id` (UUID), se ainda não existir.
  2. Preenche `id` (gerado em Python -- não depende da extensão pgcrypto do
     Postgres) em toda linha ainda com `id` NULL.
  3. Se a PK ainda for sobre `key` (primeira execução): reseta account_id
     pra NULL em toda linha != 'bot_status', depois remove a PK antiga (nome
     buscado dinamicamente via information_schema, nunca hardcoded).
  4. Define `id` NOT NULL; cria a PK nova em `id`, se a tabela ainda não
     tiver PK nenhuma.
  5. Cria os dois índices únicos parciais:
       - uq_settings_global_key: (key) WHERE account_id IS NULL
       - uq_settings_account_key: (account_id, key) WHERE account_id IS NOT NULL

Uso:
    python migrate_add_settings_composite_key.py
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from db.session import engine


def main() -> None:
    with engine.begin() as conn:
        # 1. Coluna id (UUID) -- nullable por enquanto (linhas antigas ainda
        #    não têm valor).
        conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS id UUID"))

        # 2. Preenche id em toda linha ainda sem id. Nesse ponto `key`
        #    continua sendo a PK antiga (ou já não existe nenhuma linha sem
        #    id, numa segunda execução) -- então cada `key` é único.
        rows = conn.execute(text("SELECT key FROM settings WHERE id IS NULL")).all()
        for row in rows:
            conn.execute(
                text("UPDATE settings SET id = :id WHERE key = :key AND id IS NULL"),
                {"id": str(uuid.uuid4()), "key": row.key},
            )
        if rows:
            print(f"id preenchido em {len(rows)} linha(s) de settings.")
        else:
            print("Nenhuma linha de settings sem id (nada a preencher).")

        # 3. PK antiga (sobre `key`) -- só existe na primeira execução.
        pk_on_key = conn.execute(
            text(
                "SELECT tc.constraint_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name "
                "WHERE tc.table_name = 'settings' AND tc.constraint_type = 'PRIMARY KEY' "
                "  AND kcu.column_name = 'key'"
            )
        ).scalar()
        if pk_on_key:
            # Reset do account_id ANTES de derrubar a PK -- ver docstring
            # acima pro porquê disso só pode acontecer nesta janela (primeira
            # execução), nunca depois.
            reset = conn.execute(
                text("UPDATE settings SET account_id = NULL WHERE key != 'bot_status' AND account_id IS NOT NULL")
            )
            if reset.rowcount:
                print(
                    f"account_id resetado pra NULL em {reset.rowcount} linha(s) "
                    "(rótulo antigo da CONTA IVAN, agora config master/default -- ver docstring)."
                )
            else:
                print("Nenhuma linha pra resetar account_id (já estava NULL, ou só bot_status existe).")
            conn.execute(text(f'ALTER TABLE settings DROP CONSTRAINT "{pk_on_key}"'))
            print(f"PK antiga ({pk_on_key}, sobre `key`) removida.")
        else:
            print(
                "PK antiga sobre `key` não encontrada (já migrado antes -- reset de "
                "account_id NÃO repetido, de propósito -- ver docstring)."
            )

        # 4. id vira NOT NULL; cria a PK nova, se ainda não houver nenhuma.
        conn.execute(text("ALTER TABLE settings ALTER COLUMN id SET NOT NULL"))
        has_pk = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'settings' AND constraint_type = 'PRIMARY KEY'"
            )
        ).scalar()
        if not has_pk:
            conn.execute(text("ALTER TABLE settings ADD PRIMARY KEY (id)"))
            print("Nova PK (id) criada.")
        else:
            print("settings já tem PK (id) -- nada a criar.")

        # 5. Unicidade parcial -- ver docstring da classe Setting pro porquê
        #    de não dar pra usar uma UNIQUE/PK composta comum aqui (account_id
        #    é NULLABLE, e NULL nunca colide consigo mesmo numa constraint
        #    normal -- índice parcial fecha essa lacuna dos dois lados).
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_settings_global_key "
            "ON settings (key) WHERE account_id IS NULL"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_settings_account_key "
            "ON settings (account_id, key) WHERE account_id IS NOT NULL"
        ))
        print("Índices únicos parciais (global e por conta) garantidos.")

    print("Migração de settings (chave composta) concluída.")


if __name__ == "__main__":
    main()

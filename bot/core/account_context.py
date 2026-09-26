"""Contexto de conta (multi-conta-plano.md, Fase B): agrupa tudo que hoje é
lido de globais (settings.binance_api_key/secret, o singleton
core.binance_client.binance_client) numa unidade explícita por conta -- id,
label, um BinanceClient já autenticado com as credenciais DESSA conta
(decifradas em memória, nunca gravadas em texto plano em lugar nenhum), o
dry_run da conta, e is_active.

Ainda NÃO é usado por nenhum agente nem pelo cycle_runner -- essa é só a
infraestrutura de leitura das contas, pronta pra quando o resto da Fase B
(agentes passarem a receber isto em vez de importar `settings`/
`binance_client` direto) for implementado, num passo seguinte e sincronizado
à parte. Zero mudança de comportamento do bot hoje.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from core.binance_client import BinanceClient
from core.crypto import decrypt_secret
from db.models import Account
from db.session import SessionLocal


@dataclass(frozen=True)
class AccountContext:
    id: uuid.UUID
    label: str
    dry_run: bool
    binance: BinanceClient


def _build_context(account: Account) -> AccountContext:
    api_key = decrypt_secret(account.binance_api_key_encrypted)
    api_secret = decrypt_secret(account.binance_api_secret_encrypted)
    return AccountContext(
        id=account.id,
        label=account.label,
        dry_run=account.dry_run,
        binance=BinanceClient(api_key=api_key, api_secret=api_secret),
    )


def load_active_accounts() -> list[AccountContext]:
    """Todas as contas com is_active=True, ordenadas por display_order --
    o conjunto que o cycle_runner vai iterar na Fase C."""
    with SessionLocal() as session:
        accounts = (
            session.query(Account)
            .filter(Account.is_active.is_(True))
            .order_by(Account.display_order, Account.created_at)
            .all()
        )
    return [_build_context(a) for a in accounts]


def load_account(account_id: uuid.UUID) -> AccountContext | None:
    """Uma conta específica por id (ativa ou não) -- útil pra scripts manuais
    (ex: testar uma conta só, sem rodar o ciclo inteiro)."""
    with SessionLocal() as session:
        account = session.get(Account, account_id)
    if account is None:
        return None
    return _build_context(account)

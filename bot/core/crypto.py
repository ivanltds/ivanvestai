"""Criptografia simétrica das credenciais de API da Binance por conta
(multi-conta-plano.md seção 5.1). Nunca em texto plano no banco -- só a
chave de criptografia (ACCOUNTS_ENCRYPTION_KEY) fica no .env local do bot,
nunca no Postgres.

Gerar uma chave nova: `python -m core.crypto`
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings


class AccountsEncryptionKeyMissing(RuntimeError):
    """ACCOUNTS_ENCRYPTION_KEY não configurada -- ver .env.example."""


def _fernet() -> Fernet:
    key = settings.accounts_encryption_key
    if not key:
        raise AccountsEncryptionKeyMissing(
            "ACCOUNTS_ENCRYPTION_KEY não está definida no .env -- gere uma com "
            "`python -m core.crypto` e cole no .env antes de cadastrar contas."
        )
    return Fernet(key.encode("utf-8"))


def encrypt_secret(plain: str) -> str:
    """Cifra uma credencial (API key/secret) pra gravar no banco."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decifra uma credencial já cifrada, lida do banco."""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Falha ao decifrar credencial -- ACCOUNTS_ENCRYPTION_KEY no .env não "
            "bate com a chave usada pra cifrar (chave trocada ou dado corrompido)."
        ) from exc


def generate_key() -> str:
    """Gera uma chave Fernet nova (32 bytes, base64 urlsafe) pro .env."""
    return Fernet.generate_key().decode("utf-8")


if __name__ == "__main__":
    print(generate_key())

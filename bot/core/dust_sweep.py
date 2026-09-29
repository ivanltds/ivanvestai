"""Limpeza de poeira: converte saldos pequenos em BNB (29/09/2026).

Poeira = saldo pequeno demais pra vender (a Binance recusa ordem abaixo do
mínimo de ~US$ 5). A única forma de zerar é a função da Binance "Converter
saldos pequenos em BNB" (POST /sapi/v1/asset/dust): converte 100% do saldo
LIVRE de cada moeda escolhida em BNB, cobrando a taxa da Binance (hoje ~2%).
Quais moedas são elegíveis quem decide é a Binance (POST /sapi/v1/asset/dust-btc).

Regras:
  - só contas com capital real (conta e settings.dry_run desligados);
  - nunca converte: a stablecoin de segurança (USDT), outras stablecoins,
    o próprio BNB, nem moedas de posições abertas do bot nessa conta;
  - no máximo 1 conversão por conta a cada `dust_sweep_interval_hours`
    (a Binance também limita a frequência);
  - liga/desliga e intervalo em /settings (core/config_store.py).

O main.py chama `run_dust_sweep()` de hora em hora; a função decide se é hora.
Qualquer erro vira log e nunca afeta ciclo, monitor nem posições.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import requests

from config.settings import settings
from core.risk_rules import is_stablecoin

logger = logging.getLogger("ivanvestai.dust_sweep")

LAST_RUN_KEY = "dust_sweep_last_at"
DUST_URL = "https://api.binance.com/sapi/v1/asset/dust"
_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "BRL", "EUR")


def base_asset(pair: str) -> str:
    for quote in _QUOTES:
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)]
    return pair


def pick_assets(details: list[dict], excluded: set[str]) -> list[str]:
    """Das moedas que a Binance aceita converter, as que o bot vai converter."""
    out = []
    for item in details or []:
        asset = str(item.get("asset", "")).upper()
        try:
            amount = float(item.get("amountFree") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if not asset or amount <= 0 or asset in excluded or asset == "BNB" or is_stablecoin(asset):
            continue
        out.append(asset)
    return sorted(set(out))


def is_due(last_at: dt.datetime | None, now: dt.datetime, interval_hours: float) -> bool:
    return last_at is None or now - last_at >= dt.timedelta(hours=interval_hours)


def _open_position_assets(account_id) -> set[str]:
    from db.models import Position
    from db.session import get_session

    with get_session() as session:
        pairs = [p for (p,) in session.query(Position.pair)
                 .filter(Position.account_id == account_id, Position.status == "open").all()]
    return {base_asset(p) for p in pairs}


def _read_last_at(account_id) -> dt.datetime | None:
    from db.models import Setting
    from db.session import get_session

    with get_session() as session:
        row = session.query(Setting).filter(Setting.key == LAST_RUN_KEY, Setting.account_id == account_id).first()
        if row is None:
            return None
        try:
            return dt.datetime.fromisoformat(json.loads(row.value)["at"])
        except (ValueError, TypeError, KeyError):
            return None


def _write_last_at(account_id, now: dt.datetime, summary: dict) -> None:
    from db.models import Setting
    from db.session import get_session

    value = json.dumps({"at": now.isoformat(), **summary})
    with get_session() as session:
        row = session.query(Setting).filter(Setting.key == LAST_RUN_KEY, Setting.account_id == account_id).first()
        if row is None:
            session.add(Setting(key=LAST_RUN_KEY, value=value, account_id=account_id))
        else:
            row.value = value


def _transfer_dust(client, assets: list[str]) -> dict:
    """POST /sapi/v1/asset/dust com `asset` repetido (asset=A&asset=B). O
    python-binance não serializa lista, então a chamada é assinada aqui, com a
    mesma key/secret e a correção de relógio do client da conta."""
    ts = int(time.time() * 1000 + getattr(client, "timestamp_offset", 0))
    query = urlencode([("asset", a) for a in assets] + [("recvWindow", 10000), ("timestamp", ts)])
    signature = hmac.new(client.API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    resp = requests.post(f"{DUST_URL}?{query}&signature={signature}",
                         headers={"X-MBX-APIKEY": client.API_KEY}, timeout=20)
    data = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"Binance recusou a conversão ({resp.status_code}): {data}")
    return data


def sweep_account(account, now: dt.datetime) -> dict | None:
    """Uma conta. Devolve o resumo gravado, ou None se ainda não é hora."""
    if not is_due(_read_last_at(account.id), now, settings.dust_sweep_interval_hours):
        return None
    client = account.binance._client
    info = client.get_dust_assets()
    excluded = _open_position_assets(account.id) | {settings.safety_stablecoin.upper()}
    assets = pick_assets(info.get("details", []), excluded)
    if not assets:
        summary = {"assets": [], "bnb": 0.0}
        logger.info("[%s] Limpeza de poeira: nada a converter.", account.label)
    else:
        result = _transfer_dust(client, assets)
        done = [r.get("fromAsset") for r in result.get("transferResult", [])]
        bnb = float(result.get("totalTransfered") or 0)
        fee = float(result.get("totalServiceCharge") or 0)
        summary = {"assets": done, "bnb": bnb, "fee_bnb": fee}
        logger.info("[%s] Limpeza de poeira: %s convertido(s) em %.8f BNB (taxa %.8f BNB).",
                    account.label, ", ".join(done) or "-", bnb, fee)
    _write_last_at(account.id, now, summary)
    return summary


def run_dust_sweep() -> None:
    """Chamado de hora em hora pelo main.py."""
    if not settings.dust_sweep_enabled:
        return
    if settings.dry_run:
        logger.info("Limpeza de poeira: bot em simulação (DRY_RUN) -- nada é convertido.")
        return
    from core.account_context import load_active_accounts

    now = dt.datetime.now(dt.timezone.utc)
    for account in load_active_accounts():
        if account.dry_run:
            continue
        try:
            sweep_account(account, now)
        except Exception as exc:  # noqa: BLE001 -- limpeza é opcional
            logger.warning("[%s] Limpeza de poeira falhou (%r) -- tenta de novo na próxima hora.", account.label, exc)

"""Coletor de métricas de mercado além do preço (29/09/2026).

Roda de hora em hora pelo main.py (job "market_metrics") e grava na tabela
`market_metrics` (db/models.py:MarketMetric), por par:

  - funding rate (futuros perpétuos USDT-M)  -- API tem anos de histórico, mas
    gravamos junto pra ficar tudo na mesma linha do tempo;
  - contratos em aberto, proporção de contas compradas/vendidas, proporção dos
    top traders e agressão compradora/vendedora -- a API da Binance só guarda
    30 DIAS desses, então este coletor é a única forma de termos histórico;
  - índice de Medo e Ganância (alternative.me, diário). A fonte exige crédito
    onde o dado for exibido ("Fonte: Alternative.me Crypto Fear & Greed Index").

Só coleta: nenhum campo é usado em decisão de trade. Tudo best-effort -- erro
num par ou numa fonte vira campo vazio e log, nunca derruba o bot. Só
endpoints PÚBLICOS de dados (nenhuma ordem, nenhum dado de conta).
"""
from __future__ import annotations

import datetime as dt
import logging

import requests

from core.binance_client import BinanceClient

logger = logging.getLogger("ivanvestai.market_metrics")

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1&format=json"
ALWAYS = ("BTCUSDT", "ETHUSDT")


def fetch_fear_greed(timeout: float = 10.0) -> int | None:
    try:
        data = requests.get(FEAR_GREED_URL, timeout=timeout).json()["data"][0]
        return int(data["value"])
    except Exception as exc:  # noqa: BLE001 -- fonte opcional
        logger.warning("market_metrics: Medo e Ganância indisponível (%r).", exc)
        return None


def _last(rows) -> dict | None:
    return rows[-1] if rows else None


def _float(row: dict | None, key: str) -> float | None:
    try:
        return float(row[key]) if row and row.get(key) not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_symbol_metrics(client, symbol: str) -> dict:
    """Métricas de UM par nos futuros USDT-M. `client` é o python-binance Client
    (BinanceClient._client). Cada fonte falha isolada (campo fica None)."""
    out: dict = {"symbol": symbol}

    def safe(label, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- par sem futuros, limite, rede
            logger.debug("market_metrics: %s %s falhou (%r).", symbol, label, exc)
            return None

    fr = _last(safe("funding", lambda: client.futures_funding_rate(symbol=symbol, limit=1)))
    out["funding_rate"] = _float(fr, "fundingRate")
    out["price"] = _float(fr, "markPrice")
    if fr and fr.get("fundingTime"):
        out["funding_time"] = dt.datetime.fromtimestamp(int(fr["fundingTime"]) / 1000, tz=dt.timezone.utc)
    oi = _last(safe("open_interest", lambda: client.futures_open_interest_hist(symbol=symbol, period="1h", limit=1)))
    out["open_interest"] = _float(oi, "sumOpenInterest")
    out["open_interest_usd"] = _float(oi, "sumOpenInterestValue")
    ls = _last(safe("long_short", lambda: client.futures_global_longshort_ratio(symbol=symbol, period="1h", limit=1)))
    out["long_short_ratio"] = _float(ls, "longShortRatio")
    top = _last(safe("top_traders", lambda: client.futures_top_longshort_position_ratio(symbol=symbol, period="1h", limit=1)))
    out["top_trader_long_short_ratio"] = _float(top, "longShortRatio")
    tk = _last(safe("taker", lambda: client.futures_taker_longshort_ratio(symbol=symbol, period="1h", limit=1)))
    out["taker_buy_sell_ratio"] = _float(tk, "buySellRatio")
    return out


def collect(binance: BinanceClient, top_n: int = 30) -> list[dict]:
    """Uma coleta completa: BTC, ETH e os `top_n` pares de maior volume."""
    try:
        pairs = binance.get_top_pairs_by_volume(quote="USDT", top_n=top_n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_metrics: falha listando pares (%r) -- coletando só BTC/ETH.", exc)
        pairs = []
    symbols = list(dict.fromkeys([*ALWAYS, *pairs]))
    fear_greed = fetch_fear_greed()
    rows = []
    for symbol in symbols:
        row = fetch_symbol_metrics(binance._client, symbol)
        if all(row.get(k) is None for k in ("funding_rate", "open_interest", "long_short_ratio")):
            continue  # par sem futuros perpétuos -- nada a gravar
        row["fear_greed"] = fear_greed
        rows.append(row)
    return rows


def collect_and_store(binance: BinanceClient | None = None, top_n: int = 30) -> int:
    """Coleta e grava. Devolve o nº de linhas gravadas. Chamado pelo main.py."""
    from core.binance_client import binance_client
    from db.models import MarketMetric
    from db.session import get_session

    rows = collect(binance or binance_client, top_n=top_n)
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    with get_session() as session:
        for row in rows:
            session.add(MarketMetric(timestamp=now, **row))
    logger.info("market_metrics: %d par(es) gravado(s) (Medo e Ganância=%s).",
                len(rows), rows[0]["fear_greed"] if rows else None)
    return len(rows)

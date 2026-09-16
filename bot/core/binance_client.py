"""Wrapper fino sobre python-binance, restrito à subconta dedicada do bot.

Só este módulo fala com a Binance — nenhum outro lugar do bot ou do
dashboard deve chamar a API da Binance diretamente.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from binance.client import Client
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

_INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240}


class BinanceClient:
    def __init__(self) -> None:
        self._client = Client(settings.binance_api_key, settings.binance_api_secret)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_account_balances(self) -> list[dict]:
        """Saldos != 0 da subconta (spot)."""
        account = self._client.get_account()
        return [b for b in account["balances"] if float(b["free"]) + float(b["locked"]) > 0]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_top_pairs_by_volume(self, quote: str = "USDT", top_n: int = 100) -> list[str]:
        """Top N pares por volume de 24h cotados na stablecoin de segurança."""
        tickers = self._client.get_ticker()
        pairs = [t for t in tickers if t["symbol"].endswith(quote)]
        pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        return [p["symbol"] for p in pairs[:top_n]]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_klines_df(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        raw = self._client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=_KLINE_COLUMNS)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_klines_df_window(
        self, symbol: str, interval: str, num_candles: int, end_time: dt.datetime
    ) -> pd.DataFrame:
        """Busca `num_candles` klines terminando em `end_time` (não precisa ser "agora").

        Usado pelo backtest pra testar janelas históricas diferentes (ex: um
        período de 60-90 dias atrás), em vez de só os candles mais recentes
        como `get_klines_df` faz. Usa `get_historical_klines`, que pagina
        automaticamente se o intervalo pedido exceder o limite de uma chamada.
        """
        minutes = _INTERVAL_MINUTES[interval]
        start_time = end_time - dt.timedelta(minutes=minutes * num_candles)
        raw = self._client.get_historical_klines(
            symbol, interval,
            start_time.strftime("%d %b, %Y %H:%M:%S"),
            end_time.strftime("%d %b, %Y %H:%M:%S"),
        )
        df = pd.DataFrame(raw, columns=_KLINE_COLUMNS)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_symbol_filters(self, symbol: str) -> dict:
        """minNotional, stepSize etc — necessário pra validar tamanho mínimo de ordem."""
        info = self._client.get_symbol_info(symbol)
        return {f["filterType"]: f for f in info["filters"]}

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        return self._client.create_order(symbol=symbol, side=side, type="MARKET", quantity=quantity)

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float, ttl_seconds: int = 10) -> dict:
        """Ordem limit com fallback pra mercado se não preencher dentro do ttl.

        Implementação simplificada pro MVP: cria a ordem IOC (immediate-or-cancel);
        se não preencher, o ExecutionAgent decide se tenta de novo a mercado.
        """
        return self._client.create_order(
            symbol=symbol, side=side, type="LIMIT", timeInForce="IOC",
            quantity=quantity, price=f"{price:.8f}",
        )


binance_client = BinanceClient()

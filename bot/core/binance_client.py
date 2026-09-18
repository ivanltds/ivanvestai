"""Wrapper fino sobre python-binance, restrito à subconta dedicada do bot.

Só este módulo fala com a Binance — nenhum outro lugar do bot ou do
dashboard deve chamar a API da Binance diretamente.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from core.order_utils import format_quantity

# Dicas amigáveis pros códigos de erro da Binance mais comuns. -2015 pode
# acontecer por dois motivos bem diferentes, então a dica agora depende de
# qual tipo de chamada falhou (achado em 16/09/2026 rodando com dry_run=False
# pela primeira vez, ver arquitetura-tecnica.md 9.12): se uma leitura assinada
# como get_account_balances() já funcionou nesse mesmo processo, IP e
# 'Enable Reading' já estão comprovadamente corretos -- um -2015 numa ORDEM
# (place_market_order/place_limit_order) depois disso quase sempre significa
# só que falta marcar 'Enable Spot & Margin Trading' na mesma API key.
_BINANCE_ERROR_HINTS: dict[int, str] = {
    -2015: (
        "Erro -2015 (chave/IP/permissão inválidos) -- não é bug de código, é config da "
        "conta/chave na Binance. Confira, nessa ordem de probabilidade:\n"
        "      1) Se isso aconteceu tentando ENVIAR UMA ORDEM (place_market_order/"
        "place_limit_order) e leituras assinadas (saldo, get_my_trades) já funcionaram "
        "antes nesse mesmo processo: falta marcar 'Enable Spot & Margin Trading' na API "
        "key (só 'Enable Reading' não é suficiente pra operar).\n"
        "      2) Restrição de IP da API key (Binance > API Management) não inclui o IP "
        "público atual desta máquina.\n"
        "      3) A permissão 'Enable Reading' está desmarcada nessa key.\n"
        "      4) BINANCE_API_KEY/BINANCE_API_SECRET no .env não batem com essa key "
        "(espaço extra, key revogada/regenerada, ou mainnet/testnet trocados).\n"
        "      https://www.binance.com/en/my/settings/api-management"
    ),
    -1021: (
        "Erro -1021 (timestamp fora da janela) -- o relógio deste computador está "
        "dessincronizado com o servidor da Binance. No Windows: Configurações > Hora e "
        "idioma > Data e hora > 'Sincronizar agora'."
    ),
}


def _hint_for(exc: BinanceAPIException) -> str | None:
    return _BINANCE_ERROR_HINTS.get(exc.code)

_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

_INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


class BinanceClient:
    def __init__(self) -> None:
        self._client = Client(settings.binance_api_key, settings.binance_api_secret)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_account_balances(self) -> list[dict]:
        """Saldos != 0 da subconta (spot)."""
        try:
            account = self._client.get_account()
        except BinanceAPIException as exc:
            hint = _hint_for(exc)
            if hint:
                print(f"\n[binance_client] {hint}\n")
            raise
        return [b for b in account["balances"] if float(b["free"]) + float(b["locked"]) > 0]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_top_pairs_by_volume(self, quote: str = "USDT", top_n: int = 100) -> list[str]:
        """Top N pares por volume de 24h cotados na stablecoin de segurança."""
        tickers = self._client.get_ticker()
        pairs = [t for t in tickers if t["symbol"].endswith(quote)]
        pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        return [p["symbol"] for p in pairs[:top_n]]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_klines_df(self, symbol: str, interval: str, limit: int = 200, closed_only: bool = False) -> pd.DataFrame:
        """`closed_only=True` descarta o candle ainda em formação (o último, se
        `close_time` for futuro) -- indicadores/volume calculados num candle
        parcial divergem do backtest, que só enxerga candles fechados. O
        scanner e o PositionReviewAgent usam True; o padrão False preserva o
        comportamento dos scripts de backtest/paper trading."""
        raw = self._client.get_klines(symbol=symbol, interval=interval, limit=limit + 1 if closed_only else limit)
        df = pd.DataFrame(raw, columns=_KLINE_COLUMNS)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        if closed_only and len(df) and int(df["close_time"].iloc[-1]) > int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000):
            df = df.iloc[:-1]
        return df.tail(limit).reset_index(drop=True) if closed_only else df

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
    def get_last_price(self, symbol: str) -> float:
        """Preço atual (ticker) -- usado pelo paper trading (run_paper_trading.py)
        pra gerir stop/take de posições simuladas em tempo real, sem precisar
        esperar o fechamento do próximo candle. Só leitura, nenhuma ordem."""
        ticker = self._client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_asset_balance(self, asset: str) -> tuple[float, float]:
        """(free, locked) de um ativo da subconta -- usado antes de vender pra
        nunca pedir mais do que o saldo real (taxa cobrada no ativo, saldo em
        ordem aberta etc). Só leitura."""
        bal = self._client.get_asset_balance(asset=asset)
        if not bal:
            return 0.0, 0.0
        return float(bal["free"]), float(bal["locked"])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_my_trades(self, symbol: str) -> list[dict]:
        """Histórico de execuções da subconta nesse par -- usado pelo
        PortfolioAgent pra calcular o preço médio de compra real, em vez de
        aproximar pelo preço atual (ver arquitetura-tecnica.md 9.3/9.6)."""
        return self._client.get_my_trades(symbol=symbol)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_symbol_filters(self, symbol: str) -> dict:
        """minNotional, stepSize etc — necessário pra validar tamanho mínimo de ordem."""
        info = self._client.get_symbol_info(symbol)
        return {f["filterType"]: f for f in info["filters"]}

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        # A dica amigável de erro (_hint_for) só estava plugada em
        # get_account_balances() -- ordem real caindo em -2015 batia direto
        # no traceback cru do apscheduler, sem nenhuma pista (achado em
        # 16/09/2026, primeira vez rodando com dry_run=False, ver
        # arquitetura-tecnica.md 9.12). Corrigido aqui e no place_limit_order.
        try:
            # quantity como string decimal (nunca '1e-05') -- ver core/order_utils.py
            return self._client.create_order(
                symbol=symbol, side=side, type="MARKET", quantity=format_quantity(quantity)
            )
        except BinanceAPIException as exc:
            hint = _hint_for(exc)
            if hint:
                print(f"\n[binance_client] {hint}\n")
            raise

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float, ttl_seconds: int = 10) -> dict:
        """Ordem limit com fallback pra mercado se não preencher dentro do ttl.

        Implementação simplificada pro MVP: cria a ordem IOC (immediate-or-cancel);
        se não preencher, o ExecutionAgent decide se tenta de novo a mercado.
        """
        try:
            return self._client.create_order(
                symbol=symbol, side=side, type="LIMIT", timeInForce="IOC",
                quantity=format_quantity(quantity), price=f"{price:.8f}",
            )
        except BinanceAPIException as exc:
            hint = _hint_for(exc)
            if hint:
                print(f"\n[binance_client] {hint}\n")
            raise


binance_client = BinanceClient()

"""Wrapper fino sobre python-binance, restrito à subconta dedicada do bot.

Só este módulo fala com a Binance — nenhum outro lugar do bot ou do
dashboard deve chamar a API da Binance diretamente.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

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


# Códigos de erro da Binance que são de CONFIGURAÇÃO/PERMANENTES -- tentar de
# novo não muda o resultado, porque a causa não é uma falha transitória de
# rede/servidor, é uma condição que só se resolve fora do bot (chave/IP/
# permissão, saldo insuficiente, parâmetro inválido etc). Até aqui TODA
# chamada de leitura tentava de novo 3x com backoff exponencial pra QUALQUER
# exceção, inclusive essas -- com um -2015 persistente (achado 24/09/2026,
# ver arquitetura-tecnica.md 9.21 item 14, depois de 12h+ contínuas do mesmo
# erro nos logs), isso multiplicava por ~3x tentativas x até 8s de espera x
# ~100 pares escaneados por ciclo x 96 ciclos/dia -- puro desperdício de
# tempo de ciclo e ruído de log, sem nenhuma chance de sucesso na 2ª/3ª
# tentativa. Qualquer código NÃO listado aqui continua tentando de novo
# normalmente (rate limit, timeout, erro interno da Binance etc -- esses sim
# costumam se resolver numa nova tentativa), e exceções que não são
# BinanceAPIException (erro de rede, timeout de conexão) também continuam
# sendo retentadas.
_NON_RETRYABLE_BINANCE_CODES = {
    -2015,  # Invalid API-key, IP, or permissions for action
    -2014,  # API-key format invalid
    -2008,  # Invalid Api-Key ID
    -1022,  # Signature for this request is not valid
    -1021,  # Timestamp for this request is outside of the recvWindow
    -2010,  # NEW_ORDER_REJECTED (ex: saldo insuficiente)
    -1013,  # Filter failure (LOT_SIZE, MIN_NOTIONAL etc.)
    -1121,  # Invalid symbol
    -1102,  # Parâmetro obrigatório ausente/malformado
    -1100,  # Caracteres ilegais no parâmetro
}


def _is_retryable_binance_error(exc: BaseException) -> bool:
    """Predicate do @retry: só NÃO tenta de novo se for um erro Binance
    permanente conhecido (ver _NON_RETRYABLE_BINANCE_CODES acima). Qualquer
    outra exceção (rede, timeout, erro genérico, código não mapeado) continua
    sendo retentada normalmente -- ficar de fora da lista é o padrão seguro."""
    if isinstance(exc, BinanceAPIException) and exc.code in _NON_RETRYABLE_BINANCE_CODES:
        return False
    return True


_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

_INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


class BinanceClient:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B): api_key/api_secret agora são
        # opcionais -- passe as credenciais DE UMA CONTA especifica pra operar essa
        # conta (ver core/account_context.py), ou omita pra manter o comportamento de
        # sempre (lê settings.binance_api_key/secret do .env, igual o singleton
        # `binance_client` no fim deste arquivo, usado por todo o resto do projeto
        # ainda hoje). Nenhum chamador existente precisa mudar.
        #
        # Timeout explícito (21/09/2026, ver arquitetura-tecnica.md 9.20/9.21, item
        # crítico #4): sem isso, uma chamada HTTP pra Binance podia travar a thread
        # indefinidamente -- como `_manage_open_positions` roda sob `_manage_lock`,
        # isso travava a checagem de stop/take de TODAS as posições reais abertas até
        # reinício manual, sem alerta nenhum. 20s é generoso o bastante pra não gerar
        # falso positivo em rede lenta, mas bem menor que "infinito".
        self._client = Client(
            api_key if api_key is not None else settings.binance_api_key,
            api_secret if api_secret is not None else settings.binance_api_secret,
            requests_params={"timeout": 20},
        )
        self._sync_time_offset()

    def _sync_time_offset(self) -> None:
        """Corrige o erro -1021 (timestamp fora do recvWindow) na raiz, em vez
        de depender de alguém lembrar de sincronizar o relógio do Windows
        manualmente (achado em produção em 26/09/2026 -- ver multi-conta-plano.md
        10.20 -- derrubou a leitura de carteira das DUAS contas no mesmo ciclo).
        Consulta o horário do servidor da Binance (endpoint público
        `get_server_time`, não exige assinatura -- funciona mesmo com o relógio
        local bem torto) e grava a diferença em `self._client.timestamp_offset`,
        que o python-binance soma a todo timestamp de chamada assinada a partir
        daqui. Como um `BinanceClient` novo é construído a cada ciclo (uma vez
        por conta ativa, ver `core/account_context.py`), isso resincroniza
        sozinho todo ciclo -- se o relógio do Windows dessincronizar nesse
        meio-tempo (ex: depois de hibernar), o ciclo seguinte já se autocorrige,
        sem precisar reiniciar o bot.
        Best-effort: se a própria consulta falhar (ex: rede fora do ar bem na
        hora de construir o client), não derruba a inicialização -- só segue
        sem correção, igual ao comportamento de antes desta mudança."""
        try:
            server_time_ms = self._client.get_server_time()["serverTime"]
            local_time_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
            self._client.timestamp_offset = server_time_ms - local_time_ms
        except Exception:
            pass

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
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

    # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 4): sufixos de
    # tokens alavancados da Binance (ex: BTCUPUSDT, ETHBULLUSDT) -- produtos
    # com rebalanceamento diário e decaimento por desenho, comportamento de
    # preço estruturalmente diferente de uma posição spot comum. Nada além
    # disso filtrava por tipo de produto antes de entrar no Top N escaneado.
    _LEVERAGED_TOKEN_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

    def _is_leveraged_token(self, symbol: str, quote: str) -> bool:
        base = symbol[: -len(quote)] if symbol.endswith(quote) else symbol
        return base.endswith(self._LEVERAGED_TOKEN_SUFFIXES)

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_top_pairs_by_volume(self, quote: str = "USDT", top_n: int = 100) -> list[str]:
        """Top N pares por volume de 24h cotados na stablecoin de segurança."""
        tickers = self._client.get_ticker()
        pairs = [
            t for t in tickers
            if t["symbol"].endswith(quote) and not self._is_leveraged_token(t["symbol"], quote)
        ]
        pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        return [p["symbol"] for p in pairs[:top_n]]

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
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

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
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

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_last_price(self, symbol: str) -> float:
        """Preço atual (ticker) -- usado pelo paper trading (run_paper_trading.py)
        pra gerir stop/take de posições simuladas em tempo real, sem precisar
        esperar o fechamento do próximo candle. Só leitura, nenhuma ordem."""
        ticker = self._client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    # Ativos-ponte tentados, em ordem, quando o par direto contra a quote não
    # existe na Binance (ex: um ativo só listado contra BTC, nunca contra
    # USDT diretamente). BTC/BNB/ETH cobrem a esmagadora maioria dos casos.
    _PRICE_BRIDGE_ASSETS = ("BTC", "BNB", "ETH")

    def get_last_price_via_bridge(self, asset: str, quote: str) -> float:
        """Preço de `asset` cotado em `quote`, com fallback pra ativo-ponte.

        Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 11): PortfolioAgent
        assumia par direto asset+quote pra TODO ativo da carteira; pra um ativo
        sem par direto contra USDT (só listado contra BTC/BNB, por exemplo),
        get_last_price(f"{asset}{quote}") falhava (símbolo inválido) e o
        chamador caía num fallback de valor 0.0 -- o ativo "sumia" do
        patrimônio total calculado, distorcendo o teto de alocação por
        operação e a avaliação de dust/relevância em outros agentes.
        Agora tenta o par direto primeiro e, se não existir, converte via
        BTC/BNB/ETH (nessa ordem). Levanta a última exceção se nada funcionar
        -- o chamador decide como tratar (nunca decidir por engano que o
        ativo vale zero)."""
        if asset == quote:
            return 1.0
        last_error: Exception | None = None
        try:
            return self.get_last_price(f"{asset}{quote}")
        except Exception as exc:  # noqa: BLE001 -- símbolo pode simplesmente não existir
            last_error = exc
        for bridge in self._PRICE_BRIDGE_ASSETS:
            if bridge in (asset, quote):
                continue
            try:
                asset_in_bridge = self.get_last_price(f"{asset}{bridge}")
                bridge_in_quote = self.get_last_price(f"{bridge}{quote}")
                return asset_in_bridge * bridge_in_quote
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        raise last_error or RuntimeError(f"Não foi possível precificar {asset}/{quote}")

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_asset_balance(self, asset: str) -> tuple[float, float]:
        """(free, locked) de um ativo da subconta -- usado antes de vender pra
        nunca pedir mais do que o saldo real (taxa cobrada no ativo, saldo em
        ordem aberta etc). Só leitura."""
        bal = self._client.get_asset_balance(asset=asset)
        if not bal:
            return 0.0, 0.0
        return float(bal["free"]), float(bal["locked"])

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_my_trades(self, symbol: str) -> list[dict]:
        """Histórico de execuções da subconta nesse par -- usado pelo
        PortfolioAgent pra calcular o preço médio de compra real, em vez de
        aproximar pelo preço atual (ver arquitetura-tecnica.md 9.3/9.6)."""
        return self._client.get_my_trades(symbol=symbol)

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_symbol_filters(self, symbol: str) -> dict:
        """minNotional, stepSize etc — necessário pra validar tamanho mínimo de ordem."""
        info = self._client.get_symbol_info(symbol)
        return {f["filterType"]: f for f in info["filters"]}

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_price_tick_size(self, symbol: str) -> float:
        """tickSize (PRICE_FILTER) -- granularidade mínima de preço pra ordens
        LIMIT/OCO. Necessário pra arredondar os níveis do OCO (ver
        core/order_utils.oco_price_levels)."""
        filters = self.get_symbol_filters(symbol)
        price_filter = filters.get("PRICE_FILTER", {})
        return float(price_filter.get("tickSize", 0) or 0)

    def place_oco_sell(
        self, symbol: str, quantity: float, take_price: float, stop_price: float, stop_limit_price: float
    ) -> dict:
        """OCO (One-Cancels-Other) de venda: perna de lucro (LIMIT em take_price) +
        perna de stop (STOP_LOSS_LIMIT, dispara em stop_price, vende a
        stop_limit_price) -- uma cancela a outra automaticamente quando executa.
        Proteção fica na própria Binance, sobrevive ao bot/PC desligado."""
        try:
            return self._client.create_oco_order(
                symbol=symbol, side="SELL", quantity=format_quantity(quantity),
                price=format_quantity(take_price),
                stopPrice=format_quantity(stop_price),
                stopLimitPrice=format_quantity(stop_limit_price),
                stopLimitTimeInForce="GTC",
            )
        except BinanceAPIException as exc:
            hint = _hint_for(exc)
            if hint:
                print(f"\n[binance_client] {hint}\n")
            raise

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_oco_order(self, order_list_id: int) -> dict:
        """Status da lista OCO (`listOrderStatus`: EXECUTING | ALL_DONE | REJECT)."""
        return self._client.get_oco_order(orderListId=order_list_id)

    @retry(retry=retry_if_exception(_is_retryable_binance_error), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_oco_sub_orders(self, symbol: str, order_list_id: int) -> list[dict]:
        """Detalhes completos (executedQty, price, cummulativeQuoteQty etc) de cada
        ordem-perna da lista OCO -- `get_oco_order` sozinho só devolve os orderIds,
        não o resultado do preenchimento."""
        oco = self._client.get_oco_order(orderListId=order_list_id)
        return [self._client.get_order(symbol=symbol, orderId=leg["orderId"]) for leg in oco.get("orders", [])]

    def cancel_oco_order(self, symbol: str, order_list_id: int) -> dict:
        """Cancela uma lista OCO ainda aberta -- necessário antes de qualquer
        venda manual/por software da mesma posição (o saldo fica travado nas
        ordens da lista até ela ser cancelada)."""
        try:
            return self._client.cancel_oco_order(symbol=symbol, orderListId=order_list_id)
        except BinanceAPIException as exc:
            hint = _hint_for(exc)
            if hint:
                print(f"\n[binance_client] {hint}\n")
            raise

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

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> dict:
        """Ordem limit IOC (immediate-or-cancel) -- preenche na hora ou cancela
        sozinha, sem nenhum "ttl" de fato implementado aqui (achado 24/09/2026,
        arquitetura-tecnica.md 9.20, Fase 5: o parâmetro `ttl_seconds` antigo não
        fazia nada -- a docstring prometia um fallback pra mercado que este método
        nunca implementou, e nenhum chamador do projeto passava esse argumento).

        NÃO É USADA em produção hoje: desde a reescrita de execution_agent.py em
        18-19/09/2026 (ver arquitetura-tecnica.md 9.17), toda ordem real do bot é
        a mercado (place_market_order) -- limit IOC podia expirar sem executar e
        mesmo assim deixar rastro de posição "fantasma". Mantida no cliente só
        como utilitário de baixo nível, caso algum dia volte a ser necessária.
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

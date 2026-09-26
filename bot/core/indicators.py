"""Cálculo de indicadores técnicos por estratégia, conforme
indicadores-estrategias.md. Usa pandas-ta sobre klines da Binance.

Cada função `score_*` devolve um voto no sistema de confluência:
+1 favorável, 0 neutro, -1 contrário — junto com os valores brutos
calculados (pra log/auditoria em committee_decisions.reasoning).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta


@dataclass
class IndicatorVote:
    name: str
    vote: int  # -1, 0, +1
    value: float | dict = field(default_factory=dict)


def _require_finite(value: float, label: str) -> float:
    """Levanta ValueError se `value` for NaN/Inf. pandas_ta devolve NaN
    SILENCIOSAMENTE (sem exceção) quando não há candles suficientes pro
    período do indicador (ex: par recém-listado com menos histórico do que o
    `length` pedido) -- comparações tipo `NaN > 25` são sempre False em
    Python, então um regime/voto calculado a partir de NaN parece uma
    leitura normal (ex: "lateral", vote=0/neutro) em vez de sinalizar "não
    deu pra calcular". Achado 24/09/2026, ver arquitetura-tecnica.md 9.21
    item 16. Levantar aqui deixa os agentes chamadores tratarem isso como
    qualquer outro erro de dados (o try/except por par já existente em
    MarketScannerAgent.run() pula o par neste ciclo)."""
    if value is None or math.isnan(value) or math.isinf(value):
        raise ValueError(f"{label} inválido (NaN/Inf) -- histórico insuficiente pra esse período.")
    return value


def _col(df: pd.DataFrame, prefix: str) -> pd.Series:
    """Busca a primeira coluna cujo nome comece com `prefix`.

    O nome exato das colunas geradas pelo pandas_ta (ex: `BBP_20_2.0` vs
    `BBP_20_2`) muda entre versões da lib — buscar por prefixo evita
    quebrar o bot toda vez que a versão instalada mudar o sufixo.
    """
    matches = [c for c in df.columns if str(c).startswith(prefix)]
    if not matches:
        raise KeyError(f"Nenhuma coluna com prefixo '{prefix}' encontrada em {list(df.columns)}")
    return df[matches[0]]


def adx_last(df: pd.DataFrame) -> float:
    """ADX(14) mais recente do timeframe passado -- extraído do market_regime
    pra permitir logar o valor bruto (ex: no diagnóstico do backtest) sem
    recalcular duas vezes nem duplicar a lógica de leitura de coluna."""
    adx = ta.adx(df["high"], df["low"], df["close"])
    return _require_finite(float(_col(adx, "ADX_").iloc[-1]), "ADX")


def market_regime(df_4h: pd.DataFrame) -> str:
    """Classifica o regime de mercado no timeframe maior: trend | lateral."""
    return "trend" if adx_last(df_4h) > 25 else "lateral"


# --- Trend following ---------------------------------------------------

def score_trend_following(df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> list[IndicatorVote]:
    votes: list[IndicatorVote] = []

    ema9 = ta.ema(df_1h["close"], length=9).iloc[-1]
    ema21 = ta.ema(df_1h["close"], length=21).iloc[-1]
    ema50 = ta.ema(df_1h["close"], length=50).iloc[-1]
    trend_up = ema9 > ema21 > ema50
    trend_down = ema9 < ema21 < ema50
    votes.append(IndicatorVote("ema_stack", 1 if trend_up else (-1 if trend_down else 0),
                                {"ema9": ema9, "ema21": ema21, "ema50": ema50}))

    macd = ta.macd(df_15m["close"])
    hist = _col(macd, "MACDh_")
    hist_rising = hist.iloc[-1] > hist.iloc[-2] > 0
    hist_falling = hist.iloc[-1] < hist.iloc[-2] < 0
    votes.append(IndicatorVote("macd_histogram", 1 if hist_rising else (-1 if hist_falling else 0),
                                {"hist_last": float(hist.iloc[-1])}))

    adx = _col(ta.adx(df_1h["high"], df_1h["low"], df_1h["close"]), "ADX_").iloc[-1]
    votes.append(IndicatorVote("adx_strength", 1 if adx > 25 else -1, {"adx": float(adx)}))

    return votes


# --- Mean reversion ------------------------------------------------------

def score_mean_reversion(df_15m: pd.DataFrame) -> list[IndicatorVote]:
    """Reversão à média só faz sentido sem tendência forte no próprio 15m
    (pré-condição documentada em indicadores-estrategias.md: "só aplicar se
    ADX < 20-25"). Sem esse filtro o bot tenta "pegar fundo/topo" durante uma
    tendência de verdade, o que historicamente performa mal (ver backtest)."""
    votes: list[IndicatorVote] = []

    adx_15m = _col(ta.adx(df_15m["high"], df_15m["low"], df_15m["close"]), "ADX_").iloc[-1]
    if adx_15m >= 25:
        return [IndicatorVote("adx_filter_blocked", 0, {"adx_15m": float(adx_15m)})]

    rsi = ta.rsi(df_15m["close"], length=14).iloc[-1]
    votes.append(IndicatorVote("rsi_14", 1 if rsi < 30 else (-1 if rsi > 70 else 0), {"rsi": float(rsi)}))

    bb = ta.bbands(df_15m["close"], length=20, std=2)
    percent_b = _col(bb, "BBP_").iloc[-1]
    votes.append(IndicatorVote("bollinger_percent_b", 1 if percent_b < 0.05 else (-1 if percent_b > 0.95 else 0),
                                {"percent_b": float(percent_b)}))

    stoch = ta.stoch(df_15m["high"], df_15m["low"], df_15m["close"])
    k = _col(stoch, "STOCHk_").iloc[-1]
    votes.append(IndicatorVote("stochastic", 1 if k < 20 else (-1 if k > 80 else 0), {"stoch_k": float(k)}))

    return votes


# --- Breakout -------------------------------------------------------------

def score_breakout(df_15m: pd.DataFrame) -> list[IndicatorVote]:
    votes: list[IndicatorVote] = []

    donchian = ta.donchian(df_15m["high"], df_15m["low"], lower_length=20, upper_length=20)
    upper = _col(donchian, "DCU_").iloc[-2]  # até o candle anterior, pra comparar o fechamento atual
    lower = _col(donchian, "DCL_").iloc[-2]
    close = df_15m["close"].iloc[-1]
    votes.append(IndicatorVote("donchian_breakout", 1 if close > upper else (-1 if close < lower else 0),
                                {"upper": float(upper), "lower": float(lower), "close": float(close)}))

    vol_avg = df_15m["volume"].rolling(20).mean().iloc[-1]
    vol_last = df_15m["volume"].iloc[-1]
    votes.append(IndicatorVote("volume_spike", 1 if vol_last >= 2 * vol_avg else 0,
                                {"volume_last": float(vol_last), "volume_avg20": float(vol_avg)}))

    atr = _require_finite(
        float(ta.atr(df_15m["high"], df_15m["low"], df_15m["close"], length=14).iloc[-1]), "ATR (score_breakout)"
    )
    votes.append(IndicatorVote("atr_reference", 0, {"atr": atr}))  # informativo, usado no stop

    return votes


# --- Scalping --------------------------------------------------------------

def score_scalping(df_5m: pd.DataFrame) -> list[IndicatorVote]:
    votes: list[IndicatorVote] = []

    vwap = ta.vwap(df_5m["high"], df_5m["low"], df_5m["close"], df_5m["volume"])
    last_close = df_5m["close"].iloc[-1]
    last_vwap = vwap.iloc[-1]
    votes.append(IndicatorVote("vwap_position", 1 if last_close < last_vwap else -1,
                                {"close": float(last_close), "vwap": float(last_vwap)}))

    ema9 = ta.ema(df_5m["close"], length=9).iloc[-1]
    ema21 = ta.ema(df_5m["close"], length=21).iloc[-1]
    votes.append(IndicatorVote("ema_cross_fast", 1 if ema9 > ema21 else -1, {"ema9": ema9, "ema21": ema21}))

    rsi7 = ta.rsi(df_5m["close"], length=7).iloc[-1]
    votes.append(IndicatorVote("rsi_fast", 1 if rsi7 < 35 else (-1 if rsi7 > 65 else 0), {"rsi7": float(rsi7)}))

    return votes


# --- Transversal -------------------------------------------------------

def atr_stop_reference(df_15m: pd.DataFrame, multiplier: float = 1.5) -> float:
    """ATR(14) * multiplicador — base pro stop-loss/trailing proporcional à volatilidade."""
    atr = _require_finite(float(ta.atr(df_15m["high"], df_15m["low"], df_15m["close"], length=14).iloc[-1]), "ATR (atr_stop_reference)")
    return atr * multiplier


def confluence_score(votes: list[IndicatorVote]) -> int:
    return sum(v.vote for v in votes)


def passes_confluence(votes: list[IndicatorVote], min_score: int = 2) -> bool:
    return confluence_score(votes) >= min_score


def volatility_signals(df_15m: pd.DataFrame, window: int = 20) -> tuple[float, float, float]:
    """Sinais de volatilidade extrema do candle mais recente do timeframe passado:
    (candle_range_pct, atr_pct_avg, volume_ratio) -- consumidos por
    core.risk_rules.volatility_extreme() (achado 24/09/2026, ver
    arquitetura-tecnica.md 9.21 item 13: a função de risco já existia mas
    nunca tinha os sinais calculados/passados por nenhum agente). `atr_pct_avg`
    é a média do ATR% (ATR/close) nas últimas `window` velas; `volume_ratio` é
    o volume da última vela sobre a média móvel de `window` velas."""
    high = df_15m["high"].iloc[-1]
    low = df_15m["low"].iloc[-1]
    close = df_15m["close"].iloc[-1]
    candle_range_pct = float((high - low) / close * 100) if close else 0.0

    atr = ta.atr(df_15m["high"], df_15m["low"], df_15m["close"], length=14)
    atr_pct_avg = float((atr / df_15m["close"] * 100).tail(window).mean())

    vol_avg = df_15m["volume"].rolling(window).mean().iloc[-1]
    vol_last = df_15m["volume"].iloc[-1]
    volume_ratio = float(vol_last / vol_avg) if vol_avg else 0.0

    return candle_range_pct, atr_pct_avg, volume_ratio


def meme_coin_raw_signals(df_1h: pd.DataFrame) -> tuple[float, float, float]:
    """Sinais objetivos de core.risk_rules.MemeCoinSignals que dá pra calcular só
    com candles (sem o sentimento social, que vem do NewsAgent em outro agente):
    (volume_zscore, price_momentum_4h_pct, price_momentum_24h_pct), sobre o
    timeframe de 1h. Achado 24/09/2026, ver arquitetura-tecnica.md 9.21 item 13."""
    volume = df_1h["volume"]
    recent_volume = volume.tail(20)
    vol_std = recent_volume.std()
    volume_zscore = float((volume.iloc[-1] - recent_volume.mean()) / vol_std) if vol_std else 0.0

    close = df_1h["close"]
    price_momentum_4h_pct = float((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100) if len(close) > 5 else 0.0
    price_momentum_24h_pct = float((close.iloc[-1] - close.iloc[-25]) / close.iloc[-25] * 100) if len(close) > 25 else 0.0

    return volume_zscore, price_momentum_4h_pct, price_momentum_24h_pct


def rolling_correlation(series_a: pd.Series, series_b: pd.Series, window: int = 30) -> float:
    """Correlação móvel (ex: retornos diários) entre dois ativos, pro filtro de correlação."""
    returns_a = series_a.pct_change().dropna()
    returns_b = series_b.pct_change().dropna()
    aligned = pd.concat([returns_a, returns_b], axis=1).dropna().tail(window)
    if len(aligned) < 5:
        return 0.0
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))

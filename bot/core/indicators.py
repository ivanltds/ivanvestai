"""Cálculo de indicadores técnicos por estratégia, conforme
indicadores-estrategias.md. Usa pandas-ta sobre klines da Binance.

Cada função `score_*` devolve um voto no sistema de confluência:
+1 favorável, 0 neutro, -1 contrário — junto com os valores brutos
calculados (pra log/auditoria em committee_decisions.reasoning).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta


@dataclass
class IndicatorVote:
    name: str
    vote: int  # -1, 0, +1
    value: float | dict = field(default_factory=dict)


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
    return float(_col(adx, "ADX_").iloc[-1])


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

    atr = ta.atr(df_15m["high"], df_15m["low"], df_15m["close"], length=14).iloc[-1]
    votes.append(IndicatorVote("atr_reference", 0, {"atr": float(atr)}))  # informativo, usado no stop

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
    return float(ta.atr(df_15m["high"], df_15m["low"], df_15m["close"], length=14).iloc[-1] * multiplier)


def confluence_score(votes: list[IndicatorVote]) -> int:
    return sum(v.vote for v in votes)


def passes_confluence(votes: list[IndicatorVote], min_score: int = 2) -> bool:
    return confluence_score(votes) >= min_score


def rolling_correlation(series_a: pd.Series, series_b: pd.Series, window: int = 30) -> float:
    """Correlação móvel (ex: retornos diários) entre dois ativos, pro filtro de correlação."""
    returns_a = series_a.pct_change().dropna()
    returns_b = series_b.pct_change().dropna()
    aligned = pd.concat([returns_a, returns_b], axis=1).dropna().tail(window)
    if len(aligned) < 5:
        return 0.0
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))

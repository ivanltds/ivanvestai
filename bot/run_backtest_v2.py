"""Simulador v2 -- base de medição do Plano de melhoria da estratégia (Fase 0).

Criado em 29/09/2026. Diferenças para `run_backtest_multi_tf.py` (que continua no
repo como referência histórica):

1. ENTRADA igual ao MarketScannerAgent DE HOJE (não à versão de 16/09):
   - regime pelo ADX(14) do 4h: > 25 = trend_following, senão mean_reversion
     (em produção o mean_reversion continua ATIVO -- só o backtest antigo o desligou);
   - breakout substitui a estratégia do regime quando o score dele é >= 2 e
     MAIOR que o da estratégia do regime (arquitetura-tecnica.md 9.24 item 28);
   - pula o par na vela com volatilidade extrema (risk_rules.volatility_extreme)
     ou colado em US$1 (risk_rules.is_stablecoin_peg);
   - entra quando o score >= 2, uma posição por par, oportunidades da mesma vela
     ordenadas por score (igual ao cycle_runner).
2. SAÍDA pelo mesmo módulo que o bot vai usar (core/exit_rules.py): pisos/tetos
   de stop/take 2-10% / 4-10%, stop móvel com a distância inicial, alvo sempre
   ativo, checagem stop -> alvo -> stop móvel.
3. PORTFÓLIO compartilhado entre todos os pares (não um caixa por par): cada
   entrada usa até 50% do patrimônio, limitado ao caixa livre (regra do
   PortfolioComparisonAgent / risk_rules.suggested_order_value).
4. TAXA em todo lugar: o resultado por operação é LÍQUIDO (compra e venda),
   com a taxa por lado configurável (--fee 0.001 hoje, 0.00075 com BNB).
5. JANELAS FIXAS (datas absolutas) pra ninguém "gastar" a validação sem querer:
       ajuste  2026-04-01 -> 2026-06-01   (pode olhar à vontade pra escolher parâmetros)
       val1    2026-06-01 -> 2026-08-01   (só pra validar; nunca pra escolher)
       val2    2026-08-01 -> 2026-09-28   (só pra validar; nunca pra escolher)
6. CACHE de klines em backtest_cache/ -- a primeira rodada busca na Binance, as
   seguintes são instantâneas e usam exatamente os mesmos dados.

O que o RiskCommitteeAgent (LLM) faria é APROXIMADO por uma regra fixa (ele
aprova ~96% em produção): aprova tudo, stop = 1,5 x ATR(14) do 15m, alvo =
2 x stop, stop móvel ligado -- tudo passando pelos mesmos pisos/tetos. Ajuste
com --committee-* depois de calibrar com analyze_period_performance.py (que lê
o stop/alvo/trailing que o comitê de fato escolheu em produção).

NÃO simulado (ainda): filtro de correlação > 0,75 com posição aberta, janela
macro FOMC/CPI, veto por notícia, teto menor pra meme coin, arredondamento de
lote/mínimo da Binance (irrelevante com o caixa simulado de 10k).

Fidelidade dos indicadores: com --modo rapido (padrão) os indicadores são
calculados uma vez sobre a série inteira (vetorizado), enquanto o bot usa uma
janela de 120 velas. EMA/ADX com janela curta diferem um pouco no início da
série. `--modo exato` recalcula tudo com as funções reais de core/indicators.py
em janelas de 120 velas (lento; use com 1-3 pares pra conferir que o modo
rápido gera os mesmos sinais -- o relatório mostra a concordância).

Uso (de dentro de bot/):
    python run_backtest_v2.py --janela ajuste --top 30 --tag baseline
    python run_backtest_v2.py --janela todas --top 30 --tag baseline
    python run_backtest_v2.py --janela ajuste --pares BTCUSDT,ETHUSDT --modo exato --tag conferencia
    # Fase 2 (exemplo): stop móvel só depois de +3%, 2% de distância, sem alvo
    python run_backtest_v2.py --janela ajuste --top 30 --tag f2a \
        --trailing-ativacao 3 --trailing-distancia 2 --sem-alvo

    # Fase 3: stop = 2 x ATR de 1h; tamanho tal que o stop custe 1% do patrimônio
    python run_backtest_v2.py --janela ajuste --top 30 --tag f3 \
        --trailing-ativacao 3 --trailing-distancia 2 --sem-alvo --stop-atr-1h 2 --risco-pct 1
    # Fase 4: filtros de entrada (BTC 4h em alta, sinal no 1h, universo)
    python run_backtest_v2.py ... --filtro-btc --sinal 1h --min-preco 0.05 \
        --min-volume-usd 20000000 --bloquear KITE,AUDIO,MUBARAK,ONE,BANK

Saídas: backtest_results/<tag>_<janela>_trades.csv e uma linha por rodada em
backtest_results/resumo.csv.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta

from core.exit_rules import ExitConfig, exit_hit, initial_levels, time_exit_due, trailing_is_armed, update_trailing

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "backtest_cache"
RESULTS_DIR = HERE / "backtest_results"

WINDOWS = {
    "ajuste": ("2026-04-01", "2026-06-01"),
    "val1": ("2026-06-01", "2026-08-01"),
    "val2": ("2026-08-01", "2026-09-28"),
    # prova final (29/09): períodos que nenhuma configuração viu. Rodar UMA vez, com a versão já escolhida.
    "final1": ("2025-10-01", "2026-01-01"),
    "final2": ("2026-01-01", "2026-04-01"),
    # estudo do filtro de mercado mais lento (29/09, opção 3): dados de 2024-2025, nunca usados antes.
    "ajuste2": ("2024-01-01", "2024-07-01"),
    "val3": ("2024-07-01", "2025-01-01"),
    "final3": ("2025-01-01", "2025-10-01"),
    # estudo de dados extras (funding / Medo e Ganância, 29/09): prova final em 2023, nunca usado.
    "prova2023": ("2023-01-01", "2024-01-01"),
}
WARMUP_DAYS = 25  # 120 velas de 4h = 20 dias, + folga
LOOKBACK = 120  # mesma janela do MarketScannerAgent (get_klines_df limit=120)
INITIAL_CASH = 10_000.0
MAX_POSITION_PCT = 0.5
INTERVALS = ("15m", "1h", "4h")
_DELTA = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}

# mesmos limiares de core/risk_rules.py (copiados pra não importar settings/.env no modo rápido)
VOL_EXTREME_RANGE_MULT = 3.0
VOL_EXTREME_VOLUME_MULT = 3.0
PEG_MIN, PEG_MAX, PEG_ATR_MAX = 0.98, 1.02, 0.3


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------

def _cache_path(symbol: str, interval: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}_{start}_{end}.csv.gz"


def load_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Klines de [start - WARMUP_DAYS, end), com cache em disco."""
    path = _cache_path(symbol, interval, start, end)
    if path.exists():
        df = pd.read_csv(path)
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        return df
    from core.binance_client import binance_client  # só quando precisa buscar

    t0 = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=WARMUP_DAYS)
    t1 = pd.Timestamp(end, tz="UTC")
    n = int((t1 - t0) / _DELTA[interval]) + 2
    df = binance_client.get_klines_df_window(symbol, interval, n, t1.to_pydatetime())
    df = df[["open_time", "open", "high", "low", "close", "volume"]]
    df = df[(df["open_time"] >= t0) & (df["open_time"] < t1)].drop_duplicates("open_time").reset_index(drop=True)
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(path, index=False, compression="gzip")
    return df


def load_universe(top: int, symbols: list[str] | None) -> list[str]:
    """Lista de pares. Com --top, a primeira execução grava a lista em cache e as
    seguintes reusam -- assim todas as rodadas comparam o MESMO universo.
    (Viés de sobrevivência: é o top de hoje aplicado ao passado. Aceitável pra
    comparar variações entre si; não pra estimar o retorno absoluto.)"""
    if symbols:
        return symbols
    path = CACHE_DIR / f"universe_top{top}.json"
    if path.exists():
        return json.loads(path.read_text())
    from core.binance_client import binance_client
    from core.risk_rules import is_stablecoin

    pairs = binance_client.get_top_pairs_by_volume(quote="USDT", top_n=top * 2)
    pairs = [p for p in pairs if not is_stablecoin(p.removesuffix("USDT"))][:top]
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(pairs, indent=1))
    return pairs


# ---------------------------------------------------------------------------
# Sinais -- modo rápido (vetorizado)
# ---------------------------------------------------------------------------

def _prefix(df: pd.DataFrame, prefix: str) -> pd.Series:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        raise KeyError(prefix)
    return df[cols[0]]


def _as_of(df_hi: pd.DataFrame, interval: str, values: dict[str, pd.Series], times_lo: pd.Series,
           lo_interval: str = "15m") -> pd.DataFrame:
    """Leva valores de um timeframe maior pra cada vela do timeframe menor usando
    só velas que JÁ FECHARAM no fechamento da vela menor (sem olhar o futuro)."""
    hi = pd.DataFrame({"close_time": df_hi["open_time"] + _DELTA[interval], **values})
    lo = pd.DataFrame({"t": times_lo + _DELTA[lo_interval]})
    out = pd.merge_asof(lo, hi.sort_values("close_time"), left_on="t", right_on="close_time", direction="backward")
    return out.drop(columns=["t", "close_time"])


def signals_fast(d15: pd.DataFrame, d1h: pd.DataFrame, d4h: pd.DataFrame, sig_interval: str = "15m",
                 use_mean_reversion: bool = True, breakout_volume_exempt: bool = False) -> pd.DataFrame:
    """Uma linha por vela do timeframe do sinal (15m = scanner de hoje; 1h =
    Fase 4): estratégia e score que o scanner daria no fechamento dela, mais os
    ATRs usados pro stop proposto. Com sig_interval="1h", `d15` recebe as velas
    de 1h -- MACD, reversão à média, rompimento e filtros de volatilidade passam
    a olhar o 1h; o regime continua no 4h e a pilha de EMAs no 1h."""
    lo = sig_interval
    c, h, lw, v = d15["close"], d15["high"], d15["low"], d15["volume"]

    adx4 = _prefix(ta.adx(d4h["high"], d4h["low"], d4h["close"]), "ADX_")
    ema9 = ta.ema(d1h["close"], length=9)
    ema21 = ta.ema(d1h["close"], length=21)
    ema50 = ta.ema(d1h["close"], length=50)
    adx1 = _prefix(ta.adx(d1h["high"], d1h["low"], d1h["close"]), "ADX_")
    atr1 = ta.atr(d1h["high"], d1h["low"], d1h["close"], length=14)
    hi4 = _as_of(d4h, "4h", {"adx4": adx4}, d15["open_time"], lo)
    hi1 = _as_of(d1h, "1h", {"ema9": ema9, "ema21": ema21, "ema50": ema50, "adx1": adx1, "atr1h": atr1},
                 d15["open_time"], lo)

    # trend following
    ema_vote = np.where((hi1.ema9 > hi1.ema21) & (hi1.ema21 > hi1.ema50), 1,
                        np.where((hi1.ema9 < hi1.ema21) & (hi1.ema21 < hi1.ema50), -1, 0))
    hist = _prefix(ta.macd(c), "MACDh_")
    hp = hist.shift(1)
    macd_vote = np.where((hist > hp) & (hp > 0), 1, np.where((hist < hp) & (hp < 0), -1, 0))
    adx1_vote = np.where(hi1.adx1 > 25, 1, -1)
    trend_score = ema_vote + macd_vote + adx1_vote

    # mean reversion (bloqueado se ADX 15m >= 25)
    adx15 = _prefix(ta.adx(h, lw, c), "ADX_")
    rsi = ta.rsi(c, length=14)
    bbp = _prefix(ta.bbands(c, length=20, std=2), "BBP_")
    k = _prefix(ta.stoch(h, lw, c), "STOCHk_")
    mr_score = (np.where(rsi < 30, 1, np.where(rsi > 70, -1, 0))
                + np.where(bbp < 0.05, 1, np.where(bbp > 0.95, -1, 0))
                + np.where(k < 20, 1, np.where(k > 80, -1, 0)))
    mr_score = np.where(adx15 >= 25, 0, mr_score)
    if not use_mean_reversion:  # v3 (problema 2): reversão à média desligada
        mr_score = np.zeros(len(c), dtype=int)

    # breakout
    dc = ta.donchian(h, lw, lower_length=20, upper_length=20)
    upper = _prefix(dc, "DCU_").shift(1)
    lower = _prefix(dc, "DCL_").shift(1)
    vol_avg20 = v.rolling(20).mean()
    atr15 = ta.atr(h, lw, c, length=14)
    bo_score = np.where(c > upper, 1, np.where(c < lower, -1, 0)) + np.where(v >= 2 * vol_avg20, 1, 0)

    regime_trend = hi4.adx4 > 25
    base_score = np.where(regime_trend, trend_score, mr_score)
    base_strategy = np.where(regime_trend, "trend_following", "mean_reversion")
    use_bo = (bo_score >= 2) & (bo_score > base_score)
    score = np.where(use_bo, bo_score, base_score)
    strategy = np.where(use_bo, "breakout", base_strategy)

    # filtros de volatilidade extrema e peg de stablecoin (mesmos do scanner)
    range_pct = (h - lw) / c * 100
    atr_pct_avg = (atr15 / c * 100).rolling(20).mean()
    vol_ratio = v / vol_avg20
    vol_spike = vol_ratio > VOL_EXTREME_VOLUME_MULT
    if breakout_volume_exempt:  # v3 (problema 3): volume alto é justamente o que confirma um rompimento
        vol_spike &= ~use_bo
    vol_extreme = (range_pct > atr_pct_avg * VOL_EXTREME_RANGE_MULT) | vol_spike
    peg = (c >= PEG_MIN) & (c <= PEG_MAX) & (atr_pct_avg < PEG_ATR_MAX)

    # sem histórico suficiente (o bot pula o par): exige indicadores válidos
    valid = hi4.adx4.notna() & hi1.ema50.notna() & hi1.adx1.notna() & atr15.notna() & upper.notna() & hist.notna() & hp.notna()
    valid &= np.where(regime_trend, True, adx15.notna() & rsi.notna() & bbp.notna() & k.notna())

    signal = valid & ~vol_extreme & ~peg & (score >= 2)
    return pd.DataFrame({
        "open_time": d15["open_time"], "close": c, "high": h, "low": lw,
        "signal": signal.astype(bool), "strategy": strategy, "score": score,
        "atr15": atr15, "atr1h": hi1.atr1h, "adx4": hi4.adx4,
        "quote_vol_24h": (v * c).rolling(int(pd.Timedelta(hours=24) / _DELTA[lo])).sum(),
    })


# ---------------------------------------------------------------------------
# Sinais -- modo exato (funções reais do bot, janela de 120 velas)
# ---------------------------------------------------------------------------

def signals_exact(d15: pd.DataFrame, d1h: pd.DataFrame, d4h: pd.DataFrame) -> pd.DataFrame:
    from core.indicators import (atr_stop_reference, confluence_score, market_regime, score_breakout,
                                 score_mean_reversion, score_trend_following, volatility_signals)
    from core.risk_rules import is_stablecoin_peg, volatility_extreme

    rows = []
    for i in range(len(d15)):
        now_close = d15["open_time"].iloc[i] + _DELTA["15m"]
        w15 = d15.iloc[max(0, i - LOOKBACK + 1): i + 1]
        w1 = d1h[d1h["open_time"] + _DELTA["1h"] <= now_close].tail(LOOKBACK)
        w4 = d4h[d4h["open_time"] + _DELTA["4h"] <= now_close].tail(LOOKBACK)
        rec = {"open_time": d15["open_time"].iloc[i], "close": float(d15["close"].iloc[i]),
               "high": float(d15["high"].iloc[i]), "low": float(d15["low"].iloc[i]),
               "signal": False, "strategy": "", "score": 0, "atr15": math.nan, "atr1h": math.nan,
               "adx4": math.nan, "quote_vol_24h": math.nan}
        if len(w15) < 60 or len(w1) < 55 or len(w4) < 30:
            rows.append(rec)
            continue
        try:
            rng, atr_avg, vr = volatility_signals(w15)
            if volatility_extreme(rng, atr_avg, vr) or is_stablecoin_peg(float(w15["close"].iloc[-1]), atr_avg):
                rows.append(rec)
                continue
            regime = market_regime(w4)
            if regime == "trend":
                votes, strat = score_trend_following(w1, w15), "trend_following"
            else:
                votes, strat = score_mean_reversion(w15), "mean_reversion"
            score = confluence_score(votes)
            bo = confluence_score(score_breakout(w15))
            if bo >= 2 and bo > score:
                strat, score = "breakout", bo
            rec.update(signal=score >= 2, strategy=strat, score=score,
                       atr15=atr_stop_reference(w15, multiplier=1.0))
        except Exception:
            pass
        rows.append(rec)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulação de portfólio
# ---------------------------------------------------------------------------

@dataclass
class TradeRec:
    symbol: str
    strategy: str
    score: int
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    stop_pct: float
    take_pct: float | None
    reason: str
    hours_open: float
    gross_pct: float
    net_pct: float
    notional: float
    net_usd: float


def _macro_blocked_hours() -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Janelas de bloqueio FOMC/CPI, mesma regra de core/risk_rules.in_macro_risk_window
    (lida direto do JSON pra não depender de settings/.env). O calendário só tem 2026:
    períodos anteriores ficam sem bloqueio."""
    path = HERE / "config" / "macro_calendar.json"
    if not path.exists():
        return []
    cal = json.loads(path.read_text(encoding="utf-8"))
    before = pd.Timedelta(minutes=cal.get("window_minutes_before", 15))
    after = pd.Timedelta(minutes=cal.get("window_minutes_after", 30))
    out = []
    for ev in cal.get("events", []):
        if ev["type"] == "CPI":
            t = pd.Timestamp(f"{ev['date']} {ev.get('time_et', '08:30')}", tz="America/New_York")
            out.append(((t - before).tz_convert("UTC"), (t + after).tz_convert("UTC")))
        else:
            d = pd.Timestamp(ev.get("end_date", ev["date"]), tz="America/New_York")
            out.append((d.tz_convert("UTC"), (d + pd.Timedelta(days=1)).tz_convert("UTC")))
    return out


def _corr_30d(a: pd.Series, b: pd.Series, until: pd.Timestamp) -> float:
    """Correlação dos retornos diários nos 30 dias antes de `until` (como o
    PortfolioComparisonAgent, que usa velas diárias)."""
    day = until.floor("D")
    ra = a[a.index < day].pct_change().tail(30)
    rb = b[b.index < day].pct_change().tail(30)
    j = pd.concat([ra, rb], axis=1).dropna()
    return float(j.iloc[:, 0].corr(j.iloc[:, 1])) if len(j) >= 15 else float("nan")


def simulate(sigs: dict[str, pd.DataFrame], start: str, end: str, cfg: ExitConfig, args,
             daily: dict[str, pd.Series] | None = None) -> tuple[list[TradeRec], pd.Series]:
    t0, t1 = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    frames = []
    for sym, df in sigs.items():
        d = df[(df["open_time"] >= t0) & (df["open_time"] < t1)].copy()
        d["symbol"] = sym
        frames.append(d)
    allbars = pd.concat(frames).sort_values(["open_time", "symbol"])
    fee = args.fee

    cash = INITIAL_CASH
    positions: dict[str, dict] = {}
    last_close: dict[str, pd.Timestamp] = {}      # anti-recompra: último fechamento por par
    entries_log: dict[str, list[pd.Timestamp]] = {}  # anti-recompra: entradas por par
    macro = _macro_blocked_hours() if args.paridade else []
    day_key, day_start_equity, peak_equity = None, INITIAL_CASH, INITIAL_CASH
    paused_until = None
    daily = daily or {}
    last_price: dict[str, float] = {}
    trades: list[TradeRec] = []
    curve: dict[pd.Timestamp, float] = {}

    def close(sym: str, pos: dict, t, price: float, reason: str) -> None:
        nonlocal cash
        proceeds = pos["qty"] * price * (1 - fee)
        cash += proceeds
        gross = (price / pos["entry"] - 1) * 100
        net_usd = proceeds - pos["cost"]
        last_close[sym] = t
        trades.append(TradeRec(
            symbol=sym, strategy=pos["strategy"], score=pos["score"],
            entry_time=str(pos["t"]), exit_time=str(t), entry_price=pos["entry"], exit_price=price,
            stop_pct=pos["stop_pct"], take_pct=pos["take_pct"], reason=reason,
            hours_open=(t - pos["t"]).total_seconds() / 3600, gross_pct=gross,
            net_pct=net_usd / pos["cost"] * 100, notional=pos["cost"], net_usd=net_usd,
        ))

    for t, bars in allbars.groupby("open_time", sort=True):
        # 1) gestão das posições abertas nesta vela (antes de novas entradas)
        for row in bars.itertuples(index=False):
            last_price[row.symbol] = row.close
            pos = positions.get(row.symbol)
            if pos is None:
                continue
            hit = exit_hit(low=row.low, high=row.high, stop_price=pos["stop"], take_price=pos["take"])
            if hit:
                reason, px = hit
                if reason == "stop_loss" and pos["stop"] > pos["entry"]:
                    reason = "stop_movel_lucro"
                close(row.symbol, pos, t, px, reason)
                del positions[row.symbol]
                continue
            armed = trailing_is_armed(pos["entry"], pos["ref"], cfg)
            hours = (t + _DELTA["15m"] - pos["t"]).total_seconds() / 3600
            if time_exit_due(hours, armed, cfg):
                close(row.symbol, pos, t, row.close, "tempo")
                del positions[row.symbol]
                continue
            if pos["trailing"]:
                pos["ref"], pos["stop"] = update_trailing(
                    entry_price=pos["entry"], reference_price=pos["ref"], stop_price=pos["stop"],
                    initial_stop_pct=pos["stop_pct"], price=row.high, cfg=cfg)

        # 2) patrimônio no fechamento da vela
        equity = cash + sum(p["qty"] * last_price.get(s, p["entry"]) for s, p in positions.items())
        curve[t] = equity
        if t.date() != day_key:
            day_key, day_start_equity = t.date(), equity
        peak_equity = max(peak_equity, equity)

        # v3 (problema 20): travas de perda -- param novas ENTRADAS, nunca a gestão de saída
        if args.pausa_perda_dia and equity < day_start_equity * (1 - args.pausa_perda_dia / 100):
            continue
        if paused_until is not None:
            if t < paused_until:
                continue
            paused_until, peak_equity = None, equity  # volta a operar com um pico novo
        if args.pausa_queda and equity < peak_equity * (1 - args.pausa_queda / 100):
            paused_until = t + pd.Timedelta(days=args.pausa_dias)
            continue
        close_t = t + _DELTA["15m"]
        if macro and any(a <= close_t < b for a, b in macro):
            continue

        # 3) novas entradas (maior score primeiro, como o cycle_runner)
        cands = bars[bars["signal"]].sort_values("score", ascending=False, kind="stable")
        for row in cands.itertuples(index=False):
            if row.symbol in positions or not (row.atr15 > 0):
                continue
            if args.max_posicoes and len(positions) >= args.max_posicoes:
                break
            if args.paridade:  # travas que o bot real já tem (cycle_runner._pair_entry_block_reason)
                lc = last_close.get(row.symbol)
                if lc is not None and (close_t - lc) < pd.Timedelta(hours=4):
                    continue
                recent = [e for e in entries_log.get(row.symbol, []) if close_t - e < pd.Timedelta(hours=24)]
                if len(recent) >= 2:
                    continue
                if daily and row.symbol in daily and any(
                    abs(c) > 0.75 for c in (_corr_30d(daily[row.symbol], daily[o], close_t) for o in positions if o in daily)
                    if not math.isnan(c)
                ):
                    continue
            entry = row.close * (1 + args.slippage)
            if args.stop_atr_1h:  # Fase 3: stop = N x ATR(14) de 1h, com piso/teto próprios
                if not (row.atr1h > 0):
                    continue
                stop_pct = min(max(row.atr1h * args.stop_atr_1h / row.close * 100, args.min_stop), args.stop_atr_teto)
            else:
                stop_pct = row.atr15 * args.committee_stop_atr / row.close * 100
            take_pct = stop_pct * args.committee_rr
            notional = min(equity * args.max_pos_pct, cash * 0.98)
            if args.risco_pct:  # Fase 3: perda no stop ~= risco_pct do patrimônio
                eff_stop = min(max(stop_pct, cfg.min_stop_loss_pct), cfg.max_stop_loss_pct)
                notional = min(notional, equity * args.risco_pct / eff_stop)
            if notional < args.ordem_minima:  # mínimo da Binance (+5% de margem, como o PortfolioComparisonAgent)
                if cash * 0.98 < args.ordem_minima:
                    break
                continue
            lv = initial_levels(entry, stop_pct, take_pct, cfg)
            qty = notional * (1 - fee) / entry
            cash -= notional
            entries_log.setdefault(row.symbol, []).append(close_t)
            positions[row.symbol] = {
                "qty": qty, "entry": entry, "cost": notional, "t": t + _DELTA["15m"],
                "stop": lv.stop_price, "take": lv.take_price, "stop_pct": lv.stop_loss_pct,
                "take_pct": lv.take_profit_pct, "ref": entry, "trailing": args.committee_trailing,
                "strategy": row.strategy, "score": int(row.score),
            }

    # fecha o que sobrou no último preço (marcado como fim_janela; entra nas métricas)
    t_end = allbars["open_time"].max()
    for sym, pos in list(positions.items()):
        close(sym, pos, t_end, last_price.get(sym, pos["entry"]) * (1 - args.slippage), "fim_janela")
    return trades, pd.Series(curve).sort_index()


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def metrics(trades: list[TradeRec], curve: pd.Series) -> dict:
    if not trades:  # mesmas colunas de sempre, pra linha do resumo.csv não sair desalinhada
        nan = float("nan")
        return {"n": 0, "acerto_pct": nan, "media_bruta_pct": nan, "media_liquida_pct": nan, "t_liquido": nan,
                "ganho_medio_pct": nan, "perda_media_pct": nan, "fator_lucro": nan,
                "retorno_total_pct": round((curve.iloc[-1] / INITIAL_CASH - 1) * 100, 2) if len(curve) else 0.0,
                "drawdown_max_pct": nan, "horas_media": nan}
    net = np.array([t.net_pct for t in trades])
    gross = np.array([t.gross_pct for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    sd = net.std(ddof=1) if len(net) > 1 else float("nan")
    dd = (curve / curve.cummax() - 1).min() * 100 if len(curve) else 0.0
    usd = np.array([t.net_usd for t in trades])
    return {
        "n": len(trades),
        "acerto_pct": round(len(wins) / len(net) * 100, 2),
        "media_bruta_pct": round(gross.mean(), 3),
        "media_liquida_pct": round(net.mean(), 3),
        "t_liquido": round(net.mean() / (sd / math.sqrt(len(net))), 2) if sd and sd > 0 else float("nan"),
        "ganho_medio_pct": round(wins.mean(), 3) if len(wins) else 0.0,
        "perda_media_pct": round(losses.mean(), 3) if len(losses) else 0.0,
        "fator_lucro": round(usd[usd > 0].sum() / -usd[usd <= 0].sum(), 3) if (usd <= 0).any() and usd[usd <= 0].sum() < 0 else float("inf"),
        "retorno_total_pct": round((curve.iloc[-1] / INITIAL_CASH - 1) * 100, 2) if len(curve) else 0.0,
        "drawdown_max_pct": round(dd, 2),
        "horas_media": round(float(np.mean([t.hours_open for t in trades])), 1),
    }


def breakdown(trades: list[TradeRec], key: str) -> pd.DataFrame:
    df = pd.DataFrame([asdict(t) for t in trades])
    g = df.groupby(key)
    return pd.DataFrame({
        "n": g.size(),
        "acerto_%": g["net_pct"].apply(lambda s: (s > 0).mean() * 100).round(1),
        "media_liq_%": g["net_pct"].mean().round(3),
        "soma_usd": g["net_usd"].sum().round(2),
    }).sort_values("n", ascending=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--janela", default="ajuste", choices=[*WINDOWS, "todas", "validacao", "prova_final"])
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--pares", default="", help="lista separada por vírgula (substitui --top)")
    p.add_argument("--modo", default="rapido", choices=["rapido", "exato"])
    p.add_argument("--tag", default="baseline")
    p.add_argument("--fee", type=float, default=0.001, help="taxa por lado (0.001 = 0,1%%; 0.00075 com BNB)")
    p.add_argument("--slippage", type=float, default=0.0, help="derrapagem por lado (fração)")
    # aproximação do RiskCommitteeAgent
    p.add_argument("--committee-stop-atr", type=float, default=1.5, help="stop proposto = N x ATR(14) do 15m")
    p.add_argument("--committee-rr", type=float, default=2.0, help="alvo proposto = N x stop")
    p.add_argument("--committee-sem-trailing", dest="committee_trailing", action="store_false")
    # saídas (Fase 2) -- padrões = comportamento atual
    p.add_argument("--trailing-ativacao", type=float, default=0.0)
    p.add_argument("--trailing-distancia", type=float, default=None)
    p.add_argument("--sem-alvo", action="store_true")
    p.add_argument("--breakeven", action="store_true")
    p.add_argument("--max-horas", type=float, default=None)
    p.add_argument("--min-stop", type=float, default=2.0)
    p.add_argument("--max-stop", type=float, default=10.0)
    p.add_argument("--min-alvo", type=float, default=4.0)
    p.add_argument("--max-alvo", type=float, default=10.0)
    # Fase 3 -- stop por volatilidade e tamanho por risco
    p.add_argument("--stop-atr-1h", type=float, default=None, help="stop = N x ATR(14) de 1h (substitui --committee-stop-atr)")
    p.add_argument("--stop-atr-teto", type=float, default=8.0, help="teto do stop por ATR (%%)")
    p.add_argument("--risco-pct", type=float, default=None, help="tamanho tal que o stop custe N%% do patrimônio")
    # Fase 4 -- filtros de entrada
    p.add_argument("--filtro-btc", action="store_true", help="só entra com BTC 4h acima da EMA50 e EMA50 subindo (6 velas)")
    p.add_argument("--filtro-mercado", default="", choices=["", "4h", "diario", "semanal", "4h+diario"],
                   help="filtro de mercado pelo BTC (substitui --filtro-btc): 4h = o de hoje")
    p.add_argument("--max-medo-ganancia", type=float, default=0.0,
                   help="só entra com o índice de Medo e Ganância <= N (0 = desligado)")
    p.add_argument("--max-funding", type=float, default=None,
                   help="só entra com o funding do próprio par <= X (fração; 0.0003 = 0,03%%)")
    p.add_argument("--max-funding-btc", type=float, default=None,
                   help="só entra com a média de ~3 dias do funding do BTC <= X")
    p.add_argument("--sinal", default="15m", choices=["15m", "1h"], help="timeframe do gatilho de entrada")
    p.add_argument("--min-preco", type=float, default=0.0)
    p.add_argument("--min-volume-usd", type=float, default=0.0, help="volume de 24h mínimo em US$")
    p.add_argument("--bloquear", default="", help="pares proibidos, ex: KITE,AUDIO,MUBARAK")
    # paridade com o bot real e v3
    p.add_argument("--sem-paridade", dest="paridade", action="store_false",
                   help="desliga pausa de 4h por par, máx. 2 entradas/24h, correlação > 0,75 e FOMC/CPI")
    p.add_argument("--capital", type=float, default=10_000.0, help="patrimônio inicial (US$)")
    p.add_argument("--ordem-minima", type=float, default=10.0, help="ordem mínima (US$); 5.25 = mínimo da Binance + 5%%")
    p.add_argument("--max-pos-pct", type=float, default=MAX_POSITION_PCT, help="fração máx. do patrimônio por posição")
    p.add_argument("--max-posicoes", type=int, default=0, help="nº máx. de posições abertas (0 = sem limite)")
    p.add_argument("--sem-reversao", action="store_true", help="desliga a reversão à média")
    p.add_argument("--rompimento-volume-livre", action="store_true",
                   help="rompimento não é barrado pelo filtro de volume > 3x")
    p.add_argument("--score-min", type=int, default=2, help="confluência mínima (2 = hoje)")
    p.add_argument("--pausa-perda-dia", type=float, default=0.0, help="sem novas entradas no dia após perder N%%")
    p.add_argument("--pausa-queda", type=float, default=0.0,
                   help="caindo N%% do pico, pausa novas entradas por --pausa-dias e recomeça o pico")
    p.add_argument("--pausa-dias", type=float, default=7.0)
    return p.parse_args(argv)


def exit_config_from(args) -> ExitConfig:
    return ExitConfig(
        min_stop_loss_pct=args.min_stop, max_stop_loss_pct=args.max_stop,
        min_take_profit_pct=args.min_alvo, max_take_profit_pct=args.max_alvo,
        trailing_activation_pct=args.trailing_ativacao, trailing_distance_pct=args.trailing_distancia,
        take_profit_enabled=not args.sem_alvo, breakeven_on_trailing=args.breakeven,
        max_hold_hours=args.max_horas, fee_per_side=args.fee,
    )


def _signals_1h_on_15m(d: dict[str, pd.DataFrame], **kw) -> pd.DataFrame:
    """Fase 4: sinal calculado no fechamento de cada vela de 1h e colocado na
    vela de 15m que fecha junto com ela (as outras 3 ficam sem sinal). A gestão
    de saída continua vela a vela no 15m, como o monitor de 60s do bot."""
    s1 = signals_fast(d["1h"], d["1h"], d["4h"], sig_interval="1h", **kw)
    base = d["15m"][["open_time", "close", "high", "low"]].copy()
    base["_close_t"] = base["open_time"] + _DELTA["15m"]
    s1 = s1.drop(columns=["close", "high", "low"]).assign(_close_t=s1["open_time"] + _DELTA["1h"]).drop(columns=["open_time"])
    out = base.merge(s1, on="_close_t", how="left").drop(columns=["_close_t"])
    out["signal"] = out["signal"].eq(True)
    out["score"] = out["score"].fillna(0).astype(int)
    out["strategy"] = out["strategy"].fillna("")
    # ATRs e volume vêm "como estavam" até a próxima vela de 1h
    for col in ("atr15", "atr1h", "adx4", "quote_vol_24h"):
        out[col] = out[col].ffill()
    return out


def _btc_uptrend_on(times_15m: pd.Series, start: str, end: str) -> pd.Series:
    btc = load_klines("BTCUSDT", "4h", start, end)
    ema50 = ta.ema(btc["close"], length=50)
    ok = (btc["close"] > ema50) & (ema50 > ema50.shift(6))
    return _as_of(btc, "4h", {"ok": ok.astype(float)}, times_15m)["ok"].eq(1.0).to_numpy()


def _btc_daily() -> pd.Series:
    """Fechamentos diários do BTC com histórico longo (pra médias de 50 dias e 20
    semanas). Reaproveita o cache diário do run_backtest_momentum.py."""
    path = CACHE_DIR / "BTCUSDT_1d_2022-09-01_2026-09-28.csv.gz"
    if path.exists():
        df = pd.read_csv(path)
    else:
        from core.binance_client import binance_client
        t1 = pd.Timestamp("2026-09-28", tz="UTC")
        n = int((t1 - pd.Timestamp("2022-09-01", tz="UTC")).days) + 2
        df = binance_client.get_klines_df_window("BTCUSDT", "1d", n, t1.to_pydatetime())
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.strftime("%Y-%m-%d")
        CACHE_DIR.mkdir(exist_ok=True)
        df.to_csv(path, index=False, compression="gzip")
    idx = pd.to_datetime(df["open_time"].astype(str).str[:10]).dt.tz_localize("UTC")
    return pd.Series(df["close"].astype(float).to_numpy(), index=idx).sort_index()


def _market_ok_on(kind: str, times_15m: pd.Series, start: str, end: str) -> np.ndarray:
    """Filtro de mercado (BTC) avaliado no fechamento de cada vela de 15m, só com
    velas já fechadas. kind: 4h (o de hoje) | diario | semanal | 4h+diario."""
    out = np.ones(len(times_15m), dtype=bool)
    if "4h" in kind:
        out &= _btc_uptrend_on(times_15m, start, end)
    if kind in ("diario", "4h+diario", "semanal"):
        d = _btc_daily()
        if kind == "semanal":
            w = d.resample("W-SUN").last()
            ok = (w > w.rolling(20).mean()).astype(float)
            # rótulo W-SUN = domingo (dia da última vela diária da semana); a semana fecha 1 dia depois
            hi = pd.DataFrame({"close_time": w.index + pd.Timedelta(days=1), "ok": ok.to_numpy()})
            lo = pd.DataFrame({"t": times_15m.reset_index(drop=True) + _DELTA["15m"]})
            vals = pd.merge_asof(lo, hi.sort_values("close_time"), left_on="t", right_on="close_time", direction="backward")
        else:
            sma50 = d.rolling(50).mean()
            ok = ((d > sma50) & (sma50 > sma50.shift(10))).astype(float)
            hi = pd.DataFrame({"close_time": d.index + pd.Timedelta(days=1), "ok": ok.to_numpy()})
            lo = pd.DataFrame({"t": times_15m.reset_index(drop=True) + _DELTA["15m"]})
            vals = pd.merge_asof(lo, hi.sort_values("close_time"), left_on="t", right_on="close_time", direction="backward")
        out &= vals["ok"].eq(1.0).to_numpy()
    return out


def _fear_greed_on(times_15m: pd.Series) -> np.ndarray:
    """Índice de Medo e Ganância (alternative.me) em vigor no fechamento de cada
    vela: o valor do dia D é publicado às 00:00 UTC de D. Sem dado = NaN.
    Fonte: Alternative.me Crypto Fear & Greed Index."""
    path = CACHE_DIR / "fear_greed.csv"
    if not path.exists():
        raise SystemExit("Falta backtest_cache/fear_greed.csv -- rode antes: python baixar_dados_extras.py")
    fg = pd.read_csv(path)
    hi = pd.DataFrame({"t_pub": pd.to_datetime(fg["date"]).dt.tz_localize("UTC"), "fg": fg["value"].astype(float)})
    lo = pd.DataFrame({"t": times_15m.reset_index(drop=True) + _DELTA["15m"]})
    return pd.merge_asof(lo, hi.sort_values("t_pub"), left_on="t", right_on="t_pub", direction="backward")["fg"].to_numpy()


def _funding_on(symbol: str, times_15m: pd.Series, avg_n: int = 1) -> np.ndarray:
    """Funding já liquidado no fechamento de cada vela (média das últimas `avg_n`
    liquidações; 9 = ~3 dias). Par sem futuros/sem arquivo = NaN."""
    path = CACHE_DIR / f"{symbol}_funding.csv"
    if not path.exists():
        return np.full(len(times_15m), np.nan)
    f = pd.read_csv(path)
    # format="ISO8601": a Binance às vezes devolve o horário com milissegundos (16:00:00.019)
    f["funding_time"] = pd.to_datetime(f["funding_time"], utc=True, format="ISO8601")
    f = f.sort_values("funding_time")
    val = f["funding_rate"].rolling(avg_n, min_periods=avg_n).mean()
    hi = pd.DataFrame({"ft": f["funding_time"], "fr": val})
    lo = pd.DataFrame({"t": times_15m.reset_index(drop=True) + _DELTA["15m"]})
    return pd.merge_asof(lo, hi, left_on="t", right_on="ft", direction="backward")["fr"].to_numpy()


def _apply_entry_filters(sigs: dict[str, pd.DataFrame], start: str, end: str, args) -> None:
    """Fase 4: filtros aplicados sobre o sinal do scanner (não mexem em saída)."""
    blocked = {b.strip().upper() + ("" if b.strip().upper().endswith("USDT") else "USDT")
               for b in args.bloquear.split(",") if b.strip()}
    for sym in list(sigs):
        if sym in blocked:
            del sigs[sym]
            continue
        df = sigs[sym]
        keep = df["signal"].to_numpy().copy()
        if args.score_min > 2:
            keep &= (df["score"] >= args.score_min).to_numpy()
        if args.min_preco:
            keep &= (df["close"] >= args.min_preco).to_numpy()
        if args.min_volume_usd:
            keep &= (df["quote_vol_24h"] >= args.min_volume_usd).to_numpy()
        if args.filtro_mercado:
            keep &= _market_ok_on(args.filtro_mercado, df["open_time"], start, end)
        # Dados extras (29/09). Sem dado disponível = NÃO filtra (o par/dia segue a regra base).
        if args.max_medo_ganancia:
            fg = _fear_greed_on(df["open_time"])
            keep &= ~(fg > args.max_medo_ganancia)
        if args.max_funding is not None:
            fr = _funding_on(sym, df["open_time"])
            keep &= ~(fr > args.max_funding + 1e-12)
        if args.max_funding_btc is not None:
            frb = _funding_on("BTCUSDT", df["open_time"], avg_n=9)
            keep &= ~(frb > args.max_funding_btc + 1e-12)
        elif args.filtro_btc:
            keep &= _btc_uptrend_on(df["open_time"], start, end)
        df["signal"] = keep


def run_window(name: str, symbols: list[str], cfg: ExitConfig, args) -> dict:
    start, end = WINDOWS[name]
    sigs: dict[str, pd.DataFrame] = {}
    daily: dict[str, pd.Series] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            d = {iv: load_klines(sym, iv, start, end) for iv in INTERVALS}
            if min(len(x) for x in d.values()) < 60:
                print(f"  [{i}/{len(symbols)}] {sym}: histórico insuficiente, pulado")
                continue
            kw = {"use_mean_reversion": not args.sem_reversao, "breakout_volume_exempt": args.rompimento_volume_livre}
            if args.sinal == "1h":
                sigs[sym] = _signals_1h_on_15m(d, **kw)
            else:
                sigs[sym] = signals_fast(d["15m"], d["1h"], d["4h"], **kw)
            daily[sym] = d["1h"].set_index("open_time")["close"].resample("1D").last()
            if args.modo == "exato":
                fast = sigs[sym]
                sigs[sym] = signals_exact(d["15m"], d["1h"], d["4h"])
                inside = (fast["open_time"] >= pd.Timestamp(start, tz="UTC")).to_numpy()
                a, b = fast["signal"].to_numpy()[inside], sigs[sym]["signal"].to_numpy()[inside]
                both, either = int((a & b).sum()), int((a | b).sum())
                print(f"  [{i}/{len(symbols)}] {sym}: concordância rápido x exato nos sinais = "
                      f"{both}/{either} ({(both / either * 100 if either else 100):.0f}%)")
            print(f"  [{i}/{len(symbols)}] {sym}: {int(sigs[sym]['signal'].sum())} sinais")
        except Exception as exc:
            print(f"  [{i}/{len(symbols)}] {sym}: erro ({exc!r}), pulado")
    _apply_entry_filters(sigs, start, end, args)
    if not sigs:
        raise SystemExit(
            f"Nenhum par com dados na janela {name}. Se todos deram 'erro ... pulado' acima, a busca na Binance "
            "falhou (rode `python check_binance_connection.py`); confira também se o comando foi rodado de dentro de bot/."
        )
    trades, curve = simulate(sigs, start, end, cfg, args, daily)
    m = metrics(trades, curve)

    RESULTS_DIR.mkdir(exist_ok=True)
    if trades:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(RESULTS_DIR / f"{args.tag}_{name}_trades.csv", index=False)
    row = {"quando": dt.datetime.now().isoformat(timespec="seconds"), "tag": args.tag, "janela": name,
           "pares": len(sigs), "modo": args.modo, "fee": args.fee, **m,
           "config": json.dumps({k: v for k, v in vars(args).items() if k not in ("tag", "janela", "pares")})}
    summary = RESULTS_DIR / "resumo.csv"
    pd.DataFrame([row]).to_csv(summary, mode="a", header=not summary.exists(), index=False)

    print(f"\n=== {args.tag} | janela {name} ({start} -> {end}) | {len(sigs)} pares | taxa {args.fee*100:.3f}%/lado ===")
    for k, v in m.items():
        print(f"  {k:20s} {v}")
    if trades:
        print("\n  por motivo de saída:\n" + breakdown(trades, "reason").to_string().replace("\n", "\n  "))
        print("\n  por estratégia:\n" + breakdown(trades, "strategy").to_string().replace("\n", "\n  "))
    return m


def main(argv=None) -> None:
    args = parse_args(argv)
    global INITIAL_CASH
    INITIAL_CASH = args.capital
    symbols = load_universe(args.top, [s.strip().upper() for s in args.pares.split(",") if s.strip()])
    cfg = exit_config_from(args)
    names = {"todas": ["ajuste", "val1", "val2"], "validacao": ["val1", "val2"],
             "prova_final": ["final1", "final2"]}.get(args.janela, [args.janela])
    return names_run(names, symbols, cfg, args)


def names_run(names, symbols, cfg, args):
    out = {}
    if any(n != "ajuste" for n in names):
        print("Aviso: janelas de validação servem só pra CONFIRMAR uma configuração já escolhida na janela 'ajuste'.")
    print(f"Universo: {len(symbols)} pares | saída: {cfg}")
    for n in names:
        out[n] = run_window(n, symbols, cfg, args)
    return out


if __name__ == "__main__":
    main()

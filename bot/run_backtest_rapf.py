"""Backtest standalone do setup "RAPF" (Retomada Após Perda de Fluidez) que o
Ivan trouxe de um vídeo do YouTube -- swing trade fractal/multi-timeframe,
tendência maior manda, gatilho de reversão na escala menor.

Diferente das outras duas estratégias já testadas (comitê de confluência e
Kotegawa/reversão à média), este é um setup de CONTINUAÇÃO de tendência: a
tendência maior está clara, o preço faz um pullback que "assusta" (perde as
médias na escala menor), e a entrada é na retomada -- comprando o medo de
quem vendeu no fundo.

Regras implementadas (fiéis ao resumo, com definições operacionais explícitas
onde o resumo era qualitativo):

  1. Contexto (escala MAIOR): tendência de alta = EMA9 > EMA21 > EMA50;
     tendência de baixa = EMA9 < EMA21 < EMA50. Sem alinhamento claro, não
     opera. Usa o último candle da escala maior já FECHADO até o momento
     (sem lookahead).
  2. Perda de fluidez (escala MENOR): depois que a escala menor também
     estava alinhada a favor da tendência maior, o preço fecha abaixo da
     EMA50 -- "perde as médias".
  3. Retomada / candle gatilho: a partir daí, o primeiro candle que fecha
     de volta acima da EMA21 E é um candle de alta (close > open) vira
     candle gatilho.
  4. Filtros de confirmação (força + volume), LIGADOS por padrão mas
     testáveis desligados via CLI: candle gatilho precisa ter amplitude >=
     1.5x a amplitude média recente E volume >= 1.5x o volume médio recente
     -- senão o candle é descartado e o script continua procurando um
     candle gatilho melhor.
  5. Entrada: rompimento da MÁXIMA do candle gatilho (ordem stop de compra),
     em qualquer candle seguinte -- não entra no próprio candle gatilho.
  6. Stop: mínima entre (mínima do candle gatilho) e (menor mínima desde
     que perdeu fluidez) -- "logo abaixo do fundo recém-formado".
  7. Alvo: risco x REWARD_RISK (2 a 3x sugerido pela fonte; default 2.5,
     configurável).

A subconta do bot é Binance SPOT -- setups de VENDA (tendência de baixa) são
só informativos aqui, não seriam executados de verdade.

Uso:
    python run_backtest_rapf.py [SYMBOL] [BIG_INTERVAL] [SMALL_INTERVAL] [NUM_SMALL_CANDLES] [DAYS_AGO_END] [REWARD_RISK] [REQUIRE_FILTERS]

    python run_backtest_rapf.py BTCUSDT 1d 4h 1000              # swing diário/4h, filtros ligados
    python run_backtest_rapf.py BTCUSDT 1w 1d 500                # semanal/diário, fiel ao outro par que a fonte cita
    python run_backtest_rapf.py BTCUSDT 1d 4h 1000 0 2.5 0       # mesma coisa, filtros de força/volume desligados
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta

from core.binance_client import binance_client

MAX_POSITION_PCT = 0.5  # mesma regra de capital do spec (máx 50%/operação)
COMMISSION_PCT = 0.001
INITIAL_CASH = 10_000.0
STRENGTH_MULT = 1.5   # candle gatilho precisa ter amplitude >= 1.5x a média recente
VOLUME_MULT = 1.5     # e volume >= 1.5x a média recente
LOOKBACK_AVG = 14     # janela pra calcular amplitude/volume médios recentes

SCRIPT_VERSION = "2026-09-16-v1"

_INTERVAL_DELTA = {
    "15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(weeks=1),
}


@dataclass
class RTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    direction: str  # "long" | "short"
    reason: str  # "stop_loss" | "take_profit"
    pnl_pct: float


@dataclass
class RResult:
    symbol: str
    trades: list[RTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    triggers_seen: int = 0
    triggers_filtered_out: int = 0

    @property
    def total_return_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        return (self.equity_curve[-1] / self.equity_curve[0] - 1) * 100

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            max_dd = min(max_dd, (e / peak - 1) * 100)
        return max_dd

    @property
    def win_rate_pct(self) -> float:
        if not self.trades:
            return float("nan")
        return sum(1 for t in self.trades if t.pnl_pct > 0) / len(self.trades) * 100

    @property
    def num_trades(self) -> int:
        return len(self.trades)


def _add_emas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"] = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)
    return df


def _closed_before(df: pd.DataFrame, interval: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    delta = _INTERVAL_DELTA[interval]
    return df[df["open_time"] + delta <= cutoff]


def _big_trend(row) -> str:
    if pd.isna(row["ema50"]):
        return "none"
    if row["ema9"] > row["ema21"] > row["ema50"]:
        return "up"
    if row["ema9"] < row["ema21"] < row["ema50"]:
        return "down"
    return "none"


def run(symbol: str, big_interval: str, small_interval: str, num_small_candles: int,
        end_time: dt.datetime, reward_risk: float, require_filters: bool) -> RResult:
    big_span_needed = num_small_candles * _INTERVAL_DELTA[small_interval] / _INTERVAL_DELTA[big_interval]
    num_big = int(big_span_needed) + 60  # + folga pra EMA50 ter histórico logo no início

    print(f"Buscando {num_small_candles} candles de {small_interval} e ~{num_big} de {big_interval} "
          f"de {symbol} (janela terminando em {end_time.isoformat()})...")
    df_small = binance_client.get_klines_df_window(symbol, small_interval, num_small_candles, end_time)
    df_big = binance_client.get_klines_df_window(symbol, big_interval, num_big, end_time)

    df_small = _add_emas(df_small)
    df_big = _add_emas(df_big)

    result = RResult(symbol=symbol)
    cash = INITIAL_CASH
    equity = cash
    position: dict | None = None
    # state: "idle" | "aligned" | "lost_fluidity" | "armed"
    state = "idle"
    context_dir: str | None = None
    min_low_since_lost = None
    trigger: dict | None = None  # {high, low, idx}

    start = 55  # EMA50 + folga
    for i in range(start, len(df_small)):
        row = df_small.iloc[i]
        now = row["open_time"]
        price, high, low = float(row["close"]), float(row["high"]), float(row["low"])

        big_closed = _closed_before(df_big, big_interval, now)
        if len(big_closed) < 55:
            result.equity_curve.append(equity)
            continue
        big_trend = _big_trend(big_closed.iloc[-1])

        # --- gestão de posição aberta ---
        if position is not None:
            exit_price, reason = None, None
            if position["direction"] == "long":
                if low <= position["stop_price"]:
                    exit_price, reason = position["stop_price"], "stop_loss"
                elif high >= position["take_price"]:
                    exit_price, reason = position["take_price"], "take_profit"
            else:
                if high >= position["stop_price"]:
                    exit_price, reason = position["stop_price"], "stop_loss"
                elif low <= position["take_price"]:
                    exit_price, reason = position["take_price"], "take_profit"

            if exit_price is not None:
                if position["direction"] == "long":
                    pnl_pct = (exit_price / position["entry_price"] - 1) * 100
                    cash += position["qty"] * exit_price * (1 - COMMISSION_PCT)
                    equity = cash
                else:
                    pnl_pct = (1 - exit_price / position["entry_price"]) * 100
                    equity = cash
                result.trades.append(RTrade(
                    entry_time=position["entry_time"], exit_time=now,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    direction=position["direction"], reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
                state, context_dir, trigger, min_low_since_lost = "idle", None, None, None
            else:
                if position["direction"] == "long":
                    equity = cash + position["qty"] * price
            result.equity_curve.append(equity)
            continue

        # contexto maior mudou/sumiu -- invalida qualquer setup em andamento
        if state != "idle" and big_trend != context_dir:
            state, context_dir, trigger, min_low_since_lost = "idle", None, None, None

        small_dir = None
        if row["ema9"] > row["ema21"] > row["ema50"]:
            small_dir = "up"
        elif row["ema9"] < row["ema21"] < row["ema50"]:
            small_dir = "down"

        if state == "idle":
            if big_trend in ("up", "down") and small_dir == big_trend:
                state, context_dir = "aligned", big_trend

        elif state == "aligned":
            lost = (context_dir == "up" and price < row["ema50"]) or \
                   (context_dir == "down" and price > row["ema50"])
            if lost:
                state = "lost_fluidity"
                min_low_since_lost = low if context_dir == "up" else high

        elif state == "lost_fluidity":
            min_low_since_lost = (min(min_low_since_lost, low) if context_dir == "up"
                                   else max(min_low_since_lost, high))
            reclaimed = (context_dir == "up" and price > row["ema21"] and price > row["open"]) or \
                        (context_dir == "down" and price < row["ema21"] and price < row["open"])
            if reclaimed:
                result.triggers_seen += 1
                ok = True
                if require_filters:
                    recent = df_small.iloc[max(0, i - LOOKBACK_AVG):i]
                    avg_range = (recent["high"] - recent["low"]).mean()
                    avg_vol = recent["volume"].mean()
                    candle_range = high - low
                    ok = (avg_range > 0 and candle_range >= STRENGTH_MULT * avg_range and
                          avg_vol > 0 and row["volume"] >= VOLUME_MULT * avg_vol)
                if ok:
                    stop_ref = min(low, min_low_since_lost) if context_dir == "up" else max(high, min_low_since_lost)
                    trigger = {"high": high, "low": low, "stop_ref": stop_ref}
                    state = "armed"
                else:
                    result.triggers_filtered_out += 1

        elif state == "armed":
            if context_dir == "up":
                if high >= trigger["high"]:
                    entry_price = trigger["high"]
                    stop_price = trigger["stop_ref"]
                    risk = entry_price - stop_price
                    if risk > 0:
                        take_price = entry_price + risk * reward_risk
                        notional = min(cash, equity * MAX_POSITION_PCT)
                        qty = (notional * (1 - COMMISSION_PCT)) / entry_price
                        cash -= notional
                        equity = cash + qty * entry_price
                        position = {"qty": qty, "entry_price": entry_price, "entry_time": now,
                                    "stop_price": stop_price, "take_price": take_price, "direction": "long"}
                    state, context_dir, trigger, min_low_since_lost = "idle", None, None, None
                elif low <= trigger["stop_ref"]:
                    state, context_dir, trigger, min_low_since_lost = "idle", None, None, None
            else:
                if low <= trigger["low"]:
                    entry_price = trigger["low"]
                    stop_price = trigger["stop_ref"]
                    risk = stop_price - entry_price
                    if risk > 0:
                        take_price = entry_price - risk * reward_risk
                        position = {"qty": 0.0, "entry_price": entry_price, "entry_time": now,
                                    "stop_price": stop_price, "take_price": take_price, "direction": "short"}
                    state, context_dir, trigger, min_low_since_lost = "idle", None, None, None
                elif high >= trigger["stop_ref"]:
                    state, context_dir, trigger, min_low_since_lost = "idle", None, None, None

        result.equity_curve.append(equity)

    if position is not None and position["direction"] == "long":
        last_price = float(df_small["close"].iloc[-1])
        final_equity = cash + position["qty"] * last_price * (1 - COMMISSION_PCT)
        if result.equity_curve:
            result.equity_curve[-1] = final_equity

    return result


def main() -> None:
    print(f"[run_backtest_rapf.py versão: {SCRIPT_VERSION}]")
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    big_interval = sys.argv[2] if len(sys.argv) > 2 else "1d"
    small_interval = sys.argv[3] if len(sys.argv) > 3 else "4h"
    num_small = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
    days_ago = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    reward_risk = float(sys.argv[6]) if len(sys.argv) > 6 else 2.5
    require_filters = bool(int(sys.argv[7])) if len(sys.argv) > 7 else True

    end_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    result = run(symbol, big_interval, small_interval, num_small, end_time, reward_risk, require_filters)

    print()
    print("=== Backtest: RAPF (Retomada Após Perda de Fluidez) ===")
    print(f"Par:                {result.symbol}")
    print(f"Escalas:            {big_interval} (contexto) / {small_interval} (gatilho)")
    print(f"Reward:risk:        1:{reward_risk}")
    print(f"Filtros força/vol:  {'ligados' if require_filters else 'desligados'}")
    print(f"Tamanho máx/op:     {MAX_POSITION_PCT:.0%} do equity")
    print(f"Candles gatilho:    {result.triggers_seen} "
          f"({result.triggers_filtered_out} descartados pelos filtros)" if require_filters else
          f"Candles gatilho:    {result.triggers_seen}")
    print(f"Retorno total:      {result.total_return_pct:.2f}%  (só COMPRA mexe no caixa -- ver nota)")
    print(f"Drawdown máximo:    {result.max_drawdown_pct:.2f}%")
    print(f"Win rate (geral):   {result.win_rate_pct:.2f}%")
    print(f"Número de trades:   {result.num_trades}")

    if result.trades:
        print()
        print("--- Por direção ---")
        for direction in ("long", "short"):
            trades = [t for t in result.trades if t.direction == direction]
            if not trades:
                continue
            wins = sum(1 for t in trades if t.pnl_pct > 0)
            avg = sum(t.pnl_pct for t in trades) / len(trades)
            total = sum(t.pnl_pct for t in trades)
            print(f"  {direction:6s} n={len(trades):3d}  win_rate={wins / len(trades) * 100:5.1f}%  "
                  f"avg_pnl={avg:+6.2f}%  soma_pnl={total:+7.2f}%")

    print()
    print("Nota: a subconta do bot é Binance SPOT (sem margem/short) -- setups de venda")
    print("(tendência de baixa) aqui são só informativos; nenhuma operação short seria")
    print("executada de verdade pelo ExecutionAgent.")


if __name__ == "__main__":
    main()

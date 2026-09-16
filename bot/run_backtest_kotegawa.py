"""Backtest standalone da estratégia de reversão à média "estilo Kotegawa"
(BNF) que o Ivan trouxe de um resumo de YouTube -- variação de mean reversion
baseada em Bollinger Bands(20, 3 desvios) + confirmação por candle de
price action, com stop curto no candle de sinal e alvo na banda central.

Diferente de run_backtest_multi_tf.py (que testa a lógica de comitê já
implementada no MarketScannerAgent), este script testa essa estratégia NOVA
e ISOLADA, fiel ao resumo passado pelo Ivan, pra decidir com dado -- antes de
mexer em qualquer código de produção -- se vale a pena incorporar ao bot.

Tensão real com a arquitetura atual: a fonte recomenda gráfico DIÁRIO pra
maior assertividade ("regra de ouro" do resumo -- diz que timeframes
intradiários tem sinal mais frequente só que de qualidade pior), mas o bot
roda em ciclos de 15 min. Por isso o script aceita o timeframe como
parâmetro -- dá pra rodar "1d" (fiel à fonte) e "4h"/"1h" (mais compatível
com o ritmo do bot) lado a lado e comparar.

Regras implementadas:
  1. Alerta: candle fecha fora da banda de Bollinger (20, N desvios).
  2. Confirmação: o candle SEGUINTE precisa ser um padrão de reversão
     (engolfo, martelo/estrela cadente, ou "candle de força" na direção do
     sinal) -- senão o alerta expira (e esse mesmo candle seguinte já é
     reavaliado como um novo alerta em potencial, não fica preso esperando).
  3. Stop: mínima (compra) / máxima (venda) do candle de ALERTA (não do de
     confirmação).
  4. Alvo: banda central (SMA 20) no momento da entrada -- simplificação:
     fixamos o alvo no valor da média no momento da entrada, em vez de
     acompanhar a média se deslocando candle a candle (mais simples de
     simular; tende a ser conservador, já que a média real se moveria a
     favor do trade na maioria dos casos).

A subconta do bot é Binance SPOT (sem margem/short) -- os sinais de VENDA
(topo, reversão pra baixo) são simulados só de forma informativa aqui, pra
avaliar se a lógica capta reversões de topo tão bem quanto as de fundo, mas
nenhuma operação short seria executada de verdade pelo ExecutionAgent.

Uso:
    python run_backtest_kotegawa.py [SYMBOL] [INTERVAL] [NUM_CANDLES] [DAYS_AGO_END] [BB_STD]

    python run_backtest_kotegawa.py BTCUSDT 1d 730        # ~2 anos de diário
    python run_backtest_kotegawa.py BTCUSDT 4h 1000       # ~166 dias de 4h
    python run_backtest_kotegawa.py ETHUSDT 1d 730 0 3.0
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta

from core.binance_client import binance_client
from core.indicators import _col

MAX_POSITION_PCT = 0.5  # mesma regra de capital do spec (máx 50%/operação)
COMMISSION_PCT = 0.001
INITIAL_CASH = 10_000.0
BB_LENGTH = 20

SCRIPT_VERSION = "2026-09-16-v1"


@dataclass
class KTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    direction: str  # "long" | "short"
    reason: str  # "stop_loss" | "take_profit"
    pnl_pct: float


@dataclass
class KResult:
    symbol: str
    interval: str
    trades: list[KTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    signals_seen: int = 0
    confirmed: int = 0

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


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _is_bullish_engulfing(df: pd.DataFrame, i: int) -> bool:
    o0, c0 = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    o1, c1 = df["open"].iloc[i], df["close"].iloc[i]
    return c0 < o0 and c1 > o1 and o1 <= c0 and c1 >= o0


def _is_bearish_engulfing(df: pd.DataFrame, i: int) -> bool:
    o0, c0 = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    o1, c1 = df["open"].iloc[i], df["close"].iloc[i]
    return c0 > o0 and c1 < o1 and o1 >= c0 and c1 <= o0


def _is_hammer(df: pd.DataFrame, i: int) -> bool:
    o, c, h, low = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    rng = h - low
    if rng <= 0:
        return False
    body = _body(o, c)
    lower_wick = min(o, c) - low
    upper_wick = h - max(o, c)
    return lower_wick >= 2 * body and upper_wick <= 0.3 * rng


def _is_shooting_star(df: pd.DataFrame, i: int) -> bool:
    o, c, h, low = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    rng = h - low
    if rng <= 0:
        return False
    body = _body(o, c)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - low
    return upper_wick >= 2 * body and lower_wick <= 0.3 * rng


def _is_strong_reversal_candle(df: pd.DataFrame, i: int, direction: str) -> bool:
    """"Candle de força": corpo bem maior que a média recente, fechando
    perto do extremo na direção do sinal (pouco pavio contra a direção)."""
    o, c, h, low = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    rng = h - low
    if rng <= 0:
        return False
    body = _body(o, c)
    recent_bodies = (df["close"] - df["open"]).abs().iloc[max(0, i - 14): i]
    avg_body = recent_bodies.mean() if len(recent_bodies) else body
    if avg_body <= 0 or body < 1.5 * avg_body:
        return False
    if direction == "long":
        return c > o and (c - low) / rng >= 0.7
    return c < o and (h - c) / rng >= 0.7


def _confirms(df: pd.DataFrame, i: int, direction: str) -> bool:
    if direction == "long":
        return _is_bullish_engulfing(df, i) or _is_hammer(df, i) or _is_strong_reversal_candle(df, i, "long")
    return _is_bearish_engulfing(df, i) or _is_shooting_star(df, i) or _is_strong_reversal_candle(df, i, "short")


def run(symbol: str, interval: str, num_candles: int, end_time: dt.datetime, bb_std: float) -> KResult:
    print(f"Buscando {num_candles} candles de {interval} de {symbol} "
          f"(janela terminando em {end_time.isoformat()})...")
    df = binance_client.get_klines_df_window(symbol, interval, num_candles, end_time)

    bb = ta.bbands(df["close"], length=BB_LENGTH, std=bb_std)
    df["bb_lower"] = _col(bb, "BBL_")
    df["bb_mid"] = _col(bb, "BBM_")
    df["bb_upper"] = _col(bb, "BBU_")

    result = KResult(symbol=symbol, interval=interval)
    cash = INITIAL_CASH
    equity = cash
    position: dict | None = None  # {qty, entry_price, entry_time, stop_price, take_price, direction}
    pending_alert: dict | None = None  # {idx, direction}

    start = BB_LENGTH + 1
    for i in range(start, len(df)):
        price = float(df["close"].iloc[i])
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        now = df["open_time"].iloc[i]

        if pd.isna(df["bb_mid"].iloc[i]):
            result.equity_curve.append(equity)
            continue

        # --- gestão de posição aberta ---
        if position is not None:
            exit_price = None
            reason = None
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
                    equity = cash  # short é só informativo (spot-only, sem caixa real envolvido)
                result.trades.append(KTrade(
                    entry_time=position["entry_time"], exit_time=now,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    direction=position["direction"], reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
            else:
                if position["direction"] == "long":
                    equity = cash + position["qty"] * price
                # short: sem posição real, equity não muda

        # --- confirmação de alerta pendente (só no candle imediatamente seguinte) ---
        if position is None and pending_alert is not None:
            direction = pending_alert["direction"]
            if _confirms(df, i, direction):
                result.confirmed += 1
                alert_idx = pending_alert["idx"]
                stop_price = (float(df["low"].iloc[alert_idx]) if direction == "long"
                              else float(df["high"].iloc[alert_idx]))
                take_price = float(df["bb_mid"].iloc[i])
                qty = 0.0
                if direction == "long":
                    notional = min(cash, equity * MAX_POSITION_PCT)
                    qty = (notional * (1 - COMMISSION_PCT)) / price
                    cash -= notional
                    equity = cash + qty * price
                position = {
                    "qty": qty, "entry_price": price, "entry_time": now,
                    "stop_price": stop_price, "take_price": take_price, "direction": direction,
                }
            pending_alert = None  # confirmado (virou posição) ou expirou -- de qualquer forma, some

        # --- novo alerta (só se não está em posição nem esperando confirmação) ---
        if position is None and pending_alert is None:
            if price < float(df["bb_lower"].iloc[i]):
                pending_alert = {"idx": i, "direction": "long"}
                result.signals_seen += 1
            elif price > float(df["bb_upper"].iloc[i]):
                pending_alert = {"idx": i, "direction": "short"}
                result.signals_seen += 1

        result.equity_curve.append(equity)

    if position is not None and position["direction"] == "long":
        last_price = float(df["close"].iloc[-1])
        final_equity = cash + position["qty"] * last_price * (1 - COMMISSION_PCT)
        if result.equity_curve:
            result.equity_curve[-1] = final_equity

    return result


def main() -> None:
    print(f"[run_backtest_kotegawa.py versão: {SCRIPT_VERSION}]")
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1d"
    num_candles = int(sys.argv[3]) if len(sys.argv) > 3 else 730
    days_ago = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    bb_std = float(sys.argv[5]) if len(sys.argv) > 5 else 3.0

    end_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    result = run(symbol, interval, num_candles, end_time, bb_std)

    print()
    print("=== Backtest: reversão à média (Bollinger 3σ + confirmação de price action) ===")
    print(f"Par/timeframe:      {result.symbol} ({result.interval})")
    print(f"Bollinger:          {BB_LENGTH} períodos, {bb_std} desvios")
    print(f"Tamanho máx/op:     {MAX_POSITION_PCT:.0%} do equity")
    print(f"Sinais de alerta:   {result.signals_seen}")
    if result.signals_seen:
        print(f"Confirmados:        {result.confirmed} ({result.confirmed / result.signals_seen * 100:.1f}% dos alertas)")
    else:
        print("Confirmados:        0")
    print(f"Retorno total:      {result.total_return_pct:.2f}%  (só os trades de COMPRA mexem no caixa -- ver nota)")
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
    print("Nota: a subconta do bot é Binance SPOT (sem margem/short) -- sinais de venda")
    print("(short) aqui são só informativos, pra ver se a lógica capta reversões de topo")
    print("também; nenhuma operação short seria executada de verdade pelo ExecutionAgent.")
    print("Ainda não inclui NewsAgent/RiskCommitteeAgent (não simuláveis offline).")


if __name__ == "__main__":
    main()

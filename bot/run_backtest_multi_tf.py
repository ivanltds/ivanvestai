"""Backtest multi-timeframe fiel à lógica real do MarketScannerAgent.

Diferente de `run_backtest.py` (que usa a lib `backtesting.py` com um único
timeframe como proxy tanto pra tendência quanto pro timing), este script
busca os 3 timeframes reais (4h, 1h, 15m) e replica exatamente a lógica de
`core.indicators` + `agents.market_scanner_agent`:

  - regime de mercado calculado no 4h (ADX)
  - trend_following (1h + 15m) se regime == trend
  - mean_reversion (15m) se regime == lateral
  - breakout (15m) sobrepõe as anteriores se a confluência de breakout >= 2,
    independente do regime (igual ao scanner real)

É um simulador "walk-forward" escrito à mão (a lib `backtesting.py` não
suporta múltiplos timeframes nativamente) — em cada candle de 15m, só usa
dados de 1h/4h que já fecharam até aquele momento, pra não vazar informação
do futuro (lookahead bias).

Ainda não inclui: o veto do NewsAgent (sentimento) nem o piso de confiança
agregada de 80% do RiskCommitteeAgent -- ambos dependem de chamadas à OpenAI
e não têm como ser simulados aqui de forma determinística. Ou seja, isso
ainda é só a camada técnica, mas agora fiel aos 3 timeframes reais.

Uso:
    python run_backtest_multi_tf.py [SYMBOL] [NUM_CANDLES_15M] [DAYS_AGO_END] [STOP_ATR_MULT]

    python run_backtest_multi_tf.py BTCUSDT 1000              # janela terminando agora
    python run_backtest_multi_tf.py BTCUSDT 1000 75            # janela terminando 75 dias atrás
    python run_backtest_multi_tf.py BTCUSDT 1000 0 2.5         # stop mais largo (ATR x 2.5 em vez de 1.5)

DAYS_AGO_END desloca o "agora" simulado pra trás no tempo -- útil pra testar
se um resultado é específico de um período recente ou se se repete em
janelas mais antigas. STOP_ATR_MULT controla a distância do stop-loss (em
múltiplos de ATR); o take-profit acompanha via REWARD_RISK_RATIO.

Limite prático: a Binance pagina automaticamente (get_historical_klines),
mas janelas muito grandes demoram mais pra buscar.
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field

import pandas as pd

from core.binance_client import binance_client
from core.indicators import (
    atr_stop_reference,
    confluence_score,
    market_regime,
    score_breakout,
    score_mean_reversion,
    score_trend_following,
)

LOOKBACK = 120  # mesmo valor usado pelo MarketScannerAgent real (get_klines_df limit=120)
MIN_15M_HISTORY = 60  # candles mínimos de 15m antes de começar a operar
REWARD_RISK_RATIO = 2.0  # take-profit = risco * esse fator (proxy do RiskCommitteeAgent real)
COMMISSION_PCT = 0.001  # 0.1%, igual ao backtest.py baseline
INITIAL_CASH = 10_000.0  # ver nota em run_backtest.py sobre por que não é literalmente R$250

_INTERVAL_DELTA = {
    "15m": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
}


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    strategy: str
    reason: str
    pnl_pct: float


@dataclass
class MultiTfResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

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
        for equity in self.equity_curve:
            peak = max(peak, equity)
            dd = (equity / peak - 1) * 100
            max_dd = min(max_dd, dd)
        return max_dd

    @property
    def win_rate_pct(self) -> float:
        if not self.trades:
            return float("nan")
        wins = sum(1 for t in self.trades if t.pnl_pct > 0)
        return wins / len(self.trades) * 100

    @property
    def num_trades(self) -> int:
        return len(self.trades)


def _closed_before(df: pd.DataFrame, interval: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Candles desse timeframe que JÁ FECHARAM antes de `cutoff` (sem lookahead)."""
    delta = _INTERVAL_DELTA[interval]
    return df[df["open_time"] + delta <= cutoff]


def fetch_all(symbol: str, num_15m_candles: int, end_time: dt.datetime) -> dict[str, pd.DataFrame]:
    span_hours = num_15m_candles * 15 / 60
    num_1h = int(span_hours) + LOOKBACK + 10
    num_4h = int(span_hours / 4) + LOOKBACK + 10

    print(f"Buscando {num_15m_candles} candles de 15m, {num_1h} de 1h e {num_4h} de 4h "
          f"(janela terminando em {end_time.isoformat()})...")

    df_15m = binance_client.get_klines_df_window(symbol, "15m", num_15m_candles, end_time)
    df_1h = binance_client.get_klines_df_window(symbol, "1h", num_1h, end_time)
    df_4h = binance_client.get_klines_df_window(symbol, "4h", num_4h, end_time)
    return {"15m": df_15m, "1h": df_1h, "4h": df_4h}


def run(
    symbol: str, num_15m_candles: int, end_time: dt.datetime, stop_atr_mult: float
) -> MultiTfResult:
    data = fetch_all(symbol, num_15m_candles, end_time)
    df_15m, df_1h, df_4h = data["15m"], data["1h"], data["4h"]

    result = MultiTfResult(symbol=symbol)
    cash = INITIAL_CASH
    equity = cash
    position: dict | None = None  # {qty, entry_price, entry_time, stop_price, take_price, strategy}

    total_steps = len(df_15m) - MIN_15M_HISTORY
    for i in range(MIN_15M_HISTORY, len(df_15m)):
        if (i - MIN_15M_HISTORY) % 200 == 0:
            print(f"  ... processando candle {i - MIN_15M_HISTORY}/{total_steps}")

        now = df_15m["open_time"].iloc[i]
        window_15m = df_15m.iloc[max(0, i - LOOKBACK + 1): i + 1]
        window_1h = _closed_before(df_1h, "1h", now).tail(LOOKBACK)
        window_4h = _closed_before(df_4h, "4h", now).tail(LOOKBACK)
        price = float(df_15m["close"].iloc[i])
        high = float(df_15m["high"].iloc[i])
        low = float(df_15m["low"].iloc[i])

        # precisa de janela mínima pra calcular os indicadores de tendência
        # (ema50 no 1h, adx no 4h) -- sem isso, só acumula equity e segue
        if len(window_1h) < 55 or len(window_4h) < 20:
            result.equity_curve.append(equity)
            continue

        # --- Gestão de posição aberta (stop/take, igual ExecutionAgent) ---
        if position is not None:
            exit_price = None
            reason = None
            if low <= position["stop_price"]:
                exit_price = position["stop_price"]
                reason = "stop_loss"
            elif high >= position["take_price"]:
                exit_price = position["take_price"]
                reason = "take_profit"

            if exit_price is not None:
                pnl_pct = (exit_price / position["entry_price"] - 1) * 100
                cash = position["qty"] * exit_price * (1 - COMMISSION_PCT)
                equity = cash
                result.trades.append(Trade(
                    entry_time=position["entry_time"], exit_time=now,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    strategy=position["strategy"], reason=reason, pnl_pct=pnl_pct,
                ))
                position = None
            else:
                equity = position["qty"] * price

        # --- Scanner: mesma lógica de agents.market_scanner_agent -----------
        if position is None:
            regime = market_regime(window_4h)
            if regime == "trend":
                votes = score_trend_following(window_1h, window_15m)
                strategy = "trend_following"
            else:
                votes = score_mean_reversion(window_15m)
                strategy = "mean_reversion"

            breakout_votes = score_breakout(window_15m)
            if confluence_score(breakout_votes) >= 2:
                votes = breakout_votes
                strategy = "breakout"

            score = confluence_score(votes)
            if score >= 2:
                atr_stop = atr_stop_reference(window_15m, multiplier=stop_atr_mult)
                stop_price = price - atr_stop
                take_price = price + atr_stop * REWARD_RISK_RATIO
                qty = (cash * (1 - COMMISSION_PCT)) / price
                position = {
                    "qty": qty, "entry_price": price, "entry_time": now,
                    "stop_price": stop_price, "take_price": take_price, "strategy": strategy,
                }
                cash = 0.0
                equity = qty * price

        result.equity_curve.append(equity)

    # fecha posição em aberto no fim do período pro cálculo de equity final
    if position is not None:
        last_price = float(df_15m["close"].iloc[-1])
        final_equity = position["qty"] * last_price * (1 - COMMISSION_PCT)
        if result.equity_curve:
            result.equity_curve[-1] = final_equity

    return result


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    num_candles = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    days_ago = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    stop_atr_mult = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5

    end_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    result = run(symbol, num_candles, end_time, stop_atr_mult)

    print()
    print("=== Resultado do backtest multi-timeframe (fiel ao MarketScannerAgent) ===")
    print(f"Par:                {result.symbol}")
    print(f"Janela terminando:  {end_time.isoformat()} ({days_ago:.0f} dias atrás)")
    print(f"Stop (x ATR):       {stop_atr_mult}")
    print(f"Retorno total:      {result.total_return_pct:.2f}%")
    print(f"Drawdown máximo:    {result.max_drawdown_pct:.2f}%")
    print(f"Win rate:           {result.win_rate_pct:.2f}%")
    print(f"Número de trades:   {result.num_trades}")

    if result.trades:
        by_strategy: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for t in result.trades:
            by_strategy[t.strategy] = by_strategy.get(t.strategy, 0) + 1
            by_reason[t.reason] = by_reason.get(t.reason, 0) + 1
        print()
        print("Trades por estratégia:", dict(by_strategy))
        print("Trades por motivo de saída:", dict(by_reason))

    print()
    print("Nota: ainda não inclui o veto do NewsAgent nem o piso de confiança")
    print("de 80% do RiskCommitteeAgent (ambos dependem de chamadas à OpenAI) --")
    print("é a camada técnica pura, mas agora com os 3 timeframes reais e sem lookahead.")


if __name__ == "__main__":
    main()

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
    adx_last,
    atr_stop_reference,
    confluence_score,
    score_breakout,
    score_trend_following,
)
# score_mean_reversion não é mais usado aqui -- perna desativada (ver nota no
# scanner mais abaixo), mas a função continua em core/indicators.py caso
# alguém queira retestar com dados/regras diferentes no futuro.

LOOKBACK = 120  # mesmo valor usado pelo MarketScannerAgent real (get_klines_df limit=120)
MIN_15M_HISTORY = 60  # candles mínimos de 15m antes de começar a operar
REWARD_RISK_RATIO = 2.0  # take-profit = risco * esse fator (proxy do RiskCommitteeAgent real)
COMMISSION_PCT = 0.001  # 0.1%, igual ao backtest.py baseline
INITIAL_CASH = 10_000.0  # ver nota em run_backtest.py sobre por que não é literalmente R$250
MAX_POSITION_PCT = 0.5  # regra de capital do spec (arquitetura-tecnica.md 3.1, PortfolioComparisonAgent):
                         # no máximo 50% do equity por operação. Versões anteriores deste script apostavam
                         # 100% do caixa em cada trade, o que não reflete a regra real do bot e amplifica
                         # artificialmente o "drag" de composição (uma sequência perda/ganho corrói o
                         # equity muito mais rápido quando cada aposta é o saldo inteiro da conta).

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
    symbol: str = ""
    adx_4h: float = float("nan")
    confluence_score: int = 0


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

    def stats_by(self, key: str) -> dict[str, dict]:
        """Agrupa trades por `strategy` ou `reason` e devolve contagem, win
        rate e pnl médio/total de cada grupo -- usado pra diagnosticar QUAL
        perna da lógica (trend_following/mean_reversion/breakout, ou
        stop_loss/take_profit) está puxando o resultado agregado pra baixo,
        em vez de só olhar o retorno total do teste."""
        groups: dict[str, list[Trade]] = {}
        for t in self.trades:
            groups.setdefault(getattr(t, key), []).append(t)
        out: dict[str, dict] = {}
        for name, trades in groups.items():
            wins = sum(1 for t in trades if t.pnl_pct > 0)
            out[name] = {
                "count": len(trades),
                "win_rate_pct": wins / len(trades) * 100 if trades else float("nan"),
                "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades) if trades else 0.0,
                "total_pnl_pct_sum": sum(t.pnl_pct for t in trades),
            }
        return out


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
                cash += position["qty"] * exit_price * (1 - COMMISSION_PCT)
                equity = cash
                result.trades.append(Trade(
                    entry_time=position["entry_time"], exit_time=now,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    strategy=position["strategy"], reason=reason, pnl_pct=pnl_pct,
                    symbol=symbol, adx_4h=position["adx_4h"], confluence_score=position["confluence_score"],
                ))
                position = None
            else:
                equity = cash + position["qty"] * price

        # --- Scanner: mesma lógica de agents.market_scanner_agent, com
        # ajustes vindos do diagnóstico do sweep de trades poolados (ver
        # seção 9 de arquitetura-tecnica.md):
        #   1. breakout só sobrepõe o regime se ADX(4h) > 25 (tendência de
        #      fundo real) -- em ADX(4h) < 15 o avg_pnl do breakout foi
        #      -0,18% (pior fatia da base original); em ADX(4h) > 30 foi
        #      +0,10%. VALIDADO fora da amostra (janela totalmente
        #      diferente): win rate 48,4%, avg_pnl +0,367% (n=126).
        #   2. mean_reversion DESATIVADO. Testamos exigir confluência 3-de-3
        #      em vez de 2-de-3 -- parecia melhor na amostra original (n=57,
        #      avg +0,029%) mas piorou na validação fora da amostra (n=80,
        #      avg -0,200%). Com o limiar padrão de 2 também ficou negativo
        #      fora da amostra (n=292, avg -0,178%). Ou seja: não é questão
        #      de limiar, a perna inteira (RSI+Bollinger+Stochastic em
        #      regime lateral) não mostrou vantagem validada em nenhuma
        #      configuração testada. Removendo mean_reversion do pool
        #      out-of-sample, o resultado combinado (breakout+trend_following)
        #      sobe pra win rate 38,1%, avg_pnl +0,117%, t=+1,72 -- melhor
        #      que qualquer configuração com mean_reversion incluído. Efeito:
        #      o bot fica de fora quando ADX(4h) <= 25 (mercado sem tendência
        #      de fundo e sem rompimento com força), em vez de tentar pegar
        #      reversão nesse regime.
        # -----------------------------------------------------------------
        if position is None:
            adx_4h_val = adx_last(window_4h)
            votes: list = []
            strategy = ""
            if adx_4h_val > 25:
                votes = score_trend_following(window_1h, window_15m)
                strategy = "trend_following"

                breakout_votes = score_breakout(window_15m)
                if confluence_score(breakout_votes) >= 2:
                    votes = breakout_votes
                    strategy = "breakout"

            score = confluence_score(votes) if votes else 0
            if strategy and score >= 2:
                atr_stop = atr_stop_reference(window_15m, multiplier=stop_atr_mult)
                stop_price = price - atr_stop
                take_price = price + atr_stop * REWARD_RISK_RATIO
                # regra de capital (ver nota da MAX_POSITION_PCT acima): usa no
                # máximo 50% do equity nessa operação, não o caixa inteiro
                notional = min(cash, equity * MAX_POSITION_PCT)
                qty = (notional * (1 - COMMISSION_PCT)) / price
                position = {
                    "qty": qty, "entry_price": price, "entry_time": now,
                    "stop_price": stop_price, "take_price": take_price, "strategy": strategy,
                    "adx_4h": adx_4h_val, "confluence_score": score,
                }
                cash -= notional
                equity = cash + qty * price

        result.equity_curve.append(equity)

    # fecha posição em aberto no fim do período pro cálculo de equity final
    if position is not None:
        last_price = float(df_15m["close"].iloc[-1])
        final_equity = cash + position["qty"] * last_price * (1 - COMMISSION_PCT)
        if result.equity_curve:
            result.equity_curve[-1] = final_equity

    return result


SCRIPT_VERSION = "2026-09-16-v3-sizing50pct"


def main() -> None:
    print(f"[run_backtest_multi_tf.py versão: {SCRIPT_VERSION}]")
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
    print(f"Tamanho máx/op:     {MAX_POSITION_PCT:.0%} do equity (regra de capital do spec)")
    print(f"Stop (x ATR):       {stop_atr_mult}")
    print(f"Retorno total:      {result.total_return_pct:.2f}%")
    print(f"Drawdown máximo:    {result.max_drawdown_pct:.2f}%")
    print(f"Win rate:           {result.win_rate_pct:.2f}%")
    print(f"Número de trades:   {result.num_trades}")

    if result.trades:
        print()
        print("--- Diagnóstico por estratégia (qtd / win rate / pnl médio / soma pnl) ---")
        for name, s in sorted(result.stats_by("strategy").items(), key=lambda kv: -kv[1]["count"]):
            print(f"  {name:18s} n={s['count']:3d}  win_rate={s['win_rate_pct']:5.1f}%  "
                  f"avg_pnl={s['avg_pnl_pct']:+6.2f}%  soma_pnl={s['total_pnl_pct_sum']:+7.2f}%")

        print()
        print("--- Diagnóstico por motivo de saída ---")
        for name, s in sorted(result.stats_by("reason").items(), key=lambda kv: -kv[1]["count"]):
            print(f"  {name:18s} n={s['count']:3d}  win_rate={s['win_rate_pct']:5.1f}%  "
                  f"avg_pnl={s['avg_pnl_pct']:+6.2f}%  soma_pnl={s['total_pnl_pct_sum']:+7.2f}%")

    print()
    print("Nota: ainda não inclui o veto do NewsAgent nem o piso de confiança")
    print("de 80% do RiskCommitteeAgent (ambos dependem de chamadas à OpenAI) --")
    print("é a camada técnica pura, mas agora com os 3 timeframes reais e sem lookahead.")


if __name__ == "__main__":
    main()

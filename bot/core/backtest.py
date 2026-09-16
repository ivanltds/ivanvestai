"""Validação obrigatória das estratégias contra dados históricos da
Binance antes de ligar o bot em produção (settings.bot_status = "running").

MVP: usa a lib `backtesting.py` com uma Strategy que replica a lógica
de confluência de core.indicators para cada tipo de estratégia. Isso é
um ponto de partida — o ideal é rodar por par e por estratégia
separadamente e agregar os resultados antes de decidir ligar o bot.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from backtesting import Backtest, Strategy

from core.indicators import score_breakout, score_mean_reversion, score_trend_following


@dataclass
class BacktestResult:
    strategy: str
    total_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    num_trades: int


class ConfluenceStrategy(Strategy):
    """Estratégia genérica: entra quando confluence_score >= 2, sai no
    stop/take baseado em ATR. Serve como baseline simplificado — o
    comportamento real do comitê (LLM + multi-agente) é mais rico que
    isso, mas o backtest foca em validar a camada técnica quantificável.
    """

    atr_multiplier = 1.5

    def init(self) -> None:
        # backtesting.py exige colunas Open/High/Low/Close/Volume (iniciais
        # maiúsculas), mas os indicadores em core.indicators esperam as
        # colunas minúsculas usadas pelo resto do bot (saída de
        # binance_client.get_klines_df). Mantém as duas versões.
        self.df = self.data.df.rename(columns=str.lower)

    def next(self) -> None:
        if len(self.data) < 60:
            return
        window = self.df.iloc[: len(self.data)]
        votes = score_trend_following(window, window) + score_mean_reversion(window) + score_breakout(window)
        score = sum(v.vote for v in votes)

        if not self.position and score >= 2:
            atr = window["close"].iloc[-1] * 0.02  # aproximação simplificada de ATR% pro baseline
            self.buy(sl=self.data.Close[-1] - atr * self.atr_multiplier)
        elif self.position and score <= -2:
            self.position.close()


def run_backtest(df: pd.DataFrame, strategy_label: str = "confluence_baseline",
                  cash: float = 250.0, commission: float = 0.001) -> BacktestResult:
    bt = Backtest(df, ConfluenceStrategy, cash=cash, commission=commission)
    stats = bt.run()
    return BacktestResult(
        strategy=strategy_label,
        total_return_pct=float(stats["Return [%]"]),
        max_drawdown_pct=float(stats["Max. Drawdown [%]"]),
        win_rate_pct=float(stats.get("Win Rate [%]", 0.0)),
        num_trades=int(stats["# Trades"]),
    )


# Uso pretendido (script manual, roda antes de ligar o bot em produção):
#
#   from core.binance_client import binance_client
#   from core.backtest import run_backtest
#
#   df = binance_client.get_klines_df("BTCUSDT", "15m", limit=1000)
#   df = df.set_index("open_time").rename(columns=str.title)  # backtesting.py espera Open/High/Low/Close/Volume
#   result = run_backtest(df)
#   print(result)
#
# Revisar manualmente total_return_pct / max_drawdown_pct / win_rate_pct
# por par e só então setar settings.bot_status = "running" no dashboard.

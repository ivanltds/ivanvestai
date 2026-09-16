"""Script executável do backtest obrigatório (ver seção 7 de arquitetura-tecnica.md).

Busca klines históricas reais da Binance (endpoint público — não precisa de
credenciais de trading, só de uma API key/secret válidas ou mesmo vazias) e
roda a `ConfluenceStrategy` contra elas via `core.backtest.run_backtest`.

Uso:
    python run_backtest.py                          # BTCUSDT, 15m, 1000 candles (padrão)
    python run_backtest.py ETHUSDT 15m 1500
    python run_backtest.py SOLUSDT 1h 500

Revise manualmente os resultados (retorno %, drawdown, win rate, nº de trades)
antes de considerar ligar o bot com dinheiro real (bot_status = "running").
"""
from __future__ import annotations

import sys

from core.backtest import run_backtest
from core.binance_client import binance_client


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "15m"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 1000

    print(f"Buscando {limit} candles de {symbol} ({interval}) na Binance...")
    df = binance_client.get_klines_df(symbol, interval, limit=limit)

    # backtesting.py espera colunas Open/High/Low/Close/Volume com o índice
    # sendo a data/hora do candle.
    df = df.set_index("open_time").rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )

    # Nota: a lib backtesting.py não compra frações de unidade (limitação dela,
    # ver GH-134) -- com um preço de ativo caro (ex: BTC) e cash=250, ela nunca
    # consegue montar 1 posição e o backtest trava em 0 trades. Usamos um cash
    # bem maior aqui só pra lib conseguir simular execuções e avaliarmos a
    # QUALIDADE do sinal (retorno %, drawdown %, win rate são todos relativos,
    # não dependem do valor absoluto do cash). O bot real compra quantidades
    # fracionárias normalmente via API da Binance (ex: 0.001 BTC) -- isso não
    # afeta a operação com os R$250 reais, só o simulador deste script.
    backtest_cash = 100_000.0
    print(f"Rodando backtest (cash simulado: {backtest_cash:,.0f} -- ver nota no código, comissão 0.1%)...")
    result = run_backtest(df, strategy_label=f"confluence_{symbol}_{interval}", cash=backtest_cash)

    print()
    print("=== Resultado do backtest ===")
    print(f"Par/timeframe:      {symbol} ({interval})")
    print(f"Retorno total:      {result.total_return_pct:.2f}%")
    print(f"Drawdown máximo:    {result.max_drawdown_pct:.2f}%")
    print(f"Win rate:           {result.win_rate_pct:.2f}%")
    print(f"Número de trades:   {result.num_trades}")
    print()
    print("Revise esses números manualmente antes de ligar o bot com dinheiro real.")
    print("Rode pra outros pares/timeframes também, ex:")
    print("  python run_backtest.py ETHUSDT 15m 1500")
    print("  python run_backtest.py SOLUSDT 1h 500")


if __name__ == "__main__":
    main()

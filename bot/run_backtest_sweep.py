"""Sweep de backtest: roda a mesma estratégia (comitê atual ou Kotegawa) em
vários pares e (no caso do comitê) várias janelas de tempo de uma vez só, e
agrega os trades de todos os testes numa estatística POOLADA.

Por quê poolar em vez de só olhar cada teste isolado: o retorno total
composto de UM teste é sensível a ordem dos trades e sorte de amostra
pequena (já vimos isso -- BTC sozinho no Kotegawa deu +1% e some quando
pooled com ETH/SOL). Win rate e pnl médio por trade poolados em dezenas de
trades independentes (vários pares, vários períodos) são uma medida bem
mais robusta de se existe uma vantagem real.

Uso:
    python run_backtest_sweep.py committee [NUM_CANDLES] [STOP_ATR_MULT]
    python run_backtest_sweep.py kotegawa [NUM_CANDLES] [BB_STD] [INTERVAL]

Exemplos:
    python run_backtest_sweep.py committee
    python run_backtest_sweep.py committee 1000 1.5
    python run_backtest_sweep.py kotegawa 1095 3.0 1d

Aviso: isso faz muitas chamadas à API pública da Binance (uma por
par/período/timeframe) -- demora alguns minutos, é normal.
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
import time

SCRIPT_VERSION = "2026-09-16-v6-rapf"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
]

# janelas não sobrepostas (dias atrás do fim da janela) pro sweep do comitê --
# 15m/1000 candles = ~10.4 dias por janela, espaçar 40 dias evita
# sobreposição entre elas e cobre ~4 meses de condições de mercado distintas
COMMITTEE_DAYS_AGO = [0, 40, 80, 120]

# janelas de validação fora da amostra usada pra minerar os ajustes (min_score=3
# no mean_reversion, gate de ADX no breakout) -- período totalmente diferente,
# pra não testar a hipótese nos mesmos dados que a geraram
COMMITTEE_DAYS_AGO_OOS = [160, 200, 240, 280]

# 2ª janela de validação, genuinamente nova -- COMMITTEE_DAYS_AGO_OOS já foi
# usada duas vezes nessa rodada de redesenho (uma pra decidir reverter o
# min_score do mean_reversion, outra pra decidir desativá-lo de vez), então
# deixou de ser "fora da amostra" no sentido estrito. Esse período final serve
# só pra checar a config final (gate de ADX + mean_reversion desativado) sem
# nenhum reaproveitamento de dado já visto.
COMMITTEE_DAYS_AGO_OOS2 = [320, 360, 400, 440]

SLEEP_BETWEEN_CALLS = 0.5  # educado com a API pública da Binance


def run_committee_sweep(num_candles: int, stop_atr_mult: float, symbols: list[str],
                         days_ago_list: list[int] | None = None, run_label: str = "committee") -> None:
    from run_backtest_multi_tf import run as run_committee

    days_ago_list = days_ago_list or COMMITTEE_DAYS_AGO
    all_trades = []
    per_run_summary = []
    total_runs = len(symbols) * len(days_ago_list)
    done = 0
    for symbol in symbols:
        for days_ago in days_ago_list:
            done += 1
            end_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
            print(f"[{done}/{total_runs}] {symbol} (d-{days_ago})...")
            try:
                result = run_committee(symbol, num_candles, end_time, stop_atr_mult)
            except Exception as exc:  # nao deixa um par com erro (ex: par deslistado) derrubar o sweep inteiro
                print(f"  [erro, pulando] {exc}")
                time.sleep(SLEEP_BETWEEN_CALLS)
                continue
            all_trades.extend(result.trades)
            per_run_summary.append((symbol, days_ago, result))
            print(f"  n={result.num_trades:3d}  win_rate={result.win_rate_pct:5.1f}%  "
                  f"retorno={result.total_return_pct:+6.2f}%")
            time.sleep(SLEEP_BETWEEN_CALLS)

    _print_pooled(all_trades, per_run_summary, label="comitê (trend_following/breakout, ADX-gated)")
    _export_committee_csv(all_trades, f"backtest_trades_{run_label}.csv")


def _export_committee_csv(all_trades: list, path: str) -> None:
    """Exporta cada trade poolado com o contexto de entrada (símbolo, ADX(4h),
    score de confluência) -- é essa granularidade que falta pra diagnosticar
    padrões de verdade (ex: "só ganha quando ADX > X") em vez de só olhar a
    média agregada."""
    if not all_trades:
        print(f"(nenhum trade pra exportar em {path})")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "strategy", "reason", "confluence_score", "adx_4h",
                          "entry_time", "exit_time", "entry_price", "exit_price", "pnl_pct"])
        for t in all_trades:
            writer.writerow([t.symbol, t.strategy, t.reason, t.confluence_score, f"{t.adx_4h:.2f}",
                              t.entry_time, t.exit_time, t.entry_price, t.exit_price, f"{t.pnl_pct:.4f}"])
    print(f"\nExportado: {path} ({len(all_trades)} trades, com símbolo/estratégia/ADX(4h)/score/pnl)")


def run_kotegawa_sweep(num_candles: int, bb_std: float, interval: str, symbols: list[str]) -> None:
    from run_backtest_kotegawa import run as run_kotegawa

    all_trades = []
    per_run_summary = []
    end_time = dt.datetime.now(dt.timezone.utc)
    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] {symbol}...")
        try:
            result = run_kotegawa(symbol, interval, num_candles, end_time, bb_std)
        except Exception as exc:
            print(f"  [erro, pulando] {exc}")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue
        longs = [t for t in result.trades if t.direction == "long"]
        all_trades.extend(longs)  # só compra entra no pool -- short é só informativo (subconta é spot)
        per_run_summary.append((symbol, 0, result))
        wins = sum(1 for t in longs if t.pnl_pct > 0)
        wr = wins / len(longs) * 100 if longs else float("nan")
        print(f"  n_long={len(longs):3d}  win_rate={wr:5.1f}%  retorno={result.total_return_pct:+6.2f}%")
        time.sleep(SLEEP_BETWEEN_CALLS)

    _print_pooled(all_trades, per_run_summary, label="Kotegawa (só lado de compra, spot-only)")


def run_rapf_sweep(num_small: int, big_interval: str, small_interval: str,
                    reward_risk: float, require_filters: bool, symbols: list[str]) -> None:
    from run_backtest_rapf import run as run_rapf

    all_trades = []
    per_run_summary = []
    end_time = dt.datetime.now(dt.timezone.utc)
    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] {symbol}...")
        try:
            result = run_rapf(symbol, big_interval, small_interval, num_small, end_time, reward_risk, require_filters)
        except Exception as exc:
            print(f"  [erro, pulando] {exc}")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue
        longs = [t for t in result.trades if t.direction == "long"]
        all_trades.extend(longs)  # só compra entra no pool -- short é só informativo (subconta é spot)
        per_run_summary.append((symbol, 0, result))
        wins = sum(1 for t in longs if t.pnl_pct > 0)
        wr = wins / len(longs) * 100 if longs else float("nan")
        print(f"  gatilhos={result.triggers_seen:3d} (descartados={result.triggers_filtered_out})  "
              f"n_long={len(longs):3d}  win_rate={wr:5.1f}%  retorno={result.total_return_pct:+6.2f}%")
        time.sleep(SLEEP_BETWEEN_CALLS)

    label = f"RAPF ({big_interval}/{small_interval}, filtros {'ligados' if require_filters else 'desligados'}, só compra)"
    _print_pooled(all_trades, per_run_summary, label=label)


def _print_pooled(all_trades: list, per_run_summary: list, label: str) -> None:
    print()
    print(f"=== Pool agregado -- {label} ===")
    print(f"Execuções bem-sucedidas: {len(per_run_summary)}")
    print(f"Trades poolados:         {len(all_trades)}")
    if not all_trades:
        print("Nenhum trade gerado -- nada a agregar.")
        return

    wins = sum(1 for t in all_trades if t.pnl_pct > 0)
    win_rate = wins / len(all_trades) * 100
    n = len(all_trades)
    mean = sum(t.pnl_pct for t in all_trades) / n
    total_pnl_sum = sum(t.pnl_pct for t in all_trades)
    variance = sum((t.pnl_pct - mean) ** 2 for t in all_trades) / (n - 1) if n > 1 else 0.0
    std = variance ** 0.5
    stderr = std / (n ** 0.5) if n > 1 else 0.0
    t_stat = mean / stderr if stderr > 0 else float("nan")

    print(f"Win rate poolado:      {win_rate:.2f}%")
    print(f"Pnl médio por trade:   {mean:+.3f}%  (desvio padrão: {std:.3f}%)")
    print(f"Soma aritmética:       {total_pnl_sum:+.2f}%")
    print(f"Erro padrão da média:  {stderr:.3f}%")
    print(f'"t-stat" (média/erro): {t_stat:+.2f}  '
          f"(regra de bolso, não é teste formal: |t| > ~2 sugere que a média "
          f"não é só ruído, com n={n})")

    print()
    print("Por execução (retorno composto individual, sensível a ordem -- só de referência):")
    for symbol, days_ago, result in per_run_summary:
        print(f"  {symbol:10s} d-{days_ago:3d}: retorno={result.total_return_pct:+6.2f}%  "
              f"drawdown={result.max_drawdown_pct:+6.2f}%")


def main() -> None:
    print(f"[run_backtest_sweep.py versão: {SCRIPT_VERSION}]")
    strategy = sys.argv[1] if len(sys.argv) > 1 else "committee"

    if strategy in ("committee", "committee_oos", "committee_oos2"):
        num_candles = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
        stop_atr_mult = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5
        symbols_csv = sys.argv[4] if len(sys.argv) > 4 else None
        symbols = [s.strip().upper() for s in symbols_csv.split(",")] if symbols_csv else SYMBOLS
        # "committee_oos"/"committee_oos2" usam janelas de tempo DIFERENTES das
        # que mineramos os ajustes (gate de ADX no breakout, mean_reversion
        # desativado) -- validação de verdade, não testar na amostra que gerou
        # a ideia. oos2 é a mais "limpa": nunca foi olhada nessa rodada.
        days_ago_list = {
            "committee": COMMITTEE_DAYS_AGO,
            "committee_oos": COMMITTEE_DAYS_AGO_OOS,
            "committee_oos2": COMMITTEE_DAYS_AGO_OOS2,
        }[strategy]
        total = len(symbols) * len(days_ago_list)
        print(f"Sweep do comitê ({strategy}): {len(symbols)} pares x {len(days_ago_list)} janelas "
              f"= {total} execuções. Isso demora vários minutos, é normal.")
        print(f"Pares nessa execução: {symbols}")
        print(f"Janelas (dias atrás): {days_ago_list}")
        run_committee_sweep(num_candles, stop_atr_mult, symbols, days_ago_list, run_label=strategy)
    elif strategy == "kotegawa":
        num_candles = int(sys.argv[2]) if len(sys.argv) > 2 else 1095
        bb_std = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
        interval = sys.argv[4] if len(sys.argv) > 4 else "1d"
        symbols_csv = sys.argv[5] if len(sys.argv) > 5 else None
        symbols = [s.strip().upper() for s in symbols_csv.split(",")] if symbols_csv else SYMBOLS
        print(f"Sweep Kotegawa: {len(symbols)} pares, {interval}, {num_candles} candles cada.")
        print(f"Pares nessa execução: {symbols}")
        run_kotegawa_sweep(num_candles, bb_std, interval, symbols)
    elif strategy == "rapf":
        big_interval = sys.argv[2] if len(sys.argv) > 2 else "1d"
        small_interval = sys.argv[3] if len(sys.argv) > 3 else "4h"
        num_small = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
        reward_risk = float(sys.argv[5]) if len(sys.argv) > 5 else 2.5
        require_filters = bool(int(sys.argv[6])) if len(sys.argv) > 6 else True
        symbols_csv = sys.argv[7] if len(sys.argv) > 7 else None
        symbols = [s.strip().upper() for s in symbols_csv.split(",")] if symbols_csv else SYMBOLS
        small_hours = {"4h": 4, "1d": 24, "1w": 168}.get(small_interval, 4)
        approx_days = num_small * small_hours // 24
        print(f"Sweep RAPF: {len(symbols)} pares, {big_interval}/{small_interval}, "
              f"{num_small} candles de {small_interval} cada (~{approx_days} dias), "
              f"filtros {'ligados' if require_filters else 'desligados'}.")
        print(f"Pares nessa execução: {symbols}")
        run_rapf_sweep(num_small, big_interval, small_interval, reward_risk, require_filters, symbols)
    else:
        print(f"Estratégia desconhecida: '{strategy}' (use 'committee', 'committee_oos', "
              f"'committee_oos2', 'kotegawa' ou 'rapf')")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Baixa o histórico de dados além do preço pro simulador (29/09/2026).

  - Funding rate dos futuros perpétuos USDT-M (Binance, público), de nov/2022
    até set/2026, pros pares do universo do simulador + BTC.
      -> backtest_cache/<PAR>_funding.csv  (funding_time UTC, funding_rate)
  - Índice de Medo e Ganância, histórico completo (alternative.me, público).
      -> backtest_cache/fear_greed.csv     (date, value)
    Fonte: Alternative.me Crypto Fear & Greed Index -- a fonte exige crédito
    onde o dado for exibido.

Só leitura de dados públicos: nenhuma ordem, nenhum dado de conta.
Uso (de dentro de bot/): python baixar_dados_extras.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "backtest_cache"
START_MS = int(pd.Timestamp("2022-11-01", tz="UTC").timestamp() * 1000)
END_MS = int(pd.Timestamp("2026-09-28", tz="UTC").timestamp() * 1000)


def fear_greed() -> None:
    data = requests.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=30).json()["data"]
    df = pd.DataFrame({
        "date": pd.to_datetime([int(d["timestamp"]) for d in data], unit="s", utc=True).strftime("%Y-%m-%d"),
        "value": [int(d["value"]) for d in data],
    }).drop_duplicates("date").sort_values("date")
    df.to_csv(CACHE / "fear_greed.csv", index=False)
    print(f"Medo e Ganância: {len(df)} dias ({df['date'].iloc[0]} a {df['date'].iloc[-1]})")


def funding(client, symbol: str) -> int:
    rows, start = [], START_MS
    while start < END_MS:
        batch = client.futures_funding_rate(symbol=symbol, startTime=start, endTime=END_MS, limit=1000)
        if not batch:
            break
        rows += batch
        start = int(batch[-1]["fundingTime"]) + 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    if not rows:
        return 0
    df = pd.DataFrame({
        "funding_time": pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True),
        "funding_rate": [float(r["fundingRate"]) for r in rows],
    }).drop_duplicates("funding_time").sort_values("funding_time")
    df.to_csv(CACHE / f"{symbol}_funding.csv", index=False)
    return len(df)


def main() -> None:
    from core.binance_client import binance_client

    CACHE.mkdir(exist_ok=True)
    fear_greed()
    universe = json.loads((CACHE / "universe_top30.json").read_text())
    symbols = list(dict.fromkeys(["BTCUSDT", *universe]))
    for i, sym in enumerate(symbols, 1):
        try:
            n = funding(binance_client._client, sym)
            print(f"  [{i}/{len(symbols)}] {sym}: {n} registros de funding")
        except Exception as exc:  # noqa: BLE001 -- par sem futuros perpétuos
            print(f"  [{i}/{len(symbols)}] {sym}: sem funding ({exc!r})")


if __name__ == "__main__":
    main()

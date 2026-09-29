"""Simulador da Fase 5 -- rotação de momentum semanal (plano B do Plano de melhoria).

Criado em 29/09/2026. Estratégia sem IA, independente do comitê:

  1. Toda segunda-feira 00:00 UTC (fechamento de domingo) a carteira é revista.
  2. Filtro de mercado: BTC com fechamento semanal acima da média de 20 semanas.
     Se não estiver, vende tudo e fica em USDT até a próxima revisão.
  3. Universo elegível: os --elegiveis pares (padrão 20) de maior volume médio em
     US$ nos últimos 30 dias, com pelo menos 365 dias de histórico, sem stablecoin.
  4. Ranking: retorno de 4 semanas ignorando a última (de t-5s até t-1s).
     Só entra quem tiver retorno positivo.
  5. Carteira: as --k primeiras (padrão 3), pesos iguais. Uma moeda que já está na
     carteira só sai se cair para fora do top --buffer (padrão 6), pra evitar giro.
  6. Stop de catástrofe: -25% do preço de entrada, checado na mínima diária.
     Quem bate o stop vende e o dinheiro fica em USDT até a próxima revisão.
  7. Taxa em toda compra e venda (--fee por lado), inclusive nos rebalanceamentos.

Dados: velas DIÁRIAS da Binance, com cache em backtest_cache/ (mesmo esquema do
run_backtest_v2.py). A primeira rodada baixa ~3 anos de cada par.

Janelas fixas (mesma regra: parâmetros escolhidos só na de ajuste):
    ajuste     2023-10-02 -> 2025-03-31
    validacao  2025-03-31 -> 2026-09-28
    todas      2023-10-02 -> 2026-09-28

Comparações impressas: só segurar BTC, e carteira de pesos iguais em todos os
pares com dados (sem filtro, rebalanceada todo dia -- referência simples).

Viés de sobrevivência: o universo é o top de HOJE aplicado ao passado. Moedas que
sumiram ou despencaram e saíram do top não aparecem. Isso favorece a estratégia;
trate o retorno absoluto com desconfiança e olhe sobretudo a comparação com os
dois referenciais, que sofrem do mesmo viés.

Uso (de dentro de bot/):
    python run_backtest_momentum.py --janela ajuste
    python run_backtest_momentum.py --janela ajuste --k 5
    python run_backtest_momentum.py --janela validacao
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "backtest_cache"
RESULTS_DIR = HERE / "backtest_results"
DATA_START = "2022-09-01"  # 1 ano antes da 1ª janela: histórico mínimo + médias
DATA_END = "2026-09-28"
WINDOWS = {
    "ajuste": ("2023-10-02", "2025-03-31"),
    "validacao": ("2025-03-31", "2026-09-28"),
    "todas": ("2023-10-02", "2026-09-28"),
}


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------

def load_daily(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}_1d_{DATA_START}_{DATA_END}.csv.gz"
    if path.exists():
        df = pd.read_csv(path)
    else:
        from core.binance_client import binance_client

        t1 = pd.Timestamp(DATA_END, tz="UTC")
        n = int((t1 - pd.Timestamp(DATA_START, tz="UTC")).days) + 2
        df = binance_client.get_klines_df_window(symbol, "1d", n, t1.to_pydatetime())
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.strftime("%Y-%m-%d")
        CACHE_DIR.mkdir(exist_ok=True)
        df.to_csv(path, index=False, compression="gzip")
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["open_time"].astype(str).str[:10])
    return df.drop_duplicates("date").set_index("date")[["open", "high", "low", "close", "volume"]]


def load_universe(top: int) -> list[str]:
    path = CACHE_DIR / f"universe_top{top}.json"
    if path.exists():
        pairs = json.loads(path.read_text())
    else:
        from core.binance_client import binance_client
        from core.risk_rules import is_stablecoin

        pairs = binance_client.get_top_pairs_by_volume(quote="USDT", top_n=top * 2)
        pairs = [p for p in pairs if not is_stablecoin(p.removesuffix("USDT"))][:top]
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(pairs, indent=1))
    if "BTCUSDT" not in pairs:
        pairs = ["BTCUSDT", *pairs]
    return pairs


# ---------------------------------------------------------------------------
# Simulação
# ---------------------------------------------------------------------------

def simulate(data: dict[str, pd.DataFrame], start: str, end: str, args) -> tuple[pd.Series, list[dict], list[dict]]:
    close = pd.DataFrame({s: d["close"] for s, d in data.items()}).sort_index()
    low = pd.DataFrame({s: d["low"] for s, d in data.items()}).reindex(close.index)
    qvol = pd.DataFrame({s: d["close"] * d["volume"] for s, d in data.items()}).reindex(close.index)
    first_seen = {s: d.index.min() for s, d in data.items()}
    vol30 = qvol.rolling(30, min_periods=20).mean()

    btc_w = close["BTCUSDT"].resample("W-SUN").last()
    btc_ok_w = btc_w > btc_w.rolling(20).mean()

    days = close.loc[start:end].index
    cash, holdings = 1.0, {}  # holdings: sym -> {"qty", "entry"}
    curve, trades, weeks = {}, [], []
    fee = args.fee

    def sell(sym: str, price: float, day, reason: str) -> None:
        nonlocal cash
        h = holdings.pop(sym)
        cash += h["qty"] * price * (1 - fee)
        trades.append({"data": str(day.date()), "par": sym, "lado": "venda", "motivo": reason,
                       "preco": price, "resultado_pct": (price * (1 - fee)) / (h["entry"] * (1 + fee)) * 100 - 100})

    for day in days:
        # 1) stop de catástrofe na mínima do dia
        for sym in list(holdings):
            lo = low.at[day, sym]
            stop = holdings[sym]["entry"] * (1 - args.stop / 100)
            if not math.isnan(lo) and lo <= stop:
                sell(sym, stop, day, "stop_catastrofe")

        # 2) rebalanceamento: segunda-feira, com dados até domingo
        if day.weekday() == 0:
            last_week = day - pd.Timedelta(days=1)
            px = close.loc[:last_week].iloc[-1]
            market_ok = bool(btc_ok_w.loc[:last_week].iloc[-1]) if len(btc_ok_w.loc[:last_week]) else False
            eligible = [s for s in close.columns
                        if (last_week - first_seen[s]).days >= 365 and not math.isnan(px[s])]
            v = vol30.loc[:last_week].iloc[-1][eligible].dropna().sort_values(ascending=False)
            eligible = list(v.index[: args.elegiveis])
            p1 = close.loc[:last_week - pd.Timedelta(days=7)].iloc[-1]
            p5 = close.loc[:last_week - pd.Timedelta(days=35)].iloc[-1]
            mom = ((p1[eligible] / p5[eligible]) - 1).dropna().sort_values(ascending=False)
            ranked = [s for s in mom.index if mom[s] > 0]
            if market_ok:
                keep = [s for s in holdings if s in ranked[: args.buffer]]
                target = keep + [s for s in ranked if s not in keep][: max(0, args.k - len(keep))]
                target = target[: args.k]
            else:
                target = []
            price_today = close.loc[day]  # executa no fechamento de segunda (aprox. de "logo após a revisão")
            for sym in list(holdings):
                if sym not in target:
                    sell(sym, float(price_today[sym]), day, "rebalanceamento" if market_ok else "filtro_btc")
            equity = cash + sum(h["qty"] * float(price_today[s]) for s, h in holdings.items())
            # rebalanceia pesos iguais: vende excesso / compra falta de quem fica
            if target:
                w = equity / len(target)
                for sym in target:
                    p = float(price_today[sym])
                    cur = holdings.get(sym, {"qty": 0.0})["qty"] * p
                    if sym in holdings and cur > w * (1 + args.tolerancia):
                        excess_qty = (cur - w) / p
                        holdings[sym]["qty"] -= excess_qty
                        cash += excess_qty * p * (1 - fee)
                for sym in target:
                    p = float(price_today[sym])
                    cur = holdings.get(sym, {"qty": 0.0})["qty"] * p
                    need = min(w - cur, cash)
                    if need > w * args.tolerancia:
                        qty = need * (1 - fee) / p
                        if sym in holdings:
                            h = holdings[sym]
                            h["entry"] = (h["entry"] * h["qty"] + p * qty) / (h["qty"] + qty)
                            h["qty"] += qty
                        else:
                            holdings[sym] = {"qty": qty, "entry": p}
                            trades.append({"data": str(day.date()), "par": sym, "lado": "compra",
                                           "motivo": "entrada", "preco": p, "resultado_pct": float("nan")})
                        cash -= need
            weeks.append({"semana": str(day.date()), "btc_ok": market_ok, "carteira": ",".join(target)})

        equity = cash + sum(h["qty"] * float(close.at[day, s]) for s, h in holdings.items()
                            if not math.isnan(close.at[day, s]))
        curve[day] = equity
    return pd.Series(curve), trades, weeks


def stats(curve: pd.Series) -> dict:
    if len(curve) < 2:
        return {}
    weekly = curve.resample("W-SUN").last().pct_change().dropna()
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    total = curve.iloc[-1] / curve.iloc[0] - 1
    sd = weekly.std(ddof=1)
    return {
        "retorno_total_pct": round(total * 100, 1),
        "retorno_anual_pct": round(((1 + total) ** (1 / years) - 1) * 100, 1) if years > 0 and total > -1 else float("nan"),
        "drawdown_max_pct": round(((curve / curve.cummax()) - 1).min() * 100, 1),
        "semanas": len(weekly),
        "media_semanal_pct": round(weekly.mean() * 100, 3),
        "t_semanal": round(weekly.mean() / (sd / math.sqrt(len(weekly))), 2) if sd > 0 else float("nan"),
        "sharpe_anual": round(weekly.mean() / sd * math.sqrt(52), 2) if sd > 0 else float("nan"),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--janela", default="ajuste", choices=list(WINDOWS))
    p.add_argument("--top", type=int, default=30, help="universo: mesmo top usado pelo run_backtest_v2.py")
    p.add_argument("--elegiveis", type=int, default=20)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--buffer", type=int, default=6)
    p.add_argument("--stop", type=float, default=25.0, help="stop de catástrofe em %% abaixo da entrada")
    p.add_argument("--fee", type=float, default=0.001)
    p.add_argument("--tolerancia", type=float, default=0.10, help="só ajusta peso se desviar mais que isso (fração)")
    p.add_argument("--tag", default="momentum")
    args = p.parse_args(argv)
    start, end = WINDOWS[args.janela]

    data = {}
    for sym in load_universe(args.top):
        try:
            d = load_daily(sym)
            if d is not None and len(d) > 60:
                data[sym] = d
        except Exception as exc:
            print(f"  {sym}: erro ({exc!r}), pulado")
    if "BTCUSDT" not in data:
        raise SystemExit("Sem dados de BTCUSDT -- a busca na Binance falhou? Rode de dentro de bot/.")
    print(f"{len(data)} pares com dados diários | janela {args.janela} ({start} -> {end}) | "
          f"k={args.k} buffer={args.buffer} stop={args.stop}% taxa={args.fee * 100:.3f}%/lado")

    curve, trades, weeks = simulate(data, start, end, args)
    s = stats(curve)
    btc = data["BTCUSDT"]["close"].loc[start:end]
    closes = pd.DataFrame({k: d["close"] for k, d in data.items()}).loc[start:end]
    ew = (closes.pct_change().mean(axis=1).fillna(0) + 1).cumprod()
    sells = [t for t in trades if t["lado"] == "venda"]

    print("\n=== Rotação de momentum semanal ===")
    for k, v in s.items():
        print(f"  {k:20s} {v}")
    print(f"  {'operacoes':20s} {len(trades)} ({len(sells)} vendas)")
    print(f"  {'semanas_em_usdt':20s} {sum(1 for w in weeks if not w['carteira'])} de {len(weeks)}")
    for name, ref in [("so_segurar_btc", btc), ("carteira_igual_universo", ew)]:
        r = stats(ref)
        print(f"\n  referência {name}: retorno {r.get('retorno_total_pct')}% | anual {r.get('retorno_anual_pct')}% | "
              f"perda máx {r.get('drawdown_max_pct')}% | sharpe {r.get('sharpe_anual')}")
    if sells:
        by = pd.DataFrame(sells).groupby("motivo")["resultado_pct"].agg(["count", "mean"]).round(2)
        print("\n  vendas por motivo (resultado médio da posição, %):\n  " + by.to_string().replace("\n", "\n  "))

    RESULTS_DIR.mkdir(exist_ok=True)
    pd.DataFrame(trades).to_csv(RESULTS_DIR / f"{args.tag}_{args.janela}_operacoes.csv", index=False)
    pd.DataFrame(weeks).to_csv(RESULTS_DIR / f"{args.tag}_{args.janela}_semanas.csv", index=False)
    row = {"quando": dt.datetime.now().isoformat(timespec="seconds"), "tag": args.tag, "janela": args.janela,
           **s, "operacoes": len(trades), "config": json.dumps(vars(args))}
    out = RESULTS_DIR / "resumo_momentum.csv"
    pd.DataFrame([row]).to_csv(out, mode="a", header=not out.exists(), index=False)


if __name__ == "__main__":
    main()

"""Estudo: funding e Medo e Ganância como filtro de entrada (29/09/2026).

Pergunta: dados além do preço, gratuitos e com histórico longo, melhoram a
versão final (core/strategy_profile.py)? Pré-requisito: `python baixar_dados_extras.py`.

Protocolo FIXADO ANTES de ver os dados (rodar uma vez, sem mexer depois):

  Variantes (todas sobre a versão final, com as travas reais do bot):
    base        -- versão final, sem filtro extra
    mg75        -- só compra com Medo e Ganância <= 75 (evita ganância extrema)
    mg50        -- só compra com Medo e Ganância <= 50 (medo ou neutro)
    fund_par    -- só compra com o funding do próprio par <= 0,03% por 8h
    fund_btc    -- só compra com o funding médio do BTC em ~3 dias <= 0,01% por 8h
                   (0,01% é o nível "neutro" padrão da Binance)

  1. Seleção em 2024 (jan-dez, janelas ajuste2 + val3 juntas): escolhe a variante
     de maior fator de lucro com pelo menos 80 operações -- e só se ela for
     melhor que a base. Senão, conclusão = nenhum filtro ajuda.
  2. Prova final em 2023 (jan-dez), NUNCA usado antes: escolhida contra base,
     uma vez só. Comparação também com "só segurar BTC" no mesmo ano.
  3. Checagem extra em jan-set/2025 (já visto no estudo anterior; não é prova).

  Aprovação: em 2023, t >= 2, fator de lucro >= 1,2 e retorno acima da base.

Saída: backtest_results/estudo_dados_extras.md (+ linhas em resumo.csv)
Fonte do índice: Alternative.me Crypto Fear & Greed Index.
Uso (de dentro de bot/): python run_estudo_dados_extras.py
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

import run_backtest_v2 as rb

FINAL = ["--filtro-btc", "--trailing-ativacao", "3", "--trailing-distancia", "2", "--sem-alvo",
         "--stop-atr-1h", "2", "--min-preco", "0.05", "--min-volume-usd", "20000000",
         "--bloquear", "KITE,AUDIO,MUBARAK,ONE,BANK", "--pausa-perda-dia", "3", "--pausa-queda", "10", "--top", "30"]
VARIANTS = {
    "base": [],
    "mg75": ["--max-medo-ganancia", "75"],
    "mg50": ["--max-medo-ganancia", "50"],
    "fund_par": ["--max-funding", "0.0003"],
    "fund_btc": ["--max-funding-btc", "0.0001"],
}


def run(window: str, name: str) -> dict:
    tag = f"extra_{name}"
    args = rb.parse_args(["--janela", window, "--tag", tag, *FINAL, *VARIANTS[name]])
    rb.INITIAL_CASH = args.capital
    m = rb.run_window(window, rb.load_universe(args.top, []), rb.exit_config_from(args), args)
    m.update(tag=tag, janela=window)
    return m


def pooled(tag: str, windows: list[str]) -> dict:
    frames = [pd.read_csv(p) for w in windows if (p := rb.RESULTS_DIR / f"{tag}_{w}_trades.csv").exists()]
    if not frames:
        return {"n": 0, "media": float("nan"), "t": float("nan"), "fator_lucro": 0.0}
    t = pd.concat(frames)
    n, u = t["net_pct"], t["net_usd"]
    sd = float(n.std(ddof=1)) if len(n) > 1 else 0.0
    return {"n": len(t), "media": round(float(n.mean()), 3),
            "t": round(float(n.mean()) / (sd / math.sqrt(len(n))), 2) if sd > 0 else float("nan"),
            "fator_lucro": round(float(u[u > 0].sum() / -u[u <= 0].sum()), 2) if (u < 0).any() else float("inf")}


def btc_hold(start: str, end: str) -> float:
    d = rb._btc_daily()
    x = d[(d.index >= pd.Timestamp(start, tz="UTC")) & (d.index < pd.Timestamp(end, tz="UTC"))]
    return round((x.iloc[-1] / x.iloc[0] - 1) * 100, 1)


def row(m: dict) -> str:
    return (f"| {m['tag']} | {m['janela']} | {m.get('n', 0)} | {m.get('acerto_pct', '-')}% | "
            f"{m.get('media_liquida_pct', '-')}% | {m.get('t_liquido', '-')} | {m.get('fator_lucro', '-')} | "
            f"{m.get('retorno_total_pct', '-')}% | {m.get('drawdown_max_pct', '-')}% |")


def main() -> None:
    for need in ("fear_greed.csv", "BTCUSDT_funding.csv"):
        if not (rb.CACHE_DIR / need).exists():
            raise SystemExit(f"Falta backtest_cache/{need} -- rode antes: python baixar_dados_extras.py")

    rows: list[dict] = []
    print("=== 1) Seleção em 2024 ===")
    for name in VARIANTS:
        for w in ("ajuste2", "val3"):
            rows.append(run(w, name))
    sel = {name: pooled(f"extra_{name}", ["ajuste2", "val3"]) for name in VARIANTS}
    base_pf = sel["base"]["fator_lucro"]
    cands = {k: v for k, v in sel.items() if k != "base" and v["n"] >= 80 and v["fator_lucro"] > base_pf}
    chosen = max(cands, key=lambda k: cands[k]["fator_lucro"]) if cands else None
    print(f"\n>>> Escolhida em 2024: {chosen or 'nenhuma (nenhum filtro melhorou a base)'}\n")

    compare = ["base"] + ([chosen] if chosen else [])
    print("=== 2) Prova final em 2023 ===")
    rows += [run("prova2023", k) for k in compare]
    print("=== 3) Checagem extra em jan-set/2025 (já visto) ===")
    rows += [run("final3", k) for k in compare]

    proof = {k: next(m for m in rows if m["tag"] == f"extra_{k}" and m["janela"] == "prova2023") for k in compare}
    hold23 = btc_hold(*rb.WINDOWS["prova2023"])
    if chosen:
        c, b = proof[chosen], proof["base"]
        passed = (c.get("t_liquido", 0) or 0) >= 2 and (c.get("fator_lucro", 0) or 0) >= 1.2 \
            and (c.get("retorno_total_pct", -1e9) > b.get("retorno_total_pct", -1e9))
        verdict = (f"**Escolhida em 2024:** `{chosen}`. **Prova de 2023:** {'APROVADA' if passed else 'REPROVADA'} "
                   f"(t={c.get('t_liquido')}, fator de lucro {c.get('fator_lucro')}, retorno {c.get('retorno_total_pct')}% "
                   f"contra {b.get('retorno_total_pct')}% da base e {hold23}% de só segurar BTC).")
    else:
        verdict = (f"**Nenhum filtro melhorou a base em 2024.** Base em 2023: retorno "
                   f"{proof['base'].get('retorno_total_pct')}% contra {hold23}% de só segurar BTC.")

    lines = [
        f"# Estudo de dados extras: funding e Medo e Ganância ({dt.datetime.now(dt.timezone.utc):%Y-%m-%d})", "",
        verdict, "",
        "Seleção em 2024 (jan-dez juntos): " + "; ".join(
            f"{k}: n={v['n']}, média {v['media']}%, t={v['t']}, fator de lucro {v['fator_lucro']}" for k, v in sel.items()),
        "",
        "| Variante | Janela | Operações | Acerto | Média líquida | t | Fator de lucro | Retorno | Perda máx. |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *[row(m) for m in rows], "",
        f"Só segurar BTC em 2023: {hold23}%.",
        "Fonte do índice de Medo e Ganância: Alternative.me Crypto Fear & Greed Index.",
    ]
    out = rb.RESULTS_DIR / "estudo_dados_extras.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines) + f"\n\nRelatório gravado em {out}")


if __name__ == "__main__":
    main()

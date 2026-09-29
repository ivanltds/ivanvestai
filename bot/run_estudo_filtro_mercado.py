"""Estudo do filtro de mercado mais lento (opção 3 do plano, 29/09/2026).

Por que: a versão final perdeu na prova de out/2025-mar/2026 (mercado de baixa)
porque o filtro de BTC no 4h vira rápido demais e deixa o bot comprar nos
repiques. Hipótese: um filtro diário ou semanal protege melhor.

Protocolo FIXADO ANTES de ver os dados (rodar uma vez, sem mexer depois):

  1. Janela de ajuste (2024-jan a 2024-jun): testa 4 filtros com a versão final
       4h (o de hoje) | diario (BTC > média 50d e média subindo) |
       semanal (BTC > média de 20 semanas) | 4h+diario
     Escolhe o de maior fator de lucro com pelo menos 50 operações
     (empate: maior retorno).
  2. Validação (2024-jul a 2024-dez): o escolhido contra o 4h.
  3. Prova final (2025-jan a 2025-set): o escolhido contra o 4h, uma vez só,
     com US$ 10 mil e com o capital real (US$ 65, taxa BNB).
  4. Referência (NÃO é prova, já foi visto): out/2025-mar/2026, onde o 4h falhou.

Critério de aprovação do escolhido: na validação + prova final juntas,
t >= 2 e fator de lucro >= 1,2, e perda máxima menor que a do 4h.

Todas as outras regras são as da versão final (core/strategy_profile.py),
com as travas reais do bot. A 1ª execução baixa ~21 meses de velas de 15m/1h/4h
dos 30 pares (demora, e só na primeira vez).

Uso (de dentro de bot/):  python run_estudo_filtro_mercado.py
Saída: backtest_results/estudo_filtro_mercado.md (+ linhas em resumo.csv)
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

import run_backtest_v2 as rb

FINAL = ["--trailing-ativacao", "3", "--trailing-distancia", "2", "--sem-alvo", "--stop-atr-1h", "2",
         "--min-preco", "0.05", "--min-volume-usd", "20000000", "--bloquear", "KITE,AUDIO,MUBARAK,ONE,BANK",
         "--pausa-perda-dia", "3", "--pausa-queda", "10", "--top", "30"]
REAL = ["--capital", "65", "--ordem-minima", "5.25", "--fee", "0.00075"]
KINDS = ["4h", "diario", "semanal", "4h+diario"]


def run(window: str, kind: str, extra: list[str] | None = None) -> dict:
    tag = f"estudo_{kind.replace('+', '_')}" + ("_65" if extra else "")
    args = rb.parse_args(["--janela", window, "--tag", tag, "--filtro-mercado", kind, *FINAL, *(extra or [])])
    rb.INITIAL_CASH = args.capital
    symbols = rb.load_universe(args.top, [])
    m = rb.run_window(window, symbols, rb.exit_config_from(args), args)
    m["tag"], m["janela"] = tag, window
    return m


def pooled(tag: str, windows: list[str]) -> dict:
    frames = []
    for w in windows:
        path = rb.RESULTS_DIR / f"{tag}_{w}_trades.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
    if not frames:
        return {"n": 0}
    t = pd.concat(frames)
    n, u = t["net_pct"], t["net_usd"]
    sd = n.std(ddof=1)
    return {"n": len(t), "media": round(float(n.mean()), 3),
            "t": round(float(n.mean() / (sd / math.sqrt(len(n)))), 2) if sd > 0 else float("nan"),
            "fator_lucro": round(float(u[u > 0].sum() / -u[u <= 0].sum()), 2) if (u < 0).any() else float("inf")}


def fmt(m: dict) -> str:
    return (f"| {m['tag']} | {m['janela']} | {m.get('n', 0)} | {m.get('acerto_pct', '-')}% | "
            f"{m.get('media_liquida_pct', '-')}% | {m.get('t_liquido', '-')} | {m.get('fator_lucro', '-')} | "
            f"{m.get('retorno_total_pct', '-')}% | {m.get('drawdown_max_pct', '-')}% |")


def main() -> None:
    rows: list[dict] = []
    print("=== 1) Ajuste (2024-1º semestre): 4 filtros ===")
    ajuste = {k: run("ajuste2", k) for k in KINDS}
    rows += list(ajuste.values())
    eligible = {k: m for k, m in ajuste.items() if m.get("n", 0) >= 50}
    pool = eligible or ajuste
    chosen = max(pool, key=lambda k: (pool[k].get("fator_lucro", 0) or 0, pool[k].get("retorno_total_pct", -1e9)))
    print(f"\n>>> Escolhido na janela de ajuste: {chosen}\n")

    compare = [chosen] if chosen == "4h" else [chosen, "4h"]
    print("=== 2) Validação (2024-2º semestre) ===")
    rows += [run("val3", k) for k in compare]
    print("=== 3) Prova final (2025-jan a set) ===")
    rows += [run("final3", k) for k in compare]
    rows += [run("final3", k, REAL) for k in compare]
    print("=== 4) Referência já vista (out/2025-mar/2026) ===")
    for w in ("final1", "final2"):
        for k in compare:
            try:
                rows.append(run(w, k))
            except SystemExit as exc:  # referência opcional: sem dados, segue sem ela
                print(f"(referência {w}/{k} pulada: {exc})")

    verdict = {}
    for k in compare:
        tag = f"estudo_{k.replace('+', '_')}"
        verdict[k] = pooled(tag, ["val3", "final3"])
    c = verdict[chosen]
    dd_chosen = min(m.get("drawdown_max_pct", 0) for m in rows if m["tag"] == f"estudo_{chosen.replace('+', '_')}"
                    and m["janela"] in ("val3", "final3"))
    dd_4h = min(m.get("drawdown_max_pct", 0) for m in rows if m["tag"] == "estudo_4h" and m["janela"] in ("val3", "final3"))
    passed = c["n"] > 0 and c["t"] >= 2 and c["fator_lucro"] >= 1.2 and (chosen == "4h" or dd_chosen > dd_4h)

    lines = [
        f"# Estudo do filtro de mercado ({dt.datetime.now(dt.timezone.utc):%Y-%m-%d})", "",
        (f"**Escolhido na janela de ajuste:** `{chosen}`. "
         f"**Resultado:** {'APROVADO' if passed else 'REPROVADO'} no critério (validação + prova final juntas: "
         f"n={c['n']}, média {c.get('media')}%, t={c.get('t')}, fator de lucro {c.get('fator_lucro')}; "
         f"perda máx. {dd_chosen}% contra {dd_4h}% do 4h)."), "",
        "| Configuração | Janela | Operações | Acerto | Média líquida | t | Fator de lucro | Retorno | Perda máx. |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *[fmt(m) for m in rows], "",
        "Validação + prova final juntas: " + "; ".join(f"{k}: {v}" for k, v in verdict.items()),
    ]
    out = rb.RESULTS_DIR / "estudo_filtro_mercado.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nRelatório gravado em {out}")


if __name__ == "__main__":
    main()

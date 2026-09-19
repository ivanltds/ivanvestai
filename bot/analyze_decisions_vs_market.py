"""Compara as decisões do comitê (Neon) com o que o mercado fez DEPOIS, usando
preços públicos da Binance (klines de 5m, sem chave de API). Só leitura.

Uso (dentro de bot/): python analyze_decisions_vs_market.py

Saídas: eficácia das aprovações/reprovações, oportunidades "perdidas" (potencial)
e uma simulação sequencial do que o pipeline teria rendido sem restrição de saldo.
"""
from __future__ import annotations

import collections
import datetime as dt
import sys
import time

import httpx
from sqlalchemy import text

from db.session import get_session

STOP, TAKE = 0.01, 0.015      # pisos usados de fato pelo RiskCommitteeAgent (settings.min_stop/take)
FEE_ROUND_TRIP = 0.002        # 0,1% de taxa em cada lado
H24 = 288                     # 24h em candles de 5m
MIN_FWD = 48                  # exige >= 4h de dados à frente pra entrar nas médias
BASE = "https://api.binance.com/api/v3/klines"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def fetch_klines(client: httpx.Client, symbol: str, start_ms: int) -> list[list] | None:
    out: list[list] = []
    while True:
        r = client.get(BASE, params={"symbol": symbol, "interval": "5m", "startTime": start_ms, "limit": 1000})
        if r.status_code != 200:
            return None if not out else out
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        start_ms = batch[-1][0] + 300_000
        if len(batch) < 1000:
            break
        time.sleep(0.05)
    return out


class Series:
    def __init__(self, raw: list[list]) -> None:
        self.t = [k[0] for k in raw]
        self.o = [float(k[1]) for k in raw]
        self.h = [float(k[2]) for k in raw]
        self.l = [float(k[3]) for k in raw]
        self.c = [float(k[4]) for k in raw]

    def idx_after(self, ts_ms: int) -> int | None:
        lo, hi = 0, len(self.t)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.t[mid] > ts_ms:
                hi = mid
            else:
                lo = mid + 1
        return lo if lo < len(self.t) else None

    def sim(self, i: int, stop=STOP, take=TAKE, horizon=H24) -> dict | None:
        """Entra na abertura do candle i; sai em stop/take/tempo. Stop vence se os dois caem no mesmo candle."""
        n = len(self.t)
        if i >= n - MIN_FWD:
            return None
        entry = self.o[i]
        end = min(n - 1, i + horizon)
        exit_px, why, j_exit = self.c[end], "tempo", end
        for j in range(i, end + 1):
            if self.l[j] <= entry * (1 - stop):
                exit_px, why, j_exit = entry * (1 - stop), "stop", j
                break
            if self.h[j] >= entry * (1 + take):
                exit_px, why, j_exit = entry * (1 + take), "take", j
                break
        fwd = {}
        for name, k in (("1h", 12), ("4h", 48), ("24h", 288)):
            fwd[name] = self.c[i + k] / entry - 1 if i + k < n else None
        window_h = max(self.h[i:end + 1])
        window_l = min(self.l[i:end + 1])
        return {
            "net": exit_px / entry - 1 - FEE_ROUND_TRIP, "why": why, "t_exit": self.t[j_exit],
            "mfe": window_h / entry - 1, "mae": window_l / entry - 1, **{f"r{k}": v for k, v in fwd.items()},
        }


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def pct(x):
    return f"{x * 100:+.2f}%" if x == x else "   n/d"


def summarize(name: str, rows: list[dict], base_net: float | None = None) -> None:
    if not rows:
        print(f"  {name:<46} n=0")
        return
    nets = [r["sim"]["net"] for r in rows]
    wins = sum(1 for x in nets if x > 0) / len(nets)
    ex = f" excesso vs base {pct(mean(nets) - base_net)}" if base_net is not None else ""
    print(f"  {name:<46} n={len(rows):<4} ret.4h {pct(mean(r['sim']['r4h'] for r in rows))} "
          f"ret.24h {pct(mean(r['sim']['r24h'] for r in rows))} | c/ stop1%/take1,5% liq {pct(mean(nets))} "
          f"acerto {wins * 100:4.0f}% MFE24h {pct(mean(r['sim']['mfe'] for r in rows))}{ex}")


def main() -> None:
    with get_session() as s:
        opps = s.execute(text("""
            select o.id, o.cycle_id, o.timestamp, o.pair, o.strategy, o.status, o.final_confidence,
                   max(case when d.agent_name='portfolio_comparison_agent' then d.decision end) p_dec,
                   max(case when d.agent_name='portfolio_comparison_agent' then d.reasoning end) p_reason,
                   max(case when d.agent_name='viability_agent' then d.decision end) v_dec,
                   max(case when d.agent_name='viability_agent' then d.confidence end) v_conf,
                   max(case when d.agent_name='risk_committee_agent' then d.decision end) r_dec
            from opportunities o left join committee_decisions d on d.opportunity_id = o.id
            group by o.id order by o.timestamp""")).mappings().all()
        reviews = s.execute(text(
            "select timestamp, asset, decision, confidence, acted, is_paper from position_reviews order by timestamp"
        )).mappings().all()

    pairs = sorted({o["pair"] for o in opps})
    first_ms = int(min(o["timestamp"] for o in opps).timestamp() * 1000) - 3600_000
    print(f"{len(opps)} oportunidades, {len(pairs)} pares, de {min(o['timestamp'] for o in opps):%d/%m %H:%M} "
          f"a {max(o['timestamp'] for o in opps):%d/%m %H:%M} UTC. Baixando preços da Binance...")

    series: dict[str, Series] = {}
    with httpx.Client(timeout=20) as client:
        for p in pairs + ["BTCUSDT"]:
            raw = fetch_klines(client, p, first_ms)
            if raw and len(raw) > 100:
                series[p] = Series(raw)
    missing = [p for p in pairs if p not in series]
    print(f"preços obtidos p/ {len(series)} pares; sem dados: {missing}")

    # baseline: entrar em todo candle de 15m do mesmo par, no mesmo período
    base: dict[str, float] = {}
    for p, ser in series.items():
        vals = [r["net"] for i in range(0, len(ser.t), 3) if (r := ser.sim(i))]
        base[p] = mean(vals)

    rows: list[dict] = []
    for o in opps:
        ser = series.get(o["pair"])
        if not ser:
            continue
        i = ser.idx_after(int(o["timestamp"].timestamp() * 1000))
        sim = ser.sim(i) if i is not None else None
        if sim:
            rows.append({**o, "sim": sim, "base": base[o["pair"]], "i": i})
    print(f"{len(rows)} oportunidades com >= 4h de dados à frente\n")

    b_all = mean(r["base"] for r in rows)
    print("=== 1) Mercado no período (BTC, do 1º ao último candle) ===")
    btc = series["BTCUSDT"]
    print(f"  BTC: {btc.c[0]:,.0f} -> {btc.c[-1]:,.0f} ({pct(btc.c[-1] / btc.c[0] - 1)}) | "
          f"média dos pares no baseline (entrar em qualquer momento c/ mesmas regras): {pct(b_all)} líquido por operação\n")

    print("=== 2) Qualidade dos sinais (todas as oportunidades) vs entrar aleatoriamente no mesmo par ===")
    summarize("todas as oportunidades do scanner", rows, b_all)
    for strat in sorted({r["strategy"] for r in rows}):
        sub = [r for r in rows if r["strategy"] == strat]
        summarize(f"  estratégia {strat}", sub, mean(r["base"] for r in sub))

    print("\n=== 3) Decisões: aprovadas vs reprovadas ===")
    approved = [r for r in rows if r["status"] == "approved"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    summarize("APROVADAS pelo comitê", approved, mean(r["base"] for r in approved) if approved else None)
    summarize("REPROVADAS (todas)", rejected, mean(r["base"] for r in rejected))
    viab_ok = [r for r in rows if r["v_dec"] == "approve"]
    viab_no = [r for r in rows if r["v_dec"] == "reject"]
    viab_ab = [r for r in rows if r["v_dec"] == "abstain"]
    summarize("Viabilidade (LLM) = approve", viab_ok, mean(r["base"] for r in viab_ok) if viab_ok else None)
    summarize("Viabilidade (LLM) = reject", viab_no, mean(r["base"] for r in viab_no) if viab_no else None)
    summarize("Viabilidade (LLM) = abstain", viab_ab, mean(r["base"] for r in viab_ab) if viab_ab else None)
    strong = [r for r in rows if r["v_dec"] == "approve" and (r["v_conf"] or 0) >= 0.8]
    summarize("Viabilidade approve com confiança >= 80%", strong, mean(r["base"] for r in strong) if strong else None)

    print("\n=== 4) Por que foram reprovadas (portfólio) ===")
    def reason_key(r):
        t = (r["p_reason"] or "").lower()
        if "saldo livre" in t and "insuficiente" in t:
            return "saldo livre insuficiente (regra antiga)"
        if "correlação" in t:
            return "correlação alta com posição aberta"
        if "abaixo do mínimo" in t:
            return "valor abaixo do mínimo da Binance / saldo"
        return "outro/viabilidade/risco"
    groups = collections.defaultdict(list)
    for r in rejected:
        groups[reason_key(r)].append(r)
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        summarize(k, v, mean(x["base"] for x in v))

    print("\n=== 5) POTENCIAL PERDIDO: viabilidade aprovou (>=80%) mas a oportunidade foi barrada ===")
    blocked = [r for r in strong if r["status"] == "rejected"]
    summarize("bloqueadas com viabilidade >= 80%", blocked, mean(r["base"] for r in blocked) if blocked else None)
    # melhores casos, 1 por par/dia pra não repetir o mesmo movimento
    seen, best = set(), []
    for r in sorted(blocked, key=lambda r: -r["sim"]["net"]):
        key = (r["pair"], r["timestamp"].date())
        if key in seen:
            continue
        seen.add(key)
        best.append(r)
    print("  melhores casos (1 por par/dia; ordenados pelo resultado líquido com stop/take do bot):")
    for r in best[:10]:
        ts = r["timestamp"] - dt.timedelta(hours=3)
        print(f"    {ts:%d/%m %H:%M} {r['pair']:<10} {r['strategy']:<15} viab {r['v_conf'] or 0:.0%} → "
              f"líq {pct(r['sim']['net'])} ({r['sim']['why']}), máx. 24h {pct(r['sim']['mfe'])} | barrada: {reason_key(r)}")
    worst = sorted(blocked, key=lambda r: r["sim"]["net"])
    print("  piores (o que a barreira EVITOU):")
    seen = set()
    n = 0
    for r in worst:
        key = (r["pair"], r["timestamp"].date())
        if key in seen:
            continue
        seen.add(key)
        ts = r["timestamp"] - dt.timedelta(hours=3)
        print(f"    {ts:%d/%m %H:%M} {r['pair']:<10} líq {pct(r['sim']['net'])} ({r['sim']['why']}), mín. 24h {pct(r['sim']['mae'])}")
        n += 1
        if n >= 5:
            break

    print("\n=== 6) Simulação sequencial: 1 posição por vez, US$30, regras do bot (stop 1%/take 1,5%/24h), taxas 0,2% ===")
    def sequential(cands: list[dict], label: str) -> None:
        t_free = 0
        pnl, n_tr, n_win = 0.0, 0, 0
        for r in sorted(cands, key=lambda r: r["timestamp"]):
            if r["timestamp"].timestamp() * 1000 < t_free:
                continue
            n_tr += 1
            g = r["sim"]["net"] * 30
            pnl += g
            n_win += g > 0
            t_free = r["sim"]["t_exit"]
        print(f"  {label:<58} {n_tr:>3} operações, acerto {n_win / max(n_tr, 1) * 100:3.0f}%, P&L US${pnl:+.2f}")
    sequential([r for r in rows if r["r_dec"] == "approve"], "aprovadas pelo comitê (sem restrição de saldo)")
    sequential(strong, "viabilidade >= 80% (ignorando portfólio/comitê)")
    sequential([r for r in rows if r["v_dec"] == "approve"], "qualquer viabilidade = approve")
    sequential(rows, "TODAS as oportunidades do scanner (sem filtro algum)")

    print("\n=== 7) Revisão de carteira (LLM): o que o preço fez depois ===")
    rev_rows: dict[str, list] = collections.defaultdict(list)
    for rv in reviews:
        ser = series.get(rv["asset"] + "USDT")
        if not ser:
            continue
        i = ser.idx_after(int(rv["timestamp"].timestamp() * 1000))
        if i is None or i + 48 >= len(ser.t):
            continue
        rev_rows[rv["decision"]].append((ser.c[i + 48] / ser.o[i] - 1, ser.c[min(i + 288, len(ser.t) - 1)] / ser.o[i] - 1))
    for dec, vals in rev_rows.items():
        print(f"  decisão '{dec}': n={len(vals)}  variação média em 4h {pct(mean(v[0] for v in vals))}, até 24h {pct(mean(v[1] for v in vals))}"
              f"  ({'preço subiu depois = venda foi ruim' if dec == 'sell' else 'referência'})")


if __name__ == "__main__":
    main()

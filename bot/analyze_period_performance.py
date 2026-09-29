"""Relatório só-leitura do resultado REAL por período (Plano de melhoria, Fase 0).

Mesma régua do simulador (run_backtest_v2.py), aplicada ao dinheiro de verdade:
por conta, por motivo de saída, por estratégia e por par -- tudo LÍQUIDO de
taxa (fee/fee_asset de cada trade, convertido pra USDT com a mesma função do
analyze_today_performance.py).

Também mostra a CALIBRAÇÃO do comitê: que stop, alvo e stop móvel o
RiskCommitteeAgent de fato escolheu nas posições do período. Esses números
alimentam as opções --committee-* do simulador, pra aproximação dele ficar
parecida com o que acontece em produção.

Nenhum UPDATE/DELETE/INSERT. Só posições reais (is_paper = false) fechadas no
período, pareadas com suas ordens pelo position_id.

Uso (de dentro de bot/):
    python analyze_period_performance.py --desde 2026-09-17
    python analyze_period_performance.py --desde 2026-09-20 --ate 2026-09-29 --csv periodo.csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
from collections import defaultdict
from statistics import mean, median, stdev

from sqlalchemy import text

from analyze_today_performance import _fees_in_quote
from db.session import get_session


def _t(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    sd = stdev(values)
    return mean(values) / (sd / math.sqrt(len(values))) if sd > 0 else float("nan")


def _table(title: str, groups: dict[str, list[dict]]) -> None:
    print(f"\n--- {title} ---")
    print(f"  {'grupo':28s} {'n':>4s} {'acerto':>7s} {'média líq':>10s} {'soma US$':>10s} {'horas méd':>9s}")
    for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        net = [r["net_pct"] for r in rows]
        print(f"  {name[:28]:28s} {len(rows):4d} {sum(x > 0 for x in net) / len(net) * 100:6.1f}% "
              f"{mean(net):+9.3f}% {sum(r['net_usd'] for r in rows):+10.4f} {mean(r['hours'] for r in rows):9.1f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--desde", required=True, help="AAAA-MM-DD (fechamento a partir desta data, UTC)")
    p.add_argument("--ate", default=None, help="AAAA-MM-DD, exclusivo (padrão: agora)")
    p.add_argument("--csv", default=None, help="grava uma linha por posição neste arquivo")
    args = p.parse_args()
    since = dt.datetime.fromisoformat(args.desde).replace(tzinfo=dt.timezone.utc)
    until = dt.datetime.fromisoformat(args.ate).replace(tzinfo=dt.timezone.utc) if args.ate else dt.datetime.now(dt.timezone.utc)

    with get_session() as session:
        positions = session.execute(text("""
            select p.id, p.pair, p.avg_entry_price, p.stop_price, p.take_price, p.trailing_active,
                   p.opened_at, p.closed_at, coalesce(a.label, '(sem conta)') as account,
                   (select o.strategy from opportunities o
                     where o.pair = p.pair and o.timestamp <= p.opened_at
                       and (p.account_id is null or o.account_id = p.account_id)
                     order by o.timestamp desc limit 1) as strategy
              from positions p left join accounts a on a.id = p.account_id
             where p.status = 'closed' and p.is_paper = false
               and p.closed_at >= :since and p.closed_at < :until
             order by p.closed_at
        """), {"since": since, "until": until}).mappings().all()
        legs_by_pos: dict = defaultdict(list)
        if positions:
            for leg in session.execute(text("""
                select position_id, side, quantity, price, fee, fee_asset, reason
                  from trades where is_paper = false and position_id = any(:ids)
            """), {"ids": [p_["id"] for p_ in positions]}).mappings().all():
                legs_by_pos[leg["position_id"]].append(leg)

    rows: list[dict] = []
    skipped = 0
    for pos in positions:
        legs = legs_by_pos.get(pos["id"], [])
        buys = [leg for leg in legs if leg["side"] == "buy"]
        sells = [leg for leg in legs if leg["side"] == "sell"]
        if not buys or not sells:
            skipped += 1  # posição órfã / fechada só no banco -- sem pnl calculável
            continue
        cost = sum(float(b["quantity"]) * float(b["price"]) for b in buys)
        proceeds = sum(float(s["quantity"]) * float(s["price"]) for s in sells)
        fees, ok = _fees_in_quote(buys + sells, pos["pair"])
        net_usd = proceeds - cost - fees
        entry = float(pos["avg_entry_price"])
        take_pct = (float(pos["take_price"]) / entry - 1) * 100 if pos["take_price"] else None
        # stop final (pode ter subido com o stop móvel); só é o stop INICIAL se o trailing não andou
        stop_final_pct = (1 - float(pos["stop_price"]) / entry) * 100 if pos["stop_price"] else None
        reason = sells[-1]["reason"]
        if reason == "stop_loss" and pos["stop_price"] and float(pos["stop_price"]) > entry:
            reason = "stop_movel_lucro"
        rows.append({
            "account": pos["account"], "pair": pos["pair"], "strategy": pos["strategy"] or "?",
            "reason": reason, "opened_at": pos["opened_at"], "closed_at": pos["closed_at"],
            "hours": (pos["closed_at"] - pos["opened_at"]).total_seconds() / 3600,
            "cost": cost, "net_usd": net_usd, "gross_pct": (proceeds / cost - 1) * 100,
            "net_pct": net_usd / cost * 100, "fees_ok": ok, "trailing": bool(pos["trailing_active"]),
            "take_pct": take_pct, "stop_final_pct": stop_final_pct,
        })

    print("=" * 84)
    print(f"RESULTADO REAL {since:%Y-%m-%d} -> {until:%Y-%m-%d %H:%M} UTC (posições reais fechadas, líquido de taxa)")
    print("=" * 84)
    if skipped:
        print(f"({skipped} posição(ões) sem compra+venda registradas -- órfãs, fora do cálculo)")
    if not rows:
        print("Nenhuma posição real fechada no período.")
        return

    net = [r["net_pct"] for r in rows]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    usd_w = sum(r["net_usd"] for r in rows if r["net_usd"] > 0)
    usd_l = -sum(r["net_usd"] for r in rows if r["net_usd"] <= 0)
    print(f"\nOperações: {len(rows)} | acerto: {len(wins) / len(rows) * 100:.1f}% | "
          f"média bruta: {mean(r['gross_pct'] for r in rows):+.3f}% | média LÍQUIDA: {mean(net):+.3f}% (t={_t(net):+.2f})")
    print(f"Ganho médio: {mean(wins) if wins else 0:+.3f}% | perda média: {mean(losses) if losses else 0:+.3f}% | "
          f"fator de lucro: {usd_w / usd_l if usd_l else float('inf'):.2f} | resultado: US$ {sum(r['net_usd'] for r in rows):+.4f}")
    if not all(r["fees_ok"] for r in rows):
        print("Aviso: a taxa de alguma operação não pôde ser convertida pra USDT (ficou de fora).")

    for key, title in [("account", "por conta"), ("reason", "por motivo de saída"),
                       ("strategy", "por estratégia"), ("pair", "por par (top 15 em nº de operações)")]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        if key == "pair":
            groups = dict(sorted(groups.items(), key=lambda kv: -len(kv[1]))[:15])
        _table(title, groups)

    # --- calibração do comitê pro simulador ---------------------------------
    trailing_share = sum(r["trailing"] for r in rows) / len(rows) * 100
    takes = [r["take_pct"] for r in rows if r["take_pct"] is not None]
    stops = [r["stop_final_pct"] for r in rows if not r["trailing"] and r["stop_final_pct"] is not None]
    print("\n--- calibração do comitê (use no run_backtest_v2.py) ---")
    print(f"  stop móvel ligado em {trailing_share:.0f}% das posições")
    if takes:
        print(f"  alvo escolhido: mediana {median(takes):.2f}% (mín {min(takes):.2f}%, máx {max(takes):.2f}%)")
    if stops:
        print(f"  stop inicial (só posições SEM stop móvel): mediana {median(stops):.2f}% "
              f"(mín {min(stops):.2f}%, máx {max(stops):.2f}%)")
        if takes:
            print(f"  razão alvo/stop aproximada: {median(takes) / median(stops):.2f}  -> --committee-rr")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)} linha(s) gravada(s) em {args.csv}")


if __name__ == "__main__":
    main()

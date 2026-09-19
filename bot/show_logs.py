"""Consulta os logs do bot (tabela `bot_logs` no Postgres/Neon, ou arquivos em bot/logs).

Exemplos (rodar de dentro de bot/):
    python show_logs.py                        # últimas 60 linhas (banco)
    python show_logs.py --tail 200 --level WARNING
    python show_logs.py --since 6h --grep ZEC
    python show_logs.py --cycle 0dc35d66       # tudo de um ciclo (prefixo do id)
    python show_logs.py --source file          # lê o arquivo de hoje em vez do banco
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def parse_since(text: str) -> dt.datetime:
    m = re.fullmatch(r"(\d+)([mhd])", text.strip())
    if not m:
        raise SystemExit("--since deve ser como 30m, 6h ou 2d")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n), "d": dt.timedelta(days=n)}[unit]
    return dt.datetime.now(dt.timezone.utc) - delta


def from_db(args: argparse.Namespace) -> list[str]:
    from sqlalchemy import select

    from db.models import BotLog
    from db.session import get_session

    stmt = select(BotLog)
    if args.level:
        stmt = stmt.where(BotLog.level.in_(LEVELS[LEVELS.index(args.level):]))
    if args.since:
        stmt = stmt.where(BotLog.timestamp >= parse_since(args.since))
    if args.cycle:
        stmt = stmt.where(BotLog.cycle_id.like(f"{args.cycle}%"))
    if args.grep:
        stmt = stmt.where(BotLog.message.ilike(f"%{args.grep}%"))
    stmt = stmt.order_by(BotLog.timestamp.desc(), BotLog.id.desc()).limit(args.tail)

    with get_session() as session:
        rows = list(reversed(session.scalars(stmt).all()))
    local = dt.datetime.now().astimezone().tzinfo
    return [
        f"{r.timestamp.astimezone(local):%Y-%m-%d %H:%M:%S} {r.level:<8} "
        f"[{(r.cycle_id or '-')[:8]}]{'' if r.dry_run else ' REAL'} {r.message}"
        for r in rows
    ]


def from_file(args: argparse.Namespace) -> list[str]:
    log_dir = Path(__file__).resolve().parent / "logs"
    files = sorted(log_dir.glob("bot-*.log"))
    if not files:
        raise SystemExit(f"Nenhum arquivo de log em {log_dir}")
    lines = files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    if args.level:
        keep = LEVELS[LEVELS.index(args.level):]
        lines = [ln for ln in lines if any(f" {lv} " in ln for lv in keep)]
    if args.grep:
        lines = [ln for ln in lines if args.grep.lower() in ln.lower()]
    return lines[-args.tail:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Consulta os logs do bot.")
    parser.add_argument("--tail", type=int, default=60, help="quantas linhas mostrar (padrão 60)")
    parser.add_argument("--level", choices=LEVELS, help="nível mínimo")
    parser.add_argument("--since", help="janela de tempo: 30m, 6h, 2d (só banco)")
    parser.add_argument("--cycle", help="prefixo do id do ciclo (só banco)")
    parser.add_argument("--grep", help="texto contido na mensagem")
    parser.add_argument("--source", choices=["db", "file"], default="db")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in (from_db(args) if args.source == "db" else from_file(args)):
        print(line)


if __name__ == "__main__":
    main()

"""Log visual pro terminal -- caixas com caracteres UTF-8, cores ANSI e
umas piadas de mercado financeiro pra deixar o acompanhamento ao vivo menos
monótono (pedido do Ivan, 16/09/2026: "gostaria de ter LOGs bem visuais...
pode colocar algumas brincadeiras/piadas").

Usado por run_cycle_once.py / orchestrator/cycle_runner.py e por
run_paper_trading.py, pra ter a mesma cara visual nos dois "modos" de teste
(pipeline real em dry-run vs. reimplementação técnica isolada -- ver
arquitetura-tecnica.md 9.6).

Regras de design:
- Zero dependência nova (só stdlib) -- nada de instalar `rich`/`colorama`
  no ambiente do Ivan só pra isso.
- Nunca deve derrubar o bot por causa de log: qualquer coisa aqui é só
  cosmética, sem side-effect que importe pro fluxo de negócio.
- Cor só quando o terminal é um TTY de verdade (stdout.isatty()) e
  NO_COLOR não está setado -- senão a saída fica cheia de \x1b[...m se
  for redirecionada pra arquivo (ex: `python run_cycle_once.py > log.txt`).
- No Windows, cmd.exe antigo não entende ANSI por padrão; o truque
  `os.system("")` liga o "virtual terminal processing" do console sem
  precisar de nenhuma lib extra. Windows Terminal/PowerShell 7 (o que o
  Ivan usa) já suporta nativamente, isso é só rede de segurança.
"""
from __future__ import annotations

import os
import random
import sys

if sys.platform == "win32":  # pragma: no cover -- efeito colateral só no Windows
    os.system("")

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

_RESET = "\033[0m"
_BOLD = "\033[1m"
_CODES = {
    "green": "32",
    "red": "31",
    "yellow": "33",
    "cyan": "36",
    "magenta": "35",
    "blue": "34",
    "white": "37",
}

WIDTH = 72


def _c(text: str, color: str | None = None, bold: bool = False) -> str:
    if not _USE_COLOR or not (color or bold):
        return text
    parts = []
    if bold:
        parts.append(_BOLD)
    if color and color in _CODES:
        parts.append(f"\033[{_CODES[color]}m")
    return "".join(parts) + text + _RESET


def banner(title: str, subtitle: str = "", color: str = "cyan") -> None:
    """Caixa grande -- só pra início/fim de ciclo, não pra toda hora."""
    top = "╔" + "═" * (WIDTH - 2) + "╗"
    bottom = "╚" + "═" * (WIDTH - 2) + "╝"
    print(_c(top, color, bold=True))
    _boxed(title, color, bold=True)
    if subtitle:
        _boxed(subtitle, color)
    print(_c(bottom, color, bold=True))


def _boxed(text: str, color: str, bold: bool = False) -> None:
    inner = WIDTH - 4
    text = text[:inner]
    padded = text.center(inner)
    print(_c("║ ", color, bold=True) + _c(padded, color, bold=bold) + _c(" ║", color, bold=True))


def section(title: str, emoji: str = "▶", color: str = "blue") -> None:
    """Linha divisória de seção (ex: 'Passo 1 -- Coleta')."""
    label = f" {emoji} {title} "
    fill = "─" * max(4, WIDTH - len(label))
    print()
    print(_c(label, color, bold=True) + _c(fill, color))


def step(emoji: str, agent: str, msg: str, color: str = "cyan") -> None:
    print(f"  {emoji} {_c(agent + ':', color, bold=True)} {msg}")


def ok(msg: str) -> None:
    print(f"  {_c('✔', 'green', bold=True)} {msg}")


def warn(msg: str) -> None:
    print(f"  {_c('⚠', 'yellow', bold=True)}  {msg}")


def fail(msg: str) -> None:
    print(f"  {_c('✘', 'red', bold=True)} {msg}")


def money(msg: str) -> None:
    print(f"  {_c('💰', 'green')} {msg}")


def entry(msg: str) -> None:
    print(f"  {_c('🎯 ENTRADA', 'green', bold=True)} {msg}")


def exit_(msg: str, positive: bool) -> None:
    tag = _c("🚪 SAÍDA", "green" if positive else "red", bold=True)
    print(f"  {tag} {msg}")


_JOKES = [
    "O mercado é bipolar, mas pelo menos é consistente nisso.",
    "Comprar na baixa, vender na alta -- e eu aqui tentando descobrir qual é qual.",
    "Se HODL fosse esporte olímpico, eu já tava sentado numa posição há 3 anos.",
    "RSI gritando 'sobrecomprado' -- eu de fone de ouvido fingindo que não ouvi.",
    "Diversificação é a única coisa grátis em finanças, disseram. A taxa da Binance discorda.",
    "Um candle de martelo, um de engolfo e um doji entram numa análise técnica. Ninguém opera.",
    "Bot não sente medo nem ganância -- só timeout de API, o que já é bem humano.",
    "Stop loss é tipo academia: todo mundo sabe que devia usar, poucos usam de verdade.",
    "'Compra na notícia, vende no fato' -- ou, nesse caso, nem viu a notícia ainda.",
    "Se correlação alta entre altcoins fosse crime, metade do mercado tava presa.",
    "Circuit breaker é tipo alarme de carro: quando dispara, já assusta mais que resolve.",
    "FOMC essa semana -- até o bot ficou ansioso e pediu pra não operar.",
    "Backtest bonito, live feio: a saga eterna de todo trader sistemático.",
    "minNotional da Binance dizendo 'não' pro seu sonho de operar com R$ 5.",
    "Sharpe ratio alto no backtest, ansiedade alta na vida real -- correlação não é causalidade.",
]


def joke() -> None:
    print(_c(f"  🎲 {random.choice(_JOKES)}", "magenta"))


def cycle_banner(cycle_id: str, dry_run: bool) -> None:
    mode = "DRY-RUN 🧪 (simulado, capital zero em risco)" if dry_run else "⚠️  CAPITAL REAL EM JOGO ⚠️"
    banner("🤖  IVANVESTAI — CICLO DO COMITÊ", f"{mode}  |  ciclo {cycle_id[:8]}",
           color="green" if dry_run else "red")
    joke()

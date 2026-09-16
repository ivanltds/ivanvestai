"""Diagnóstico RÁPIDO e BARATO da conexão com a Binance -- isolado do resto
do pipeline (sem NewsAgent, sem chamadas de LLM), pra não gastar crédito de
OpenAI só pra testar se a API key da Binance está com IP/permissão certos
(achado em 16/09/2026, depois de dois `run_cycle_once.py` seguidos baterem
no mesmo erro -2015 -- ver arquitetura-tecnica.md 9.6/9.7).

Mostra:
  - a key configurada no .env, mascarada (só os 4 primeiros/últimos chars --
    nunca imprime a secret)
  - o IP público que ESTA máquina está usando pra sair pra internet (é esse
    IP que precisa estar na whitelist da API key, se a restrição de IP
    estiver ligada -- não o IP da rede local, nem o de outro dispositivo)
  - o resultado de uma chamada assinada mínima (get_account) com o
    diagnóstico amigável já embutido em core/binance_client.py se der -2015

Uso:
    python check_binance_connection.py
"""
from __future__ import annotations

from config.settings import settings
from core import vlog
from core.binance_client import binance_client


def _masked(value: str) -> str:
    if not value:
        return "(vazio -- não configurado no .env)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} ({len(value)} caracteres)"


def _public_ip() -> str | None:
    try:
        import requests
        resp = requests.get("https://api.ipify.org", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        vlog.warn(f"Não consegui descobrir o IP público desta máquina ({e}) -- confira manualmente em https://whatismyipaddress.com/")
        return None


def main() -> None:
    vlog.banner("🔌  DIAGNÓSTICO DE CONEXÃO — BINANCE", color="cyan")

    vlog.step("🔑", "BINANCE_API_KEY", _masked(settings.binance_api_key))
    vlog.step("🔑", "BINANCE_API_SECRET", _masked(settings.binance_api_secret))

    ip = _public_ip()
    if ip:
        vlog.step("🌐", "IP público desta máquina", ip)
        print("     (se a API key tiver restrição de IP ligada, é ESSE ip que precisa estar")
        print("      cadastrado em Binance > API Management -- não o IP da rede local tipo 192.168.x.x)")

    print()
    vlog.section("Testando chamada assinada (get_account)", emoji="📡")
    try:
        balances = binance_client.get_account_balances()
    except Exception as e:
        vlog.fail(f"Falhou: {e}")
        print()
        vlog.fail("Conexão com a Binance NÃO está funcionando ainda -- veja a dica acima (se apareceu) e")
        print("     confira https://www.binance.com/en/my/settings/api-management antes de rodar o ciclo de novo.")
        raise SystemExit(1)

    vlog.ok(f"Conectado! {len(balances)} ativo(s) com saldo != 0 nesta conta.")
    for b in balances[:15]:
        print(f"     {b['asset']:6s} free={b['free']:>15s}  locked={b['locked']:>15s}")
    if len(balances) > 15:
        print(f"     ... e mais {len(balances) - 15}.")
    vlog.banner("✅  BINANCE OK — PODE RODAR O CICLO", color="green")


if __name__ == "__main__":
    main()

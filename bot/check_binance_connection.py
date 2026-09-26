"""Diagnóstico RÁPIDO e BARATO da conexão com a Binance -- isolado do resto
do pipeline (sem NewsAgent, sem chamadas de LLM), pra não gastar crédito de
OpenAI só pra testar se a API key da Binance está com IP/permissão certos
(achado em 16/09/2026, depois de dois `run_cycle_once.py` seguidos baterem
no mesmo erro -2015 -- ver arquitetura-tecnica.md 9.6/9.7).

Achado 25/09/2026 (multi-conta-plano.md, Fase D -- LTCUSDT -2015 na CONTA
IVAN em produção): até aqui este script só testava as credenciais globais
do .env (settings.binance_api_key/secret). Desde a Fase C, `cycle_runner.py`
usa as credenciais de CADA CONTA guardadas (cifradas) na tabela `accounts`
via `core/account_context.py` -- um .env que funciona não prova que a key
gravada pra uma conta especifica também funciona. Agora este script testa a
chamada assinada com a credencial de CADA CONTA ATIVA separadamente, além do
.env, pra não deixar esse gap de novo.

Mostra:
  - a key configurada no .env, mascarada (só os 4 primeiros/últimos chars --
    nunca imprime a secret)
  - o IP público que ESTA máquina está usando pra sair pra internet (é esse
    IP que precisa estar na whitelist da API key, se a restrição de IP
    estiver ligada -- não o IP da rede local, nem o de outro dispositivo)
  - o resultado de uma chamada assinada mínima (get_account) rodada uma vez
    POR CONTA ATIVA (usando a credencial gravada dessa conta, não a do
    .env), com o diagnóstico amigável já embutido em core/binance_client.py
    se der -2015 -- se não houver nenhuma conta ativa cadastrada, cai de
    volta pra testar só o .env, como antes

Uso:
    python check_binance_connection.py
"""
from __future__ import annotations

from config.settings import settings
from core import vlog
from core.account_context import load_active_accounts
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


def _test_account(label: str, binance) -> bool:
    """Roda get_account_balances() com a credencial JA associada a este
    BinanceClient (do .env ou de uma conta especifica) e imprime o
    resultado. Retorna True se conectou, False se falhou (erro já
    impresso, incluindo o hint de -2015 embutido em core/binance_client.py)."""
    try:
        balances = binance.get_account_balances()
    except Exception as e:
        vlog.fail(f"[{label}] Falhou: {e}")
        return False
    vlog.ok(f"[{label}] Conectado! {len(balances)} ativo(s) com saldo != 0 nesta conta.")
    for b in balances[:15]:
        print(f"     {b['asset']:6s} free={b['free']:>15s}  locked={b['locked']:>15s}")
    if len(balances) > 15:
        print(f"     ... e mais {len(balances) - 15}.")
    return True


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
    accounts = load_active_accounts()
    all_ok = True

    if accounts:
        vlog.section(f"Testando chamada assinada (get_account) -- {len(accounts)} conta(s) ativa(s)", emoji="📡")
        for account in accounts:
            ok = _test_account(account.label, account.binance)
            all_ok = all_ok and ok
            print()
    else:
        vlog.warn("Nenhuma conta ativa cadastrada na tabela accounts -- testando só as credenciais do .env.")
        vlog.section("Testando chamada assinada (get_account)", emoji="📡")
        all_ok = _test_account("(.env)", binance_client)
        print()

    if not all_ok:
        vlog.fail("Pelo menos uma conta NÃO está conectando -- veja a dica acima (se apareceu) e")
        print("     confira https://www.binance.com/en/my/settings/api-management antes de rodar o ciclo de novo.")
        raise SystemExit(1)

    vlog.banner("✅  BINANCE OK — PODE RODAR O CICLO", color="green")


if __name__ == "__main__":
    main()

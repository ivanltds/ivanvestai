"""Cadastro/gestão de contas Binance (multi-conta-plano.md seção 5.2) --
SEMPRE rodado no PC do Ivan, nunca via dashboard/HTTP: as credenciais nunca
trafegam pela internet nem passam pela rota de API do Next.js.

Sub-comandos:
    python manage_accounts.py list
    python manage_accounts.py add --label "Conta 2" --api-key KEY --api-secret SECRET [--live] [--inactive]
    python manage_accounts.py disable <id>
    python manage_accounts.py enable <id>
    python manage_accounts.py rotate-key <id> --api-key KEY --api-secret SECRET
    python manage_accounts.py relabel <id> --label "Novo nome"
    python manage_accounts.py go-live <id> --confirm
    python manage_accounts.py go-simulation <id>

`go-live`/`go-simulation` (multi-conta-plano.md, Fase C, 25/09/2026): flip
manual de dry_run numa conta já cadastrada -- mesmo padrão de "fricção
deliberada" de settings.dry_run/bypass_macro_risk_window/
filter_agent_provider (ver docstring acima e config/settings.py): NUNCA um
toggle remoto/dashboard, sempre rodado aqui no PC do Ivan. `go-live` exige
`--confirm` (sem isso só mostra o aviso e não muda nada) porque é a direção
que AUMENTA risco (capital real); `go-simulation` (reduz risco) não exige.
Como todo o resto deste script, exige reiniciar `python main.py` pra valer
no próximo ciclo -- não há hot-reload de dry_run em produção.

Igual dry_run/bypass_macro_risk_window/filter_agent_provider (ver
config/settings.py): toda conta nova nasce em modo simulação (dry_run=True)
por padrão -- só ative --live depois de validar a conta por um tempo em
paper trading, e de conferir o checklist da seção 5.4 do plano (permissão de
trading habilitada na key, IP na whitelist, saldo próprio de BNB na conta).

Nota importante (corrigida 25/09/2026 -- Fases B/C concluídas, ver
multi-conta-plano.md seção 10.4): cadastrar uma conta aqui JÁ faz o bot
operá-la a partir do próximo `python main.py` -- toda conta com
is_active=True entra no `load_active_accounts()` que o `cycle_runner.py`
itera a cada ciclo. Se quiser só registrar sem operar ainda, use
`--inactive` e ative depois com `enable`.

Achado 25/09/2026 (multi-conta-plano.md, Fase D -- LTCUSDT -2015 na CONTA
IVAN, key errada na CONTA MICAEL só descoberta em produção): `add` agora
testa a API key na Binance (mesma chamada assinada get_account do
check_binance_connection.py) ANTES de gravar, e avisa na hora se a key/IP/
permissão estiverem errados -- em vez de só descobrir isso dias depois
quando o bot já está tentando operar a conta em produção. Não bloqueia o
cadastro (você pode corrigir na Binance depois), só avisa alto e claro.

Requer ACCOUNTS_ENCRYPTION_KEY no .env (gere com `python -m core.crypto`).
Este script nunca decifra/imprime credenciais de volta -- write-only, mesmo
padrão do dashboard (ver seção 5.1 do plano).
"""
from __future__ import annotations

import argparse
import sys
import uuid

from core.binance_client import BinanceClient
from core.crypto import AccountsEncryptionKeyMissing, encrypt_secret
from db.models import Account
from db.session import SessionLocal


def cmd_list(_args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        accounts = session.query(Account).order_by(Account.display_order, Account.created_at).all()
    if not accounts:
        print("Nenhuma conta cadastrada ainda. Use: python manage_accounts.py add --label ...")
        return
    for acc in accounts:
        status = "ativa" if acc.is_active else "INATIVA"
        mode = "simulação (dry_run)" if acc.dry_run else "CAPITAL REAL"
        print(f"- {acc.id}  \"{acc.label}\"  [{status}, {mode}]  criada em {acc.created_at:%Y-%m-%d}")


def _test_credentials(api_key: str, api_secret: str) -> bool:
    """Testa uma chamada assinada minima (get_account) com esta credencial,
    sem gravar nada -- o mesmo teste que check_binance_connection.py roda
    por conta (multi-conta-plano.md, Fase D: pegar key/IP/permissão errados
    JÁ no cadastro, não só depois em produção). Retorna True se conectou."""
    print("Testando a API key na Binance antes de gravar (get_account)...")
    try:
        balances = BinanceClient(api_key=api_key, api_secret=api_secret).get_account_balances()
    except Exception as e:
        print(f"\n⚠️  Não consegui autenticar com essa API key na Binance: {e}")
        print(
            "   A conta vai ser cadastrada mesmo assim -- você pode corrigir a key/IP/permissão\n"
            "   na Binance depois e rodar `python check_binance_connection.py` pra confirmar.\n"
            "   Mas repare: enquanto isso não for corrigido, o bot NÃO consegue operar essa conta\n"
            "   (nem ler saldo dela), mesmo em modo simulação."
        )
        return False
    print(f"✔ Autenticado! {len(balances)} ativo(s) com saldo != 0 nessa conta -- credencial confirmada.\n")
    return True


def cmd_add(args: argparse.Namespace) -> None:
    connected = _test_credentials(args.api_key, args.api_secret)
    try:
        enc_key = encrypt_secret(args.api_key)
        enc_secret = encrypt_secret(args.api_secret)
    except AccountsEncryptionKeyMissing as exc:
        raise SystemExit(str(exc)) from exc
    with SessionLocal() as session:
        display_order = session.query(Account).count()
        account = Account(
            label=args.label,
            binance_api_key_encrypted=enc_key,
            binance_api_secret_encrypted=enc_secret,
            is_active=not args.inactive,
            dry_run=not args.live,
            display_order=display_order,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
    mode = "CAPITAL REAL (--live)" if args.live else "simulação (dry_run, padrão)"
    print(f"Conta '{account.label}' criada (id={account.id}), modo: {mode}.")
    if args.live:
        print(
            "ATENÇÃO: conta criada já em modo capital real. Confirme o checklist da "
            "seção 5.4 do multi-conta-plano.md (permissão de trading habilitada na key, "
            "IP na whitelist, saldo próprio de BNB) antes de deixar o bot operar essa "
            "conta de verdade."
        )
    if not connected:
        print(
            "Lembrete: a credencial dessa conta ainda NÃO autenticou na Binance (veja o aviso "
            "acima). Corrija na Binance e rode `python check_binance_connection.py` antes de "
            "contar com essa conta."
        )
    if not args.inactive:
        print(
            "Nota: esta conta JÁ é operada pelo ciclo do bot a partir do próximo "
            "`python main.py` (Fases B/C concluídas) -- reinicie o bot pra ela entrar em produção."
        )
    else:
        print("Nota: conta criada com --inactive -- fica só registrada até você rodar `enable`.")


def _get_account(session, account_id: str) -> Account:
    try:
        parsed = uuid.UUID(account_id)
    except ValueError:
        print(f"'{account_id}' não é um UUID válido. Use `list` pra ver os ids.", file=sys.stderr)
        raise SystemExit(1)
    account = session.get(Account, parsed)
    if account is None:
        print(f"Conta {account_id} não encontrada. Use `list` pra ver os ids.", file=sys.stderr)
        raise SystemExit(1)
    return account


def cmd_set_active(args: argparse.Namespace, *, active: bool) -> None:
    with SessionLocal() as session:
        account = _get_account(session, args.account_id)
        account.is_active = active
        session.commit()
        label = account.label
    print(f"Conta '{label}' agora está {'ativa' if active else 'INATIVA'}.")


def cmd_relabel(args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        account = _get_account(session, args.account_id)
        old_label = account.label
        account.label = args.label
        session.commit()
    print(f"Conta renomeada: '{old_label}' -> '{args.label}'.")


def cmd_go_live(args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        account = _get_account(session, args.account_id)
        if not account.dry_run:
            print(f"Conta '{account.label}' já está em modo CAPITAL REAL (dry_run=False) -- nada a fazer.")
            return
        label, account_id = account.label, account.id
        if not args.confirm:
            print(
                f"Isso vai colocar a conta '{label}' (id={account_id}) operando com CAPITAL REAL -- "
                "o bot vai comprar/vender de verdade na Binance com o saldo dessa conta a partir do "
                "próximo ciclo (depois de reiniciar `python main.py`).\n\n"
                "Antes de confirmar, revise o checklist da seção 5.4 do multi-conta-plano.md: "
                "permissão de trading habilitada na API key, IP na whitelist (se aplicável), e saldo "
                "próprio de BNB nessa conta pra pagar taxas.\n\n"
                "Se já conferiu tudo isso, rode de novo com --confirm:\n"
                f"    python manage_accounts.py go-live {account_id} --confirm"
            )
            raise SystemExit(1)
        account.dry_run = False
        session.commit()
    print(f"Conta '{label}' agora está em modo CAPITAL REAL (dry_run=False).")
    print("Reinicie `python main.py` pra essa mudança valer a partir do próximo ciclo.")


def cmd_go_simulation(args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        account = _get_account(session, args.account_id)
        if account.dry_run:
            print(f"Conta '{account.label}' já está em modo simulação (dry_run=True) -- nada a fazer.")
            return
        account.dry_run = True
        label = account.label
        session.commit()
    print(f"Conta '{label}' agora está em modo SIMULAÇÃO (dry_run=True) -- capital real protegido.")
    print("Reinicie `python main.py` pra essa mudança valer a partir do próximo ciclo.")


def cmd_rotate_key(args: argparse.Namespace) -> None:
    try:
        enc_key = encrypt_secret(args.api_key)
        enc_secret = encrypt_secret(args.api_secret)
    except AccountsEncryptionKeyMissing as exc:
        raise SystemExit(str(exc)) from exc
    with SessionLocal() as session:
        account = _get_account(session, args.account_id)
        account.binance_api_key_encrypted = enc_key
        account.binance_api_secret_encrypted = enc_secret
        session.commit()
        label = account.label
    print(f"Credenciais da conta '{label}' atualizadas.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lista todas as contas cadastradas (sem mostrar credenciais).")

    p_add = sub.add_parser("add", help="Cadastra uma conta nova.")
    p_add.add_argument("--label", required=True, help="Nome de exibição (dashboard/alertas).")
    p_add.add_argument("--api-key", required=True)
    p_add.add_argument("--api-secret", required=True)
    p_add.add_argument("--live", action="store_true", help="Cria já com dry_run=False (padrão: simulação).")
    p_add.add_argument("--inactive", action="store_true", help="Cria já pausada (is_active=False).")

    p_disable = sub.add_parser("disable", help="Pausa uma conta (is_active=False) sem apagar histórico.")
    p_disable.add_argument("account_id")

    p_enable = sub.add_parser("enable", help="Reativa uma conta pausada.")
    p_enable.add_argument("account_id")

    p_rotate = sub.add_parser("rotate-key", help="Troca a API key/secret de uma conta existente.")
    p_rotate.add_argument("account_id")
    p_rotate.add_argument("--api-key", required=True)
    p_rotate.add_argument("--api-secret", required=True)

    p_relabel = sub.add_parser("relabel", help="Renomeia o label de exibição de uma conta existente.")
    p_relabel.add_argument("account_id")
    p_relabel.add_argument("--label", required=True)

    p_go_live = sub.add_parser(
        "go-live",
        help="Tira uma conta do modo simulação -- passa a operar com CAPITAL REAL (dry_run=False). Requer --confirm.",
    )
    p_go_live.add_argument("account_id")
    p_go_live.add_argument(
        "--confirm", action="store_true",
        help="Confirma a mudança pra capital real (sem isso, só mostra o aviso e não altera nada).",
    )

    p_go_sim = sub.add_parser(
        "go-simulation",
        help="Coloca uma conta de volta em modo simulação (dry_run=True) -- reduz risco, sem necessidade de --confirm.",
    )
    p_go_sim.add_argument("account_id")

    args = parser.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "disable":
        cmd_set_active(args, active=False)
    elif args.command == "enable":
        cmd_set_active(args, active=True)
    elif args.command == "rotate-key":
        cmd_rotate_key(args)
    elif args.command == "relabel":
        cmd_relabel(args)
    elif args.command == "go-live":
        cmd_go_live(args)
    elif args.command == "go-simulation":
        cmd_go_simulation(args)


if __name__ == "__main__":
    main()

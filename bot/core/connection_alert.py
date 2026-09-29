"""Alerta de perda de conexão autenticada com a Binance, por conta.

Motivação (27/09/2026, multi-conta-plano.md 10.21): a partir de 26/09 ~11:52
as DUAS contas passaram a falhar com -2015 (IP público da máquina saiu da
whitelist das keys) e isso ficou ~25h sem ninguém perceber -- nesse período
o bot não conseguia checar stop/take das posições reais abertas. Os logs já
registravam o erro, mas nada chegava no e-mail/celular do Ivan.

Comportamento:
- 1ª falha de autenticação (-2015/-2014/-2008) de uma conta -> alerta crítico
  imediato (e-mail + push), já com o IP público atual da máquina.
- Enquanto continuar falhando -> lembrete a cada REMINDER_SECONDS (1h).
- Primeira chamada assinada bem-sucedida depois disso -> alerta de
  "conexão restabelecida", com quanto tempo ficou fora.

Estado só em memória (reiniciar o bot zera: se ainda estiver falhando, o
alerta dispara de novo na primeira falha, o que é o desejado). Thread-safe
porque é chamado de dentro de asyncio.to_thread por contas em paralelo.
Nunca levanta exceção -- alerta não pode derrubar o ciclo.
"""
from __future__ import annotations

import logging
import threading
import time

from core import redis_bridge
from core.notifier import alert

logger = logging.getLogger(__name__)

AUTH_ERROR_CODES = {-2015, -2014, -2008}
REMINDER_SECONDS = 3600

_lock = threading.Lock()
# label -> {"first": epoch, "last_alert": epoch}
_failing: dict[str, dict[str, float]] = {}


def auth_error_code(exc: BaseException | None) -> int | None:
    """Procura um código de erro de autenticação da Binance na exceção ou na
    cadeia dela (__cause__/__context__) -- agentes às vezes embrulham o
    BinanceAPIException original."""
    seen = 0
    while exc is not None and seen < 10:
        code = getattr(exc, "code", None)
        if isinstance(code, int) and code in AUTH_ERROR_CODES:
            return code
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


def _public_ip() -> str:
    try:
        import requests
        resp = requests.get("https://api.ipify.org", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception:
        return "não consegui descobrir (veja em https://whatismyipaddress.com/)"


def _fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}min" if hours else f"{minutes}min"


def report_failure(account_label: str, exc: BaseException) -> None:
    """Chamar em todo except que envolve chamada assinada de uma conta.
    Ignora silenciosamente erros que não são de autenticação."""
    try:
        code = auth_error_code(exc)
        if code is None:
            return
        now = time.time()
        with _lock:
            state = _failing.get(account_label)
            if state is None:
                state = {"first": now, "last_alert": 0.0}
                _failing[account_label] = state
            if now - state["last_alert"] < REMINDER_SECONDS:
                return
            is_first = state["last_alert"] == 0.0
            state["last_alert"] = now
            down_for = now - state["first"]
        ip = _public_ip()
        subject = (
            f"[{account_label}] Binance recusando a API key (erro {code})"
            if is_first
            else f"[{account_label}] Binance AINDA recusando a API key ({_fmt_duration(down_for)} fora)"
        )
        body = (
            f"A conta {account_label} não consegue fazer chamadas assinadas na Binance (erro {code}: "
            "Invalid API-key, IP, or permissions).\n\n"
            "Enquanto isso durar, o bot NÃO consegue checar stop/take das posições abertas dessa conta, "
            "nem comprar ou vender.\n\n"
            f"IP público atual desta máquina: {ip}\n\n"
            "Causa mais comum: o IP da internet mudou e saiu da whitelist da key. Confira em "
            "Binance > Gerenciamento de API se esse IP está liberado (e se 'Enable Reading' + "
            "'Enable Spot & Margin Trading' continuam marcados). Depois rode "
            "`python check_binance_connection.py` pra confirmar.\n\n"
            f"Você receberá um lembrete a cada {REMINDER_SECONDS // 60} min enquanto não resolver."
        )
        logger.critical("%s -- IP atual: %s", subject, ip)
        alert(subject, body)
        redis_bridge.publish_event(
            "alerts", {"type": "binance_auth_error", "account": account_label, "code": code, "ip": ip}
        )
    except Exception:
        logger.exception("Falha ao processar alerta de conexão da conta %s.", account_label)


def report_success(account_label: str) -> None:
    """Chamar depois de uma chamada assinada bem-sucedida da conta. Só faz
    algo se a conta estava marcada como falhando."""
    try:
        with _lock:
            state = _failing.pop(account_label, None)
        if state is None:
            return
        down_for = time.time() - state["first"]
        subject = f"[{account_label}] Conexão com a Binance restabelecida"
        body = (
            f"A conta {account_label} voltou a autenticar na Binance depois de "
            f"~{_fmt_duration(down_for)} fora. O bot já voltou a gerenciar as posições dela."
        )
        logger.warning(subject)
        alert(subject, body)
        redis_bridge.publish_event("alerts", {"type": "binance_auth_restored", "account": account_label})
    except Exception:
        logger.exception("Falha ao processar alerta de reconexão da conta %s.", account_label)

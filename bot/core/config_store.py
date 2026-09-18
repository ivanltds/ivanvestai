"""Mescla os defaults do .env (config.settings) com overrides salvos pelo
dashboard na tabela `settings`. O dashboard escreve nessa tabela via API
route do Next.js; o bot lê no início de cada ciclo para pegar mudanças
recentes sem precisar reiniciar o processo.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings as env_settings
from db.models import Setting
from db.session import get_session

def _cast_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


# Faixas válidas por chave -- defesa no lado do bot (o dashboard também valida em
# web/app/api/settings/route.ts, mas a tabela `settings` pode ser editada direto).
# Valor fora da faixa é ignorado e o default do .env é usado.
_RANGES: dict[str, tuple[float, float]] = {
    "cycle_interval_minutes": (1, 1440),
    "entry_decision_timeout_seconds": (10, 900),
    "min_confidence_to_trade": (0.5, 1.0),
    "max_allocation_pct_per_trade": (0.01, 1.0),
    "daily_loss_alert_pct": (0.0, 1.0),
    "top_n_pairs": (1, 500),
}


_CASTERS = {
    "cycle_interval_minutes": int,
    "entry_decision_timeout_seconds": int,
    "min_confidence_to_trade": float,
    "max_allocation_pct_per_trade": float,
    "daily_loss_alert_pct": float,
    "top_n_pairs": int,
    "safety_stablecoin": str,
    "bot_status": str,  # "running" | "paused"
    "bypass_macro_risk_window": _cast_bool,
}


# Snapshot dos defaults do .env tirado no import, ANTES de apply_runtime_overrides
# mutar `settings` -- senão, apagar um override no dashboard "devolveria" o
# último valor sobrescrito em vez do default original do .env.
_ENV_DEFAULTS = {
    "cycle_interval_minutes": env_settings.cycle_interval_minutes,
    "entry_decision_timeout_seconds": env_settings.entry_decision_timeout_seconds,
    "min_confidence_to_trade": env_settings.min_confidence_to_trade,
    "max_allocation_pct_per_trade": env_settings.max_allocation_pct_per_trade,
    "daily_loss_alert_pct": env_settings.daily_loss_alert_pct,
    "top_n_pairs": env_settings.top_n_pairs,
    "safety_stablecoin": env_settings.safety_stablecoin,
}


@dataclass
class RuntimeConfig:
    cycle_interval_minutes: int
    entry_decision_timeout_seconds: int
    min_confidence_to_trade: float
    max_allocation_pct_per_trade: float
    daily_loss_alert_pct: float
    top_n_pairs: int
    safety_stablecoin: str
    bot_status: str
    bypass_macro_risk_window: bool  # ver arquitetura-tecnica.md 9.11 -- desliga o
    # bloqueio de ENTRADA NOVA durante janela de risco macro (FOMC/CPI) quando
    # ligado pelo dashboard (/settings). NÃO afeta dry_run nem a gestão de
    # posições já abertas -- só a checagem de abrir posição nova.


def load_runtime_config() -> RuntimeConfig:
    overrides: dict[str, str] = {}
    with get_session() as session:
        for row in session.query(Setting).all():
            overrides[row.key] = row.value

    def pick(key: str, default):
        caster = _CASTERS.get(key, str)
        if key in overrides:
            try:
                value = caster(overrides[key])
            except (TypeError, ValueError):
                return default
            if key in _RANGES and not (_RANGES[key][0] <= value <= _RANGES[key][1]):
                return default
            if key == "bot_status" and value not in ("running", "paused"):
                return default
            if key == "safety_stablecoin" and not (value.isalnum() and value.isupper()):
                return default
            return value
        return default

    config = RuntimeConfig(
        cycle_interval_minutes=pick("cycle_interval_minutes", _ENV_DEFAULTS["cycle_interval_minutes"]),
        entry_decision_timeout_seconds=pick(
            "entry_decision_timeout_seconds", _ENV_DEFAULTS["entry_decision_timeout_seconds"]
        ),
        min_confidence_to_trade=pick("min_confidence_to_trade", _ENV_DEFAULTS["min_confidence_to_trade"]),
        max_allocation_pct_per_trade=pick(
            "max_allocation_pct_per_trade", _ENV_DEFAULTS["max_allocation_pct_per_trade"]
        ),
        daily_loss_alert_pct=pick("daily_loss_alert_pct", _ENV_DEFAULTS["daily_loss_alert_pct"]),
        top_n_pairs=pick("top_n_pairs", _ENV_DEFAULTS["top_n_pairs"]),
        safety_stablecoin=pick("safety_stablecoin", _ENV_DEFAULTS["safety_stablecoin"]),
        bot_status=pick("bot_status", "paused"),  # começa pausado por segurança até backtest ser revisado
        bypass_macro_risk_window=pick("bypass_macro_risk_window", False),
    )

    apply_runtime_overrides(config)
    return config


def apply_runtime_overrides(config: RuntimeConfig) -> None:
    """Espelha os overrides do dashboard em `settings`. Os agentes leem
    `settings.max_allocation_pct_per_trade`, `min_confidence_to_trade`,
    `top_n_pairs` e `safety_stablecoin` em tempo de execução -- sem isto, editar
    esses valores em /settings não tinha efeito nenhum (só o .env valia).

    NUNCA toca em `dry_run` (segurança de capital, só via .env local)."""
    env_settings.max_allocation_pct_per_trade = config.max_allocation_pct_per_trade
    env_settings.min_confidence_to_trade = config.min_confidence_to_trade
    env_settings.top_n_pairs = config.top_n_pairs
    env_settings.safety_stablecoin = config.safety_stablecoin

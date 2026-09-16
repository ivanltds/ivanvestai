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
                return caster(overrides[key])
            except (TypeError, ValueError):
                return default
        return default

    return RuntimeConfig(
        cycle_interval_minutes=pick("cycle_interval_minutes", env_settings.cycle_interval_minutes),
        entry_decision_timeout_seconds=pick(
            "entry_decision_timeout_seconds", env_settings.entry_decision_timeout_seconds
        ),
        min_confidence_to_trade=pick("min_confidence_to_trade", env_settings.min_confidence_to_trade),
        max_allocation_pct_per_trade=pick(
            "max_allocation_pct_per_trade", env_settings.max_allocation_pct_per_trade
        ),
        daily_loss_alert_pct=pick("daily_loss_alert_pct", env_settings.daily_loss_alert_pct),
        top_n_pairs=pick("top_n_pairs", env_settings.top_n_pairs),
        safety_stablecoin=pick("safety_stablecoin", env_settings.safety_stablecoin),
        bot_status=pick("bot_status", "paused"),  # começa pausado por segurança até backtest ser revisado
        bypass_macro_risk_window=pick("bypass_macro_risk_window", False),
    )

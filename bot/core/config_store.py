"""Mescla os defaults do .env (config.settings) com overrides salvos pelo
dashboard na tabela `settings`. O dashboard escreve nessa tabela via API
route do Next.js; o bot lê no início de cada ciclo para pegar mudanças
recentes sem precisar reiniciar o processo.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from config.settings import settings as env_settings
from db.models import Setting
from db.session import get_session

logger = logging.getLogger(__name__)


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
    "final_max_fear_greed": (0, 100),
    "final_pause_daily_loss_pct": (0, 50),
    "final_pause_drawdown_pct": (0, 90),
    "final_pause_days": (0, 60),
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
    # Perfil de estratégia e travas do perfil final (29/09/2026) -- editáveis
    # em /settings (sempre globais, ver web/lib/settings-shared.ts).
    "strategy_profile": lambda v: v.strip().lower(),
    "final_max_fear_greed": float,
    "final_pause_daily_loss_pct": float,
    "final_pause_drawdown_pct": float,
    "final_pause_days": float,
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
    "strategy_profile": env_settings.strategy_profile,
    "final_max_fear_greed": env_settings.final_max_fear_greed,
    "final_pause_daily_loss_pct": env_settings.final_pause_daily_loss_pct,
    "final_pause_drawdown_pct": env_settings.final_pause_drawdown_pct,
    "final_pause_days": env_settings.final_pause_days,
}

_STRATEGY_PROFILES = ("legacy", "final")


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
    strategy_profile: str = "final"
    final_max_fear_greed: float = 50.0
    final_pause_daily_loss_pct: float = 3.0
    final_pause_drawdown_pct: float = 10.0
    final_pause_days: float = 7.0


def load_runtime_config(account_id: uuid.UUID | None = None) -> RuntimeConfig:
    """`account_id` (multi-conta-plano.md, Fase B/C/E -- ver 10.8): quando
    informado, mescla dois níveis -- primeiro os overrides "master"
    (account_id NULL na tabela settings: bot_status, seção 5.3, e o
    valor-base de qualquer chave até uma conta divergir), depois por CIMA os
    overrides da PRÓPRIA conta (account_id == account_id), que VENCEM em caso
    de a mesma chave existir nos dois níveis -- é assim que uma conta
    consegue ter, por exemplo, um max_allocation_pct_per_trade diferente da
    outra. Omitido (default), comportamento idêntico a antes: TODAS as linhas
    de settings entram, sem filtro nenhum -- é assim que main.py e chamadas
    legadas continuam funcionando sem mudança nenhuma. Desde 25/09/2026
    `Setting` tem chave composta de verdade (id substituto + índices únicos
    parciais, ver db/models.py e migrate_add_settings_composite_key.py) --
    antes disso só havia 1 linha global por chave, então account_id nunca
    filtrava nada de fato; agora filtra e a ordem do merge abaixo importa.
    """
    overrides: dict[str, str] = {}
    with get_session() as session:
        query = session.query(Setting)
        if account_id is not None:
            query = query.filter((Setting.account_id == account_id) | (Setting.account_id.is_(None)))
        # Ordena global (account_id NULL) primeiro, override da conta por
        # último -- o loop abaixo sobrescreve overrides[key] em ordem, então
        # o override específico da conta sempre vence sobre o master pra
        # mesma chave, nunca o contrário.
        query = query.order_by(Setting.account_id.isnot(None))
        for row in query.all():
            overrides[row.key] = row.value

    def pick(key: str, default):
        # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): todo desvio
        # pro default aqui era silencioso -- um valor inválido salvo na tabela
        # `settings` (corrompido manualmente, ou um bug futuro na validação do
        # lado do dashboard) fazia o bot ignorá-lo todo ciclo sem nenhuma pista
        # no log de por que "mudar em /settings não tem efeito".
        caster = _CASTERS.get(key, str)
        if key in overrides:
            raw = overrides[key]
            try:
                value = caster(raw)
            except (TypeError, ValueError):
                logger.warning("config_store: %s=%r não converteu (esperado %s) -- usando default %r.", key, raw, caster, default)
                return default
            if key in _RANGES and not (_RANGES[key][0] <= value <= _RANGES[key][1]):
                logger.warning("config_store: %s=%r fora da faixa válida %s -- usando default %r.", key, value, _RANGES[key], default)
                return default
            if key == "bot_status" and value not in ("running", "paused"):
                logger.warning("config_store: bot_status=%r inválido (esperado running/paused) -- usando default %r.", value, default)
                return default
            if key == "strategy_profile" and value not in _STRATEGY_PROFILES:
                logger.warning("config_store: strategy_profile=%r inválido (esperado legacy/final) -- usando default %r.", value, default)
                return default
            if key == "safety_stablecoin" and not (value.isalnum() and value.isupper()):
                logger.warning("config_store: safety_stablecoin=%r inválido -- usando default %r.", value, default)
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
        strategy_profile=pick("strategy_profile", _ENV_DEFAULTS["strategy_profile"]),
        final_max_fear_greed=pick("final_max_fear_greed", _ENV_DEFAULTS["final_max_fear_greed"]),
        final_pause_daily_loss_pct=pick("final_pause_daily_loss_pct", _ENV_DEFAULTS["final_pause_daily_loss_pct"]),
        final_pause_drawdown_pct=pick("final_pause_drawdown_pct", _ENV_DEFAULTS["final_pause_drawdown_pct"]),
        final_pause_days=pick("final_pause_days", _ENV_DEFAULTS["final_pause_days"]),
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
    env_settings.strategy_profile = config.strategy_profile
    env_settings.final_max_fear_greed = config.final_max_fear_greed
    env_settings.final_pause_daily_loss_pct = config.final_pause_daily_loss_pct
    env_settings.final_pause_drawdown_pct = config.final_pause_drawdown_pct
    env_settings.final_pause_days = config.final_pause_days

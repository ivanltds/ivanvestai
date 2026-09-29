"""Regras de saída de posição num lugar só, usadas pelo bot E pelo simulador.

Criado em 29/09/2026 (Plano de melhoria da estratégia, Fase 0). Objetivo: o
backtest testar EXATAMENTE a mesma lógica de saída que roda em produção -- até
aqui `run_backtest_multi_tf.py` só tinha stop/take estáticos, enquanto o bot
real usa stop móvel (execution_agent.update_trailing_stop) e pisos/tetos de
stop/take (risk_committee_agent). Números do simulador e do bot não eram
comparáveis.

Tudo aqui é função pura (sem banco, sem Binance, sem settings global): recebe
preços e uma `ExitConfig`, devolve números. O `ExitConfig()` padrão reproduz o
comportamento de produção de hoje (29/09/2026):

  - stop/take propostos pelo RiskCommitteeAgent, limitados a
    [min_stop_loss_pct, max_stop_loss_pct] e [min_take_profit_pct, max_take_profit_pct]
    (2%..10% e 4%..10%, config/settings.py);
  - stop móvel, quando o comitê liga, começa a seguir o preço IMEDIATAMENTE
    (ativação 0%) mantendo a MESMA distância inicial entrada->stop
    (core.order_utils.trailing_distance_pct);
  - alvo fixo sempre ativo, mesmo com stop móvel;
  - checagem na ordem stop -> alvo -> atualiza stop móvel
    (orchestrator/cycle_runner._manage_open_positions).

Os campos extras (`trailing_activation_pct`, `trailing_distance_pct`,
`take_profit_enabled`, `breakeven_on_trailing`, `max_hold_hours`) existem pras
Fases 2 e 3 do plano. Com o valor padrão, cada um fica neutro -- nada muda até
alguém ligar de propósito.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitConfig:
    # pisos/tetos aplicados ao que o comitê propõe (espelham config/settings.py)
    min_stop_loss_pct: float = 2.0
    max_stop_loss_pct: float = 10.0
    min_take_profit_pct: float = 4.0
    max_take_profit_pct: float = 10.0

    # --- Fase 2 (neutros por padrão) ---
    # lucro (%) sobre a entrada a partir do qual o stop móvel começa a seguir o
    # preço. 0 = comportamento atual (segue desde a abertura).
    trailing_activation_pct: float = 0.0
    # distância (%) do stop móvel abaixo do maior preço visto. None = mantém a
    # distância inicial entrada->stop (comportamento atual).
    trailing_distance_pct: float | None = None
    # False = sem alvo fixo; só stop / stop móvel encerram a posição.
    take_profit_enabled: bool = True
    # ao ativar o stop móvel, garante stop >= entrada + taxa da ida e volta
    breakeven_on_trailing: bool = False
    # fecha a posição se ficar esse tempo aberta SEM o stop móvel ativar. None = desligado.
    max_hold_hours: float | None = None

    # taxa por lado (fração), só usada pelo breakeven. 0.001 = 0,1%.
    fee_per_side: float = 0.001


@dataclass(frozen=True)
class Levels:
    stop_price: float
    take_price: float | None
    stop_loss_pct: float
    take_profit_pct: float | None


def clamp_committee_levels(stop_loss_pct: float, take_profit_pct: float, cfg: ExitConfig) -> tuple[float, float]:
    """Mesmos pisos/tetos que risk_committee_agent.py aplica depois do LLM."""
    stop = min(max(stop_loss_pct, cfg.min_stop_loss_pct), cfg.max_stop_loss_pct)
    take = min(max(take_profit_pct, cfg.min_take_profit_pct), cfg.max_take_profit_pct)
    return stop, take


def initial_levels(entry_price: float, stop_loss_pct: float, take_profit_pct: float, cfg: ExitConfig) -> Levels:
    """Stop e alvo da abertura, a partir do que o comitê propôs (já limitados)."""
    stop_pct, take_pct = clamp_committee_levels(stop_loss_pct, take_profit_pct, cfg)
    stop_price = entry_price * (1 - stop_pct / 100)
    if cfg.take_profit_enabled:
        return Levels(stop_price, entry_price * (1 + take_pct / 100), stop_pct, take_pct)
    return Levels(stop_price, None, stop_pct, None)


def trailing_is_armed(entry_price: float, reference_price: float, cfg: ExitConfig) -> bool:
    """O stop móvel já pode seguir o preço? (maior preço visto >= entrada + ativação)"""
    if cfg.trailing_activation_pct <= 0:
        return True
    return reference_price >= entry_price * (1 + cfg.trailing_activation_pct / 100)


def update_trailing(
    *,
    entry_price: float,
    reference_price: float,
    stop_price: float,
    initial_stop_pct: float,
    price: float,
    cfg: ExitConfig,
) -> tuple[float, float]:
    """Um passo do stop móvel. Devolve (nova_referência, novo_stop).

    O stop nunca desce. `reference_price` é o maior preço visto desde a
    abertura; `initial_stop_pct` é a distância entrada->stop da abertura
    (usada quando cfg.trailing_distance_pct é None -- comportamento atual).

    Nota de fidelidade: em produção a distância é recalculada a cada passo
    como (referência - stop)/referência (core.order_utils.trailing_distance_pct),
    o que dá o mesmo número que a distância inicial enquanto o stop só sobe
    junto com a referência. Usar a distância inicial fixa evita deriva numérica.
    """
    if price <= reference_price:
        return reference_price, stop_price
    new_ref = price
    if not trailing_is_armed(entry_price, new_ref, cfg):
        return new_ref, stop_price
    dist = cfg.trailing_distance_pct if cfg.trailing_distance_pct is not None else initial_stop_pct
    new_stop = new_ref * (1 - dist / 100)
    if cfg.breakeven_on_trailing:
        new_stop = max(new_stop, entry_price * (1 + 2 * cfg.fee_per_side))
    return new_ref, max(stop_price, new_stop)


def exit_hit(
    *, low: float, high: float, stop_price: float, take_price: float | None
) -> tuple[str, float] | None:
    """Checa saída dentro de uma vela (ou de um tick, com low == high == preço).

    Ordem conservadora igual à produção: stop antes do alvo. Numa vela que toca
    os dois, assume que o stop veio primeiro. Devolve (motivo, preço) ou None.
    """
    if low <= stop_price:
        return "stop_loss", stop_price
    if take_price is not None and high >= take_price:
        return "take_profit", take_price
    return None


def time_exit_due(hours_open: float, trailing_armed: bool, cfg: ExitConfig) -> bool:
    return cfg.max_hold_hours is not None and not trailing_armed and hours_open >= cfg.max_hold_hours

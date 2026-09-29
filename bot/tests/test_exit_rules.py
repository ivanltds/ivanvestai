"""Testes de core/exit_rules.py (Plano de melhoria da estratégia, Fase 0).

O mais importante aqui é o primeiro bloco: com `ExitConfig()` padrão, as regras
têm que reproduzir EXATAMENTE o que o bot faz hoje (risk_committee_agent +
execution_agent.update_trailing_stop + cycle_runner._manage_open_positions).
Se alguém mudar um padrão por engano, estes testes quebram.
"""
from __future__ import annotations

import pytest

from core.exit_rules import (
    ExitConfig,
    clamp_committee_levels,
    exit_hit,
    initial_levels,
    time_exit_due,
    trailing_is_armed,
    update_trailing,
)

CURRENT = ExitConfig()


# --- comportamento atual de produção -------------------------------------------

def test_clamp_matches_committee_floors_and_ceilings():
    assert clamp_committee_levels(0.5, 1.0, CURRENT) == (2.0, 4.0)      # pisos 2% / 4%
    assert clamp_committee_levels(150.0, 80.0, CURRENT) == (10.0, 10.0)  # tetos 10% / 10%
    assert clamp_committee_levels(3.0, 6.0, CURRENT) == (3.0, 6.0)


def test_initial_levels_like_execution_agent_open_position():
    lv = initial_levels(100.0, 3.0, 6.0, CURRENT)
    assert lv.stop_price == pytest.approx(97.0)   # fill * (1 - stop/100)
    assert lv.take_price == pytest.approx(106.0)  # fill * (1 + take/100)


def test_trailing_follows_immediately_keeping_initial_distance():
    # hoje: sem ativação mínima; stop sobe mantendo a distância entrada->stop (3%)
    ref, stop = update_trailing(entry_price=100, reference_price=100, stop_price=97,
                                initial_stop_pct=3.0, price=101, cfg=CURRENT)
    assert ref == 101
    assert stop == pytest.approx(101 * 0.97)


def test_trailing_never_moves_down_and_ignores_lower_prices():
    ref, stop = update_trailing(entry_price=100, reference_price=110, stop_price=106.7,
                                initial_stop_pct=3.0, price=105, cfg=CURRENT)
    assert (ref, stop) == (110, 106.7)


def test_stop_checked_before_take_like_cycle_runner():
    # vela que toca os dois: conservador, assume o stop
    assert exit_hit(low=96, high=107, stop_price=97, take_price=106) == ("stop_loss", 97)
    assert exit_hit(low=99, high=107, stop_price=97, take_price=106) == ("take_profit", 106)
    assert exit_hit(low=99, high=101, stop_price=97, take_price=106) is None


def test_defaults_mirror_settings_values():
    # config/settings.py em 29/09/2026: min_stop 2.0, max_stop 10.0, min_take 4.0, max_take 10.0
    assert (CURRENT.min_stop_loss_pct, CURRENT.max_stop_loss_pct) == (2.0, 10.0)
    assert (CURRENT.min_take_profit_pct, CURRENT.max_take_profit_pct) == (4.0, 10.0)
    assert CURRENT.trailing_activation_pct == 0.0
    assert CURRENT.trailing_distance_pct is None
    assert CURRENT.take_profit_enabled is True
    assert CURRENT.max_hold_hours is None


# --- parâmetros da Fase 2 (desligados por padrão) --------------------------------

def test_activation_delays_trailing_until_profit():
    cfg = ExitConfig(trailing_activation_pct=3.0, trailing_distance_pct=2.0)
    # +2%: ainda não arma, stop fica onde estava, mas a referência sobe
    ref, stop = update_trailing(entry_price=100, reference_price=100, stop_price=96,
                                initial_stop_pct=4.0, price=102, cfg=cfg)
    assert (ref, stop) == (102, 96)
    # +5%: arma e segue a 2% do topo
    ref, stop = update_trailing(entry_price=100, reference_price=102, stop_price=96,
                                initial_stop_pct=4.0, price=105, cfg=cfg)
    assert ref == 105
    assert stop == pytest.approx(105 * 0.98)
    assert trailing_is_armed(100, 105, cfg) and not trailing_is_armed(100, 102.9, cfg)


def test_no_take_profit_mode():
    lv = initial_levels(100.0, 3.0, 6.0, ExitConfig(take_profit_enabled=False))
    assert lv.take_price is None
    assert exit_hit(low=99, high=150, stop_price=97, take_price=None) is None


def test_breakeven_lifts_stop_above_entry_plus_fees():
    cfg = ExitConfig(trailing_activation_pct=3.0, trailing_distance_pct=5.0, breakeven_on_trailing=True, fee_per_side=0.001)
    _, stop = update_trailing(entry_price=100, reference_price=100, stop_price=96,
                              initial_stop_pct=4.0, price=103.5, cfg=cfg)
    # 5% abaixo de 103,5 = 98,3 < entrada; breakeven sobe para 100,2
    assert stop == pytest.approx(100.2)


def test_time_exit_only_when_trailing_not_armed():
    cfg = ExitConfig(max_hold_hours=48)
    assert time_exit_due(49, trailing_armed=False, cfg=cfg)
    assert not time_exit_due(49, trailing_armed=True, cfg=cfg)
    assert not time_exit_due(49, trailing_armed=False, cfg=CURRENT)

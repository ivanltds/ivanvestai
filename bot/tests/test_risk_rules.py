import datetime as dt

import pytest

from core.risk_rules import (
    circuit_breaker_triggered,
    in_macro_risk_window,
    max_allocation_ok,
    round_step_size,
)


@pytest.mark.parametrize(
    "qty,step,expected",
    [
        (1.23456, 0.01, 1.23),
        (0.3, 0.1, 0.3),  # sem ruído de ponto flutuante (0.30000000000000004)
        (0.000999, 0.001, 0.0),
        (5.0, 0, 5.0),
        (-1.0, 0.1, 0.0),
    ],
)
def test_round_step_size_rounds_down(qty, step, expected):
    assert round_step_size(qty, step) == pytest.approx(expected)


def test_circuit_breaker():
    assert circuit_breaker_triggered(100.0, 89.0, 0.10) is True
    assert circuit_breaker_triggered(100.0, 95.0, 0.10) is False
    assert circuit_breaker_triggered(0.0, 0.0, 0.10) is False


def test_max_allocation():
    assert max_allocation_ok(50, 100, 0.5) is True
    assert max_allocation_ok(51, 100, 0.5) is False
    assert max_allocation_ok(1, 0, 0.5) is False


def test_macro_window_fomc_blocks_whole_decision_day():
    # FOMC 15-16/09/2026: bloqueia o dia 16 (ET) inteiro
    assert in_macro_risk_window(dt.datetime(2026, 9, 16, 18, 0, tzinfo=dt.timezone.utc))[0] is True
    assert in_macro_risk_window(dt.datetime(2026, 9, 17, 18, 0, tzinfo=dt.timezone.utc))[0] is False


def test_macro_window_cpi_is_short():
    # CPI 11/09/2026 08:30 ET = 12:30 UTC (EDT), janela -15min/+30min
    assert in_macro_risk_window(dt.datetime(2026, 9, 11, 12, 20, tzinfo=dt.timezone.utc))[0] is True
    assert in_macro_risk_window(dt.datetime(2026, 9, 11, 13, 30, tzinfo=dt.timezone.utc))[0] is False


def test_suggested_order_value_caps_to_free_balance():
    from core.risk_rules import FREE_BALANCE_BUFFER, suggested_order_value

    # caso real dos logs: patrimônio $62,64, livre $30,43, teto 50% ($31,32)
    value = suggested_order_value(62.64, 0.5, 30.43)
    assert value == pytest.approx(30.43 * FREE_BALANCE_BUFFER)
    assert value < 30.43  # sobra folga pra taxa/slippage


def test_suggested_order_value_uses_pct_when_balance_is_enough():
    from core.risk_rules import suggested_order_value

    assert suggested_order_value(100.0, 0.5, 80.0) == pytest.approx(50.0)
    assert suggested_order_value(100.0, 0.5, None) == pytest.approx(50.0)
    assert suggested_order_value(100.0, 0.5, -5.0) == 0.0

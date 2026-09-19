import datetime as dt

import pytest

from core.equity import group_batches, market_pnl

T0 = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)


def rows(offset_min, assets):
    t = T0 + dt.timedelta(minutes=offset_min)
    return [(t, a, q, v) for a, (q, v) in assets.items()]


def test_group_batches_splits_by_gap():
    data = rows(0, {"BTC": (1, 100)}) + rows(15, {"BTC": (1, 110)})
    assert len(group_batches(data)) == 2


def test_price_change_counts_as_pnl():
    data = rows(0, {"NEAR": (10, 30.0)}) + rows(15, {"NEAR": (10, 33.0)})
    assert market_pnl(group_batches(data)) == pytest.approx(3.0)


def test_withdrawal_is_not_a_loss():
    # 14 NEAR -> 6 NEAR (retirada manual) ao mesmo preço: patrimônio cai, P&L de mercado é zero
    data = rows(0, {"NEAR": (14, 49.0)}) + rows(15, {"NEAR": (6, 21.0)})
    assert market_pnl(group_batches(data)) == pytest.approx(0.0)


def test_deposit_is_not_a_gain():
    data = rows(0, {"BTC": (0.001, 80.0)}) + rows(15, {"BTC": (0.002, 160.0)})
    assert market_pnl(group_batches(data)) == pytest.approx(0.0)


def test_only_price_move_on_previous_quantity_counts():
    # tinha 10 a $3,00; comprou +10 e o preço foi a $3,30 -> ganho só sobre os 10 iniciais ($3,00)
    data = rows(0, {"NEAR": (10, 30.0)}) + rows(15, {"NEAR": (20, 66.0)})
    assert market_pnl(group_batches(data)) == pytest.approx(3.0)


def test_new_and_closed_assets_do_not_count():
    data = rows(0, {"SUI": (27, 19.6)}) + rows(15, {"USDT": (19.9, 19.9)})
    assert market_pnl(group_batches(data)) == pytest.approx(0.0)

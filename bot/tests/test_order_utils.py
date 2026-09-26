import pytest

from core.order_utils import format_quantity, parse_market_fill, trailing_distance_pct


def _order(executed, quote, fills, status="FILLED"):
    return {"status": status, "executedQty": str(executed), "cummulativeQuoteQty": str(quote), "fills": fills}


def test_format_quantity_never_scientific_notation():
    assert format_quantity(1e-05) == "0.00001"
    assert format_quantity(0.5) == "0.5"
    assert format_quantity(123.0) == "123.0"


def test_buy_fee_in_base_asset_reduces_net_quantity():
    order = _order(1.0, 100.0, [
        {"qty": "0.6", "price": "100", "commission": "0.0006", "commissionAsset": "ZEC"},
        {"qty": "0.4", "price": "100", "commission": "0.0004", "commissionAsset": "ZEC"},
    ])
    fill = parse_market_fill(order, "BUY", "ZEC", fallback_price=1.0)
    assert fill.executed_quantity == 1.0
    assert fill.quantity == pytest.approx(0.999)
    assert fill.price == pytest.approx(100.0)
    assert fill.fee_asset == "ZEC"


def test_buy_fee_in_bnb_keeps_full_quantity():
    order = _order(2.0, 50.0, [{"qty": "2", "price": "25", "commission": "0.001", "commissionAsset": "BNB"}])
    fill = parse_market_fill(order, "BUY", "ZEC", fallback_price=1.0)
    assert fill.quantity == 2.0
    assert fill.fee_asset == "BNB"


def test_sell_does_not_deduct_base_fee_from_quantity():
    order = _order(1.0, 100.0, [{"qty": "1", "price": "100", "commission": "0.1", "commissionAsset": "USDT"}])
    fill = parse_market_fill(order, "SELL", "ZEC", fallback_price=1.0)
    assert fill.quantity == 1.0


def test_average_price_uses_cumulative_quote_qty():
    order = _order(2.0, 210.0, [{"qty": "1", "price": "100"}, {"qty": "1", "price": "110"}])
    assert parse_market_fill(order, "BUY", "X", 1.0).price == pytest.approx(105.0)


def test_unexecuted_order_raises():
    with pytest.raises(ValueError):
        parse_market_fill(_order(0, 0, [], status="EXPIRED"), "BUY", "ZEC", 1.0)


def test_trailing_distance_keeps_committee_distance():
    assert trailing_distance_pct(100.0, 95.0) == pytest.approx(5.0)


def test_trailing_distance_fallback():
    assert trailing_distance_pct(None, None) == 2.0
    assert trailing_distance_pct(100.0, 120.0) == 2.0  # stop acima da referência é inválido


def test_oco_price_levels_round_to_tick_and_keep_order():
    from decimal import Decimal

    from core.order_utils import oco_price_levels

    take, stop, limit = oco_price_levels(last_price=0.4218, take_price=0.43861, stop_price=0.41339, tick=0.0001)
    assert take == Decimal("0.4387")    # take arredonda pra CIMA
    assert stop == Decimal("0.4133")    # stop arredonda pra BAIXO
    assert limit < stop < Decimal("0.4218") < take


def test_oco_price_levels_rejects_price_already_beyond_level():
    from core.order_utils import oco_price_levels

    with pytest.raises(ValueError):
        oco_price_levels(last_price=0.40, take_price=0.44, stop_price=0.41, tick=0.0001)  # já abaixo do stop
    with pytest.raises(ValueError):
        oco_price_levels(last_price=0.45, take_price=0.44, stop_price=0.41, tick=0.0001)  # já acima do take


def test_summarize_oco_orders_take_profit():
    from core.order_utils import summarize_oco_orders

    orders = [
        {"type": "LIMIT_MAKER", "executedQty": "10", "cummulativeQuoteQty": "44.0", "orderId": 1, "updateTime": 111},
        {"type": "STOP_LOSS_LIMIT", "executedQty": "0", "cummulativeQuoteQty": "0", "orderId": 2},
    ]
    r = summarize_oco_orders(orders)
    assert r["reason"] == "take_profit" and r["quantity"] == 10 and r["price"] == pytest.approx(4.4)


def test_summarize_oco_orders_stop_loss_and_cancelled():
    from core.order_utils import summarize_oco_orders

    stop = [
        {"type": "LIMIT_MAKER", "executedQty": "0", "orderId": 1},
        {"type": "STOP_LOSS_LIMIT", "executedQty": "5", "cummulativeQuoteQty": "20.0", "orderId": 2},
    ]
    assert summarize_oco_orders(stop)["reason"] == "stop_loss"
    assert summarize_oco_orders([{"type": "LIMIT_MAKER", "executedQty": "0"}, {"type": "STOP_LOSS_LIMIT", "executedQty": "0"}]) is None

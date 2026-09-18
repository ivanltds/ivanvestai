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

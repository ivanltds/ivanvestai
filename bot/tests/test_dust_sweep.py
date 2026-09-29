"""Testes de core/dust_sweep.py (limpeza de poeira, 29/09/2026)."""
from __future__ import annotations

import datetime as dt

from core import dust_sweep as ds

NOW = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)


def test_base_asset():
    assert ds.base_asset("BCHUSDT") == "BCH"
    assert ds.base_asset("DOTUSDT") == "DOT"
    assert ds.base_asset("ETHBTC") == "ETH"


def test_pick_assets_skips_positions_bnb_stables_and_zero():
    details = [
        {"asset": "ada", "amountFree": "6.2"},
        {"asset": "BCH", "amountFree": "0.001"},   # posição aberta
        {"asset": "BNB", "amountFree": "0.001"},
        {"asset": "USDC", "amountFree": "0.3"},    # stablecoin
        {"asset": "XRP", "amountFree": "0"},
        {"asset": "DOGE", "amountFree": "bad"},
        {"asset": "ADA", "amountFree": "1"},       # repetida
    ]
    assert ds.pick_assets(details, {"BCH", "USDT"}) == ["ADA"]


def test_is_due():
    assert ds.is_due(None, NOW, 24)
    assert not ds.is_due(NOW - dt.timedelta(hours=23), NOW, 24)
    assert ds.is_due(NOW - dt.timedelta(hours=24), NOW, 24)

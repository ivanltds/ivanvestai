"""Testes de core/strategy_profile.py (perfil "final", 29/09/2026)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from config.settings import settings
from core import strategy_profile as sp
from core.exit_rules import exit_hit, update_trailing

NOW = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)


def _candles(closes, vol=1000.0, spread=0.01):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * (1 + spread), "low": c * (1 - spread), "close": c,
                         "volume": np.full(len(c), vol)})


# --- chave do perfil ---------------------------------------------------------------

def test_default_profile_is_final(monkeypatch):
    from config.settings import Settings
    monkeypatch.delenv("STRATEGY_PROFILE", raising=False)
    assert Settings.model_fields["strategy_profile"].default == "final"


def test_final_profile_switch(monkeypatch):
    monkeypatch.setattr(settings, "strategy_profile", " Final ")
    assert sp.is_final()


def test_marker_distinguishes_final_positions():
    assert sp.uses_final_exits(None, True)
    assert not sp.uses_final_exits(104.0, True)   # legacy com stop móvel: alvo sempre gravado
    assert not sp.uses_final_exits(None, False)


# --- saídas ----------------------------------------------------------------------

def test_final_exit_config_matches_tested_values():
    cfg = sp.final_exit_config()
    assert cfg.trailing_activation_pct == 3.0
    assert cfg.trailing_distance_pct == 2.0
    assert cfg.take_profit_enabled is False


def test_final_trailing_waits_for_3pct_then_follows_2pct():
    cfg = sp.final_exit_config()
    ref, stop = update_trailing(entry_price=100, reference_price=100, stop_price=95, initial_stop_pct=5,
                                price=102.5, cfg=cfg)
    assert stop == 95  # +2,5%: ainda não arma
    ref, stop = update_trailing(entry_price=100, reference_price=ref, stop_price=stop, initial_stop_pct=5,
                                price=110, cfg=cfg)
    assert stop == pytest.approx(110 * 0.98)
    assert exit_hit(low=108, high=111, stop_price=stop, take_price=None) is None
    assert exit_hit(low=107.5, high=111, stop_price=stop, take_price=None) == ("stop_loss", stop)


# --- entrada -------------------------------------------------------------------------

def test_btc_uptrend_true_on_rising_series():
    ok, why = sp.btc_uptrend(_candles(np.linspace(60_000, 80_000, 120)))
    assert ok, why


def test_btc_uptrend_false_on_falling_series():
    ok, _ = sp.btc_uptrend(_candles(np.linspace(80_000, 60_000, 120)))
    assert not ok


def test_btc_uptrend_false_when_price_under_rising_ema():
    closes = list(np.linspace(60_000, 80_000, 115)) + [70_000] * 5
    ok, _ = sp.btc_uptrend(_candles(closes))
    assert not ok


def test_btc_uptrend_false_without_history():
    ok, why = sp.btc_uptrend(_candles(np.linspace(1, 2, 30)))
    assert not ok and "insuficiente" in why


def test_universe_filters():
    liquid = _candles([10.0] * 120, vol=30_000)          # 96 x 10 x 30k = US$ 28,8 mi
    assert sp.universe_block_reason("ABCUSDT", liquid) is None
    assert "volume" in sp.universe_block_reason("ABCUSDT", _candles([10.0] * 120, vol=10_000))
    assert "preço" in sp.universe_block_reason("ABCUSDT", _candles([0.01] * 120, vol=1e11))
    assert sp.universe_block_reason("ONEUSDT", liquid) == "lista de bloqueio"


def test_stop_from_atr_1h_is_clamped():
    calm = _candles([100.0] * 60, spread=0.001)     # ATR ~0,2 -> 0,4% -> piso de 2%
    wild = _candles([100.0] * 60, spread=0.10)      # ATR ~20 -> 40% -> teto de 8%
    mid = _candles([100.0] * 60, spread=0.0075)     # ATR ~1,5 -> 3%
    assert sp.stop_pct_from_atr_1h(calm, 100.0) == 2.0
    assert sp.stop_pct_from_atr_1h(wild, 100.0) == 8.0
    assert sp.stop_pct_from_atr_1h(mid, 100.0) == pytest.approx(3.0, rel=0.05)
    with pytest.raises(ValueError):
        sp.stop_pct_from_atr_1h(calm, 0.0)


# --- pausa de entradas ------------------------------------------------------------------

def _decide(equity, pnl_pct, state):
    return sp.entry_pause_decision(now=NOW, equity=equity, day_market_pnl_pct=pnl_pct, state=state,
                                   daily_loss_pct=3, drawdown_pct=10, pause_days=7)


def test_no_pause_in_normal_conditions():
    reason, st = _decide(100, -0.01, sp.PauseState())
    assert reason is None and st.peak_equity == 100


def test_daily_loss_pause():
    reason, _ = _decide(100, -0.031, sp.PauseState(peak_equity=100))
    assert reason and "hoje" in reason


def test_drawdown_pause_then_resets_peak_after_pause():
    reason, st = _decide(89, 0.0, sp.PauseState(peak_equity=100))
    assert reason and st.paused_until == NOW + dt.timedelta(days=7)
    # ainda dentro da pausa
    r2, st2 = sp.entry_pause_decision(now=NOW + dt.timedelta(days=3), equity=95, day_market_pnl_pct=0.0, state=st,
                                      daily_loss_pct=3, drawdown_pct=10, pause_days=7)
    assert r2 and st2.paused_until == st.paused_until
    # fim da pausa: pico recomeça no patrimônio atual (não fica travado pra sempre)
    r3, st3 = sp.entry_pause_decision(now=NOW + dt.timedelta(days=8), equity=85, day_market_pnl_pct=0.0, state=st,
                                      daily_loss_pct=3, drawdown_pct=10, pause_days=7)
    assert r3 is None and st3.peak_equity == 85 and st3.paused_until is None


def test_unknown_day_base_does_not_pause():
    reason, _ = _decide(100, None, sp.PauseState(peak_equity=100))
    assert reason is None


# --- trava de Medo e Ganância ---------------------------------------------------------

def test_fear_greed_default_limit_is_50():
    assert settings.final_max_fear_greed == 50.0


@pytest.mark.parametrize("value,limit,blocked", [
    (51, 50, True), (73, 50, True), (50, 50, False), (20, 50, False),
    (None, 50, False),  # índice desconhecido libera (igual ao simulador)
    (90, 0, False),     # 0 desliga a trava
])
def test_fear_greed_block_reason(value, limit, blocked):
    assert (sp.fear_greed_block_reason(value, limit) is not None) == blocked

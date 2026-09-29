"""Perfil de estratégia "final" (Plano de melhoria da estratégia, 29/09/2026).

Liga com `STRATEGY_PROFILE=final` no .env (padrão: `legacy` = comportamento de
antes). Mesmo padrão de opt-in manual de dry_run / enable_oco: só muda editando
o .env e reiniciando o main.py, nunca pelo dashboard.

O que o perfil "final" muda (validado no simulador run_backtest_v2.py, ver o
plano "Plano de melhoria da estratégia -- IvanVestAI"):

  Entrada
    - Filtro de mercado: só abre compra com o BTC em alta no 4h (fechamento acima
      da EMA 50 do 4h e EMA 50 subindo nas últimas 6 velas).
    - Universo: preço >= US$ 0,05, volume de 24h >= US$ 20 mi, fora da lista de
      bloqueio (KITE, AUDIO, MUBARAK, ONE, BANK por padrão).
    - Decisão por REGRA: a IA de viabilidade só registra a opinião dela (pra
      medir depois se ajudaria); o RiskCommitteeAgent não é chamado.
    - Pausa de entradas: após -3% de variação de MERCADO no dia (aportes e
      retiradas não contam) ou -10% em relação ao pico de patrimônio (pausa de
      7 dias; depois recomeça com um pico novo).
  Saída
    - Stop inicial = 2 x ATR(14) do 1h, entre 2% e 8% abaixo da entrada.
    - Sem alvo fixo. Stop móvel só começa depois de +3% de lucro e segue 2%
      abaixo do maior preço visto.
    - Posições abertas ANTES da troca continuam com as regras antigas
      (marcador: posição do perfil final tem take_price vazio -- ver
      `uses_final_exits`).

Aviso honesto (registrado no plano): a versão passou no critério em jun-set/2026
com US$ 10 mil simulados, mas PERDEU na prova final de out/2025-mar/2026
(mercado de baixa) -- só perdeu bem menos que a estratégia de antes. Foi para
produção por decisão explícita do Ivan em 29/09/2026.

Tudo aqui é função pura, sem banco nem Binance, pra ser testável
(tests/test_strategy_profile.py).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import pandas as pd
import pandas_ta as ta

from config.settings import settings
from core.exit_rules import ExitConfig

PROFILE_FINAL = "final"


def is_final() -> bool:
    return settings.strategy_profile.strip().lower() == PROFILE_FINAL


def final_exit_config() -> ExitConfig:
    return ExitConfig(
        min_stop_loss_pct=settings.final_stop_min_pct,
        max_stop_loss_pct=settings.final_stop_max_pct,
        trailing_activation_pct=settings.final_trailing_activation_pct,
        trailing_distance_pct=settings.final_trailing_distance_pct,
        take_profit_enabled=False,
    )


def uses_final_exits(take_price: float | None, trailing_active: bool) -> bool:
    """Posição aberta pelo perfil final: sem alvo e com stop móvel. No perfil
    legacy o alvo é sempre gravado (piso de 4%), então não há ambiguidade."""
    return take_price is None and bool(trailing_active)


# --- Entrada ------------------------------------------------------------------

def btc_uptrend(df_btc_4h: pd.DataFrame) -> tuple[bool, str]:
    """BTC em alta no 4h: fechamento > EMA50 e EMA50 maior que 6 velas atrás.
    Devolve (ok, explicação). Sem dados suficientes -> (False, ...) por segurança."""
    close = df_btc_4h["close"].astype(float)
    if len(close) < 60:
        return False, f"histórico insuficiente do BTC no 4h ({len(close)} velas)"
    ema50 = ta.ema(close, length=50)
    last, e_now, e_prev = float(close.iloc[-1]), float(ema50.iloc[-1]), float(ema50.iloc[-7])
    if any(math.isnan(x) for x in (e_now, e_prev)):
        return False, "EMA50 do BTC indisponível"
    ok = last > e_now and e_now > e_prev
    why = (f"BTC ${last:,.0f} {'>' if last > e_now else '<='} EMA50 ${e_now:,.0f}; "
           f"EMA50 {'subindo' if e_now > e_prev else 'caindo'} (6 velas atrás ${e_prev:,.0f})")
    return ok, why


def _denylist() -> set[str]:
    out = set()
    for item in settings.final_pair_denylist.split(","):
        item = item.strip().upper()
        if item:
            out.add(item if item.endswith(settings.safety_stablecoin) else item + settings.safety_stablecoin)
    return out


def universe_block_reason(pair: str, df_15m: pd.DataFrame) -> str | None:
    """Motivo pra ignorar o par no perfil final, ou None. Volume de 24h calculado
    das 96 últimas velas de 15m (preço x volume), igual ao simulador."""
    if pair.upper() in _denylist():
        return "lista de bloqueio"
    price = float(df_15m["close"].iloc[-1])
    if price < settings.final_min_price_usdt:
        return f"preço ${price:.4f} abaixo de ${settings.final_min_price_usdt}"
    tail = df_15m.tail(96)
    quote_vol = float((tail["volume"].astype(float) * tail["close"].astype(float)).sum())
    if quote_vol < settings.final_min_quote_volume_24h:
        return f"volume 24h ${quote_vol / 1e6:.1f} mi abaixo de ${settings.final_min_quote_volume_24h / 1e6:.0f} mi"
    return None


def stop_pct_from_atr_1h(df_1h: pd.DataFrame, price: float) -> float:
    """Stop inicial em % = mult x ATR(14) do 1h / preço, limitado ao piso/teto.
    Levanta ValueError se o ATR não puder ser calculado (o chamador pula a entrada)."""
    atr = ta.atr(df_1h["high"].astype(float), df_1h["low"].astype(float), df_1h["close"].astype(float), length=14)
    value = float(atr.iloc[-1]) if atr is not None and len(atr) else float("nan")
    if not (value > 0) or not (price > 0):
        raise ValueError("ATR de 1h indisponível")
    pct = value * settings.final_stop_atr_1h_mult / price * 100
    return min(max(pct, settings.final_stop_min_pct), settings.final_stop_max_pct)


def fear_greed_block_reason(value: float | None, limit: float) -> str | None:
    """Trava de Medo e Ganância do perfil final (mesma regra do simulador,
    run_backtest_v2.py --max-medo-ganancia): bloqueia compra nova só quando o
    índice é CONHECIDO e maior que `limit`. limit <= 0 desliga; índice
    desconhecido libera. Fonte: Alternative.me Crypto Fear & Greed Index."""
    if not limit or limit <= 0 or value is None:
        return None
    if value > limit:
        return f"Medo e Ganância {value:g} acima de {limit:g}"
    return None


def current_fear_greed(max_age_hours: float = 48.0) -> tuple[float | None, str]:
    """Índice de agora: alternative.me; se falhar, último valor gravado pelo
    coletor (tabela market_metrics) com até `max_age_hours`. Devolve
    (valor ou None, origem). Nunca levanta exceção."""
    try:
        from core.market_metrics import fetch_fear_greed
        value = fetch_fear_greed()
        if value is not None:
            return float(value), "alternative.me"
    except Exception:  # noqa: BLE001 -- fonte opcional
        pass
    try:
        from db.models import MarketMetric
        from db.session import get_session
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=max_age_hours)
        with get_session() as session:
            row = (session.query(MarketMetric)
                   .filter(MarketMetric.fear_greed.isnot(None), MarketMetric.timestamp >= cutoff)
                   .order_by(MarketMetric.timestamp.desc()).first())
            if row is not None:
                return float(row.fear_greed), f"banco ({row.timestamp:%d/%m %H:%M} UTC)"
    except Exception:  # noqa: BLE001
        pass
    return None, "indisponível"


# --- Pausa de entradas ----------------------------------------------------------

@dataclass
class PauseState:
    peak_equity: float | None = None
    paused_until: dt.datetime | None = None


def entry_pause_decision(
    *, now: dt.datetime, equity: float, day_market_pnl_pct: float | None, state: PauseState,
    daily_loss_pct: float, drawdown_pct: float, pause_days: float,
) -> tuple[str | None, PauseState]:
    """Decide se a conta pode abrir posições novas agora. Mesma regra do simulador:

    - variação de mercado do dia <= -daily_loss_pct%  -> sem entradas hoje;
    - patrimônio < pico x (1 - drawdown_pct%)         -> pausa de pause_days dias;
      ao fim da pausa o pico recomeça no patrimônio do momento.

    `day_market_pnl_pct` é FRAÇÃO (-0.03 = -3%), como sai do circuit breaker
    (aportes/retiradas já excluídos). None = ainda sem base do dia.
    Devolve (motivo ou None, novo estado a gravar)."""
    state = PauseState(state.peak_equity, state.paused_until)
    if state.paused_until is not None:
        if now < state.paused_until:
            return f"pausa por queda até {state.paused_until:%d/%m %H:%M} UTC", state
        state = PauseState(peak_equity=equity, paused_until=None)
    if equity > 0 and (state.peak_equity is None or equity > state.peak_equity):
        state.peak_equity = equity
    if drawdown_pct and state.peak_equity and equity < state.peak_equity * (1 - drawdown_pct / 100):
        state.paused_until = now + dt.timedelta(days=pause_days)
        return (f"patrimônio ${equity:,.2f} caiu mais de {drawdown_pct:g}% do pico ${state.peak_equity:,.2f} "
                f"-- pausa de {pause_days:g} dias"), state
    if daily_loss_pct and day_market_pnl_pct is not None and day_market_pnl_pct <= -daily_loss_pct / 100:
        return f"variação de mercado hoje {day_market_pnl_pct:+.1%} (limite -{daily_loss_pct:g}%)", state
    return None, state

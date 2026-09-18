"""Regras de risco transversais: circuit breaker, correlação, setor,
tamanho máximo por operação, meme coin scoring, janelas de risco
(macro + pós-notícia + volatilidade extrema). Ver indicadores-estrategias.md.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from config.settings import settings

with open(settings.sector_map_path, encoding="utf-8") as f:
    _SECTOR_DATA = json.load(f)

with open(settings.macro_calendar_path, encoding="utf-8") as f:
    _MACRO_CALENDAR = json.load(f)

_ASSET_TO_SECTOR: dict[str, str] = {}
for sector, assets in _SECTOR_DATA["sectors"].items():
    for asset in assets:
        # um ativo pode aparecer em mais de um setor (ex: BNB é L1 e exchange token);
        # mantemos o primeiro encontrado como setor primário.
        _ASSET_TO_SECTOR.setdefault(asset, sector)


def sector_of(asset: str) -> str:
    return _ASSET_TO_SECTOR.get(asset.upper(), _SECTOR_DATA.get("default_sector", "unknown"))


def is_stablecoin(asset: str) -> bool:
    return asset.upper() in _SECTOR_DATA["sectors"].get("stablecoin_excluded", [])


def max_allocation_ok(order_value: float, total_equity: float, max_pct: float) -> bool:
    if total_equity <= 0:
        return False
    return (order_value / total_equity) <= max_pct


def round_step_size(quantity: float, step_size: float) -> float:
    """Arredonda `quantity` PRA BAIXO pro múltiplo válido do filtro LOT_SIZE
    da Binance (`stepSize`) -- obrigatório pra Binance aceitar a ordem.
    Arredondar pra baixo (nunca pra cima) garante que a ordem nunca peça mais
    do que o valor calculado (evita erro de saldo insuficiente pela sobra do
    arredondamento). Bug corrigido nesta revisão: antes, nenhum lugar do
    código fazia esse ajuste -- ver arquitetura-tecnica.md 9.6."""
    if step_size <= 0 or quantity <= 0:
        return max(quantity, 0.0)
    # Decimal (não float): 0.3 / 0.1 = 2.9999999999999996 em float, o que fazia
    # floor() devolver 2 passos e arredondar 0.3 pra 0.2 (achado nos testes,
    # 18/09/2026). Decimal(str(x)) usa a representação decimal "de tela" dos dois.
    q, step = Decimal(str(quantity)), Decimal(str(step_size))
    return float((q // step) * step)


def correlation_ok(correlation_with_open_position: float, threshold: float = 0.75) -> bool:
    return abs(correlation_with_open_position) <= threshold


def circuit_breaker_triggered(equity_start_of_day: float, equity_now: float, alert_pct: float) -> bool:
    if equity_start_of_day <= 0:
        return False
    drawdown = (equity_start_of_day - equity_now) / equity_start_of_day
    return drawdown >= alert_pct


@dataclass
class MemeCoinSignals:
    volume_zscore: float
    social_sentiment_score: float  # -1 a 1, vindo do NewsAgent
    price_momentum_4h_pct: float
    price_momentum_24h_pct: float


def meme_coin_eligible(signals: MemeCoinSignals) -> bool:
    """Precisa de pelo menos 2 dos 3 sinais elevados para entrar na cesta
    de alto risco tolerado (tolerância de 5% da carteira)."""
    hits = 0
    if signals.volume_zscore >= 2.0:
        hits += 1
    if signals.social_sentiment_score >= 0.5:
        hits += 1
    if signals.price_momentum_4h_pct >= 8.0 or signals.price_momentum_24h_pct >= 20.0:
        hits += 1
    return hits >= 2


def in_macro_risk_window(now_utc: dt.datetime) -> tuple[bool, str | None]:
    """True se `now_utc` cai dentro da janela de bloqueio ao redor de um
    evento FOMC/CPI (window_minutes_before/after, definidos no calendário)."""
    before = dt.timedelta(minutes=_MACRO_CALENDAR.get("window_minutes_before", 15))
    after = dt.timedelta(minutes=_MACRO_CALENDAR.get("window_minutes_after", 30))
    et = ZoneInfo("America/New_York")

    for event in _MACRO_CALENDAR["events"]:
        if event["type"] == "CPI":
            event_dt = dt.datetime.strptime(
                f"{event['date']} {event.get('time_et', '08:30')}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=et)
            window_start = event_dt - before
            window_end = event_dt + after
            if window_start <= now_utc.astimezone(et) <= window_end:
                return True, event["label"]
        else:  # FOMC: bloqueia o dia inteiro do segundo dia (quando sai o comunicado), por segurança
            end_date = dt.date.fromisoformat(event.get("end_date", event["date"]))
            if now_utc.astimezone(et).date() == end_date:
                return True, event["label"]

    return False, None


def volatility_extreme(candle_range_pct: float, atr_pct_avg: float, volume_ratio: float,
                        std_multiplier: float = 3.0, volume_multiplier: float = 3.0) -> bool:
    """Heurística simples: candle com range muito acima do ATR médio recente,
    ou volume muito acima da média — trata como evento de volatilidade extrema."""
    return candle_range_pct > (atr_pct_avg * std_multiplier) or volume_ratio > volume_multiplier

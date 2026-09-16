"""Varre o Top N por liquidez, calcula indicadores multi-timeframe,
classifica o regime de mercado e propõe oportunidades com a estratégia
mais adequada por ativo."""
from __future__ import annotations

from dataclasses import dataclass

from agents.base import BaseAgent
from config.settings import settings
from core.binance_client import binance_client
from core.indicators import (
    confluence_score,
    market_regime,
    score_breakout,
    score_mean_reversion,
    score_trend_following,
)
from core.risk_rules import is_stablecoin


@dataclass
class ScannerOpportunity:
    pair: str
    strategy: str
    regime: str
    confluence: int
    votes_summary: dict


class MarketScannerAgent(BaseAgent):
    name = "market_scanner_agent"
    model = ""  # análise quantitativa determinística; o veredito qualitativo fica com o ViabilityAgent

    def run(self) -> list[ScannerOpportunity]:
        pairs = binance_client.get_top_pairs_by_volume(
            quote=settings.safety_stablecoin, top_n=settings.top_n_pairs
        )
        opportunities: list[ScannerOpportunity] = []

        for pair in pairs:
            base_asset = pair.removesuffix(settings.safety_stablecoin)
            if is_stablecoin(base_asset):
                continue

            try:
                df_4h = binance_client.get_klines_df(pair, "4h", limit=120)
                df_1h = binance_client.get_klines_df(pair, "1h", limit=120)
                df_15m = binance_client.get_klines_df(pair, "15m", limit=120)
            except Exception:
                continue  # par sem histórico suficiente ou erro de API — pula neste ciclo

            regime = market_regime(df_4h)

            if regime == "trend":
                votes = score_trend_following(df_1h, df_15m)
                strategy = "trend_following"
            else:
                votes = score_mean_reversion(df_15m)
                strategy = "mean_reversion"

            # Breakout é avaliado em paralelo independente do regime —
            # um rompimento pode ocorrer mesmo saindo de lateralização.
            breakout_votes = score_breakout(df_15m)
            if confluence_score(breakout_votes) >= 2:
                votes = breakout_votes
                strategy = "breakout"

            score = confluence_score(votes)
            if score >= 2:
                opportunities.append(
                    ScannerOpportunity(
                        pair=pair,
                        strategy=strategy,
                        regime=regime,
                        confluence=score,
                        votes_summary={v.name: {"vote": v.vote, "value": v.value} for v in votes},
                    )
                )

        return opportunities

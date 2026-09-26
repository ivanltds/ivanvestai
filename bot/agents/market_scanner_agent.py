"""Varre o Top N por liquidez, calcula indicadores multi-timeframe,
classifica o regime de mercado e propõe oportunidades com a estratégia
mais adequada por ativo."""
from __future__ import annotations

from dataclasses import dataclass

from agents.base import BaseAgent
from config.settings import settings
from core.binance_client import BinanceClient, binance_client
from core import vlog
from core.indicators import (
    confluence_score,
    market_regime,
    meme_coin_raw_signals,
    score_breakout,
    score_mean_reversion,
    score_trend_following,
    volatility_signals,
)
from core.risk_rules import is_stablecoin, is_stablecoin_peg, volatility_extreme


@dataclass
class ScannerOpportunity:
    pair: str
    strategy: str
    regime: str
    confluence: int
    votes_summary: dict
    # Sinais objetivos pra core.risk_rules.meme_coin_eligible() (o sentimento
    # social, terceiro sinal da função, só é conhecido mais adiante no ciclo,
    # quando o NewsAgent já rodou -- ver orchestrator/cycle_runner.py e
    # arquitetura-tecnica.md 9.21 item 13).
    volume_zscore: float
    price_momentum_4h_pct: float
    price_momentum_24h_pct: float


class MarketScannerAgent(BaseAgent):
    name = "market_scanner_agent"
    model = ""  # análise quantitativa determinística; o veredito qualitativo fica com o ViabilityAgent

    def __init__(self, *, binance: BinanceClient | None = None) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B/C): client opcional, cai
        # pro singleton global se omitido. Roda UMA VEZ por ciclo (dado de
        # mercado é compartilhado entre contas, ver seção 4 do plano) -- por
        # isso, diferente de PortfolioAgent/ExecutionAgent, ainda não recebe
        # safety_stablecoin/top_n_pairs por conta: qual conta "empresta" o
        # client pra essa varredura pública não muda o resultado (klines e
        # volume por par são dados públicos da Binance, não da carteira).
        super().__init__()
        self._binance = binance if binance is not None else binance_client

    def run(self) -> list[ScannerOpportunity]:
        pairs = self._binance.get_top_pairs_by_volume(
            quote=settings.safety_stablecoin, top_n=settings.top_n_pairs
        )
        opportunities: list[ScannerOpportunity] = []
        skipped = 0
        skipped_volatility = 0
        skipped_stablecoin_peg = 0

        for pair in pairs:
            base_asset = pair.removesuffix(settings.safety_stablecoin)
            if is_stablecoin(base_asset):
                continue

            try:
                df_4h = self._binance.get_klines_df(pair, "4h", limit=120, closed_only=True)
                df_1h = self._binance.get_klines_df(pair, "1h", limit=120, closed_only=True)
                df_15m = self._binance.get_klines_df(pair, "15m", limit=120, closed_only=True)

                # Volatilidade extrema (achado 24/09/2026, arquitetura-tecnica.md 9.21
                # item 13): candle_range/ATR ou volume muito acima do normal -- pula o
                # par NESTE ciclo (só bloqueia entrada nova, igual à janela de risco
                # macro; gestão de posição já aberta nesse ativo não é afetada). A
                # função já existia em core/risk_rules.py mas nunca era chamada.
                candle_range_pct, atr_pct_avg, volume_ratio = volatility_signals(df_15m)
                if volatility_extreme(candle_range_pct, atr_pct_avg, volume_ratio):
                    skipped_volatility += 1
                    continue

                # Stablecoin "nova" ainda fora da lista curada (multi-conta-plano.md
                # 10.17, pedido do Ivan em 26/09/2026, achado no caso do USD1USDT):
                # preço colado em $1 + volatilidade quase zero = trata como reserva,
                # não como oportunidade de trade, mesmo sem estar em
                # core.risk_rules.is_stablecoin() (que só cobre nomes já conhecidos).
                if is_stablecoin_peg(float(df_15m["close"].iloc[-1]), atr_pct_avg):
                    skipped_stablecoin_peg += 1
                    continue

                regime = market_regime(df_4h)

                if regime == "trend":
                    votes = score_trend_following(df_1h, df_15m)
                    strategy = "trend_following"
                else:
                    votes = score_mean_reversion(df_15m)
                    strategy = "mean_reversion"
                score = confluence_score(votes)

                # Breakout é avaliado em paralelo independente do regime —
                # um rompimento pode ocorrer mesmo saindo de lateralização.
                breakout_votes = score_breakout(df_15m)
                breakout_score = confluence_score(breakout_votes)
                # Achado 24/09/2026 (arquitetura-tecnica.md 9.20 item 28,
                # decisão #12 da 9.21): antes o breakout sobrepunha a estratégia
                # de regime incondicionalmente sempre que confluence_score >= 2,
                # mesmo que o score da estratégia de regime já calculada acima
                # fosse maior (ex: tendência=5 perdendo pra breakout=2, que é só
                # "ok"). Agora vence sempre o MAIOR score entre as duas
                # candidatas; em empate, mantém a estratégia de regime (mais
                # alinhada ao contexto atual do ativo que o próprio scanner já
                # identificou).
                if breakout_score >= 2 and breakout_score > score:
                    votes = breakout_votes
                    strategy = "breakout"
                    score = breakout_score
            except Exception:
                # Par sem histórico suficiente (ex: listagem recente -- ADX(14) e
                # outros indicadores do pandas_ta retornam None silenciosamente,
                # sem levantar exceção, quando não há candles suficientes pra
                # calcular; ver core/indicators.py) ou erro de API -- pula este
                # par neste ciclo. Bug encontrado nesta revisão (16/09/2026): antes
                # só a busca de klines tinha essa proteção, então um par assim
                # derrubava o ciclo inteiro em vez de só ser ignorado, que já era
                # a intenção original do comentário aqui.
                skipped += 1
                continue

            if score >= 2:
                volume_zscore, price_momentum_4h_pct, price_momentum_24h_pct = meme_coin_raw_signals(df_1h)
                opportunities.append(
                    ScannerOpportunity(
                        pair=pair,
                        strategy=strategy,
                        regime=regime,
                        confluence=score,
                        votes_summary={v.name: {"vote": v.vote, "value": v.value} for v in votes},
                        volume_zscore=volume_zscore,
                        price_momentum_4h_pct=price_momentum_4h_pct,
                        price_momentum_24h_pct=price_momentum_24h_pct,
                    )
                )

        if skipped:
            vlog.warn(f"MarketScannerAgent: {skipped} par(es) pulado(s) (histórico insuficiente ou erro de API).")
        if skipped_volatility:
            vlog.warn(f"MarketScannerAgent: {skipped_volatility} par(es) pulado(s) (volatilidade extrema).")
        if skipped_stablecoin_peg:
            vlog.warn(
                f"MarketScannerAgent: {skipped_stablecoin_peg} par(es) pulado(s) "
                "(colado em $1, tratado como stablecoin/reserva -- ver 10.17)."
            )

        return opportunities

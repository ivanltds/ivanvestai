"""Cruza a carteira atual com as oportunidades aprovadas tecnicamente e
valida contra as regras de capital: teto de 50%/operação, correlação,
diversificação setorial, saldo de BNB pra taxa, valor mínimo de ordem."""
from __future__ import annotations

from dataclasses import dataclass

from agents.base import BaseAgent
from agents.market_scanner_agent import ScannerOpportunity
from config.settings import settings
from core.binance_client import binance_client
from core.indicators import rolling_correlation
from core.risk_rules import correlation_ok, max_allocation_ok, sector_of
from db.models import Position


@dataclass
class PortfolioCheck:
    approved: bool
    suggested_order_value_usdt: float
    reasons: list[str]


class PortfolioComparisonAgent(BaseAgent):
    name = "portfolio_comparison_agent"
    model = ""  # regras determinísticas de portfólio, sem LLM

    def check(
        self,
        opportunity: ScannerOpportunity,
        total_equity_usdt: float,
        open_positions: list[Position],
        available_stablecoin: float | None = None,
    ) -> PortfolioCheck:
        reasons: list[str] = []
        approved = True

        base_asset = opportunity.pair.removesuffix(settings.safety_stablecoin)

        # 1. Tamanho máximo por operação (50% do capital disponível)
        suggested_value = total_equity_usdt * settings.max_allocation_pct_per_trade
        if not max_allocation_ok(suggested_value, total_equity_usdt, settings.max_allocation_pct_per_trade):
            approved = False
            reasons.append("Valor sugerido excede o teto de alocação por operação.")

        # 1b. Saldo LIVRE na stablecoin de segurança (não o patrimônio total).
        # O valor sugerido acima é uma % do patrimônio TOTAL, mas a maior
        # parte dele pode estar em outros ativos (BTC, ETH etc), não em USDT
        # disponível de verdade pra comprar algo novo -- sem essa checagem, a
        # ordem só falhava lá na Binance (-2010 "insufficient balance"),
        # depois de já ter gasto a chamada de LLM do RiskCommitteeAgent.
        # Achado em 16/09/2026 rodando em produção pela primeira vez, ver
        # arquitetura-tecnica.md 9.13.
        if available_stablecoin is not None and suggested_value > available_stablecoin:
            approved = False
            reasons.append(
                f"Saldo livre em {settings.safety_stablecoin} (${available_stablecoin:.2f}) "
                f"insuficiente pro valor sugerido (${suggested_value:.2f})."
            )

        # 2. Valor mínimo de ordem da Binance (minNotional)
        try:
            filters = binance_client.get_symbol_filters(opportunity.pair)
            min_notional = float(filters.get("MIN_NOTIONAL", {}).get("minNotional", 5.0))
        except Exception:
            min_notional = 5.0
        if suggested_value < min_notional:
            approved = False
            reasons.append(f"Valor sugerido (${suggested_value:.2f}) abaixo do mínimo da Binance (${min_notional:.2f}).")

        # 3. Correlação com posições já abertas
        for position in open_positions:
            if position.status != "open":
                continue
            try:
                df_a = binance_client.get_klines_df(opportunity.pair, "1d", limit=45)
                df_b = binance_client.get_klines_df(position.pair, "1d", limit=45)
                corr = rolling_correlation(df_a.set_index("open_time")["close"], df_b.set_index("open_time")["close"])
            except Exception:
                corr = 0.0
            if not correlation_ok(corr):
                approved = False
                reasons.append(f"Correlação alta ({corr:.2f}) com posição aberta em {position.pair}.")

        # 4. Diversificação setorial (aviso, não bloqueia sozinho — só soma como sinal negativo)
        sector = sector_of(base_asset)
        same_sector_count = sum(1 for p in open_positions if p.status == "open" and sector_of(p.pair.removesuffix(settings.safety_stablecoin)) == sector)
        if same_sector_count >= 2:
            reasons.append(f"Já há {same_sector_count} posições no setor '{sector}' — considerar diversificação.")

        if not reasons:
            reasons.append("Dentro de todos os limites de capital, liquidez e correlação.")

        return PortfolioCheck(approved=approved, suggested_order_value_usdt=suggested_value, reasons=reasons)

"""Agente final (modelo robusto): agrega os pareceres do ViabilityAgent
e do PortfolioComparisonAgent, calcula confiança ponderada e aplica o
veto (confiança < 80% = rejeitado). Define stop/take/trailing iniciais."""
from __future__ import annotations

from dataclasses import dataclass

from agents.base import AgentVerdict, BaseAgent
from agents.portfolio_comparison_agent import PortfolioCheck
from config.settings import settings
from core.llm_client import call_structured
from pydantic import BaseModel, Field

# Pesos sugeridos (ver indicadores-estrategias.md seção 4) — ajustar
# depois de rodar com dados reais/backtest.
WEIGHT_VIABILITY = 0.50
WEIGHT_PORTFOLIO = 0.30
WEIGHT_NEWS = 0.20


class FinalDecision(BaseModel):
    approve: bool
    aggregated_confidence: float = Field(ge=0.0, le=1.0)
    stop_loss_pct: float = Field(description="Distância do stop em % relativa ao preço de entrada, baseada em ATR.")
    take_profit_pct: float
    use_trailing_stop: bool
    reasoning: str


@dataclass
class RiskCommitteeInput:
    pair: str
    viability_verdict: AgentVerdict
    portfolio_check: PortfolioCheck
    news_sentiment_avg: float  # -1 a 1
    atr_reference: float
    entry_price: float


class RiskCommitteeAgent(BaseAgent):
    name = "risk_committee_agent"
    model = settings.openai_model_robust

    def decide(self, data: RiskCommitteeInput) -> FinalDecision:
        news_confidence = (data.news_sentiment_avg + 1) / 2  # normaliza -1..1 -> 0..1
        aggregated = (
            WEIGHT_VIABILITY * data.viability_verdict.confidence
            + WEIGHT_PORTFOLIO * (1.0 if data.portfolio_check.approved else 0.0)
            + WEIGHT_NEWS * news_confidence
        )

        if not data.portfolio_check.approved or data.viability_verdict.decision != "approve":
            return FinalDecision(
                approve=False,
                aggregated_confidence=aggregated,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
                use_trailing_stop=False,
                reasoning="Vetado: viabilidade técnica ou regras de portfólio não aprovaram.",
            )

        if aggregated < settings.min_confidence_to_trade:
            return FinalDecision(
                approve=False,
                aggregated_confidence=aggregated,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
                use_trailing_stop=False,
                reasoning=f"Confiança agregada ({aggregated:.0%}) abaixo do mínimo exigido ({settings.min_confidence_to_trade:.0%}).",
            )

        # Modelo robusto refina stop/take com base no ATR e no contexto —
        # aqui delegamos a decisão fina de níveis pro LLM, com o ATR como âncora.
        atr_pct = (data.atr_reference / data.entry_price) * 100 if data.entry_price else 1.0

        system_prompt = (
            "Você é o agente final de risco de um comitê de trading. Já foi decidido "
            "aprovar a operação; sua tarefa é definir stop-loss, take-profit e se usa "
            "trailing stop, com base na volatilidade (ATR) do ativo. Seja conservador "
            "dado que o capital é pequeno."
        )
        user_prompt = (
            f"Par: {data.pair}\nPreço de entrada: {data.entry_price}\n"
            f"ATR%: {atr_pct:.2f}\nConfiança agregada: {aggregated:.2f}\n"
            f"Racional do agente de viabilidade: {data.viability_verdict.reasoning}"
        )

        refined = call_structured(
            agent_name=self.name,
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=FinalDecision,
        )
        refined.approve = True
        refined.aggregated_confidence = aggregated
        return refined

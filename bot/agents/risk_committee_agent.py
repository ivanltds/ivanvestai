"""Agente final (modelo robusto): agrega os pareceres do ViabilityAgent
e do PortfolioComparisonAgent, calcula confiança ponderada e aplica o
veto (confiança < 80% = rejeitado). Define stop/take/trailing iniciais."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from agents.base import AgentVerdict, BaseAgent
from agents.portfolio_comparison_agent import PortfolioCheck
from config.settings import settings
from core.llm_client import call_structured
from pydantic import BaseModel, Field

logger = logging.getLogger("ivanvestai.risk_committee")

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
    # Até 25/09/2026 este era o único agente sempre preso na OpenAI, de
    # propósito -- é quem decide aprovar/rejeitar a entrada e define
    # stop/take, com capital real das duas contas. Ivan pediu explicitamente
    # pra também migrar pro DeepSeek (créditos da OpenAI zerados), ciente do
    # trade-off explicado antes de decidir: DeepSeek não tem Structured
    # Outputs nativo (schema embutido no prompt + validação manual, ver
    # core/llm_client.py) -- mais sujeito a falha de parse pontual numa
    # oportunidade específica. `decide()` não tem try/except em volta da
    # chamada de LLM (nunca teve, não é novo desta mudança); quem chama
    # (orchestrator.cycle_runner._evaluate_opportunities) agora protege
    # oportunidade por oportunidade, então uma falha de parse aqui pula só
    # ESSA oportunidade, nunca aprova uma ordem sem decisão válida (fail-closed).
    model = (
        settings.deepseek_model
        if settings.filter_agent_provider == "deepseek"
        else settings.openai_model_robust
    )

    def __init__(
        self, *, min_confidence_to_trade: float | None = None, account_id: uuid.UUID | None = None,
    ) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B/C): único campo deste
        # agente que já é RuntimeConfig/dashboard por conta hoje -- os pisos/
        # tetos de stop-take (settings.min_stop_loss_pct etc., mais abaixo)
        # continuam globais, ainda não viraram campo do dashboard por conta.
        # account_id (Fase E, ver 10.10): só usado pra etiquetar o custo da
        # chamada de LLM em api_cost_log -- cycle_runner.py instancia este
        # agente por conta, então é sempre a conta que está decidindo agora.
        super().__init__()
        self._min_confidence_to_trade = (
            min_confidence_to_trade if min_confidence_to_trade is not None else settings.min_confidence_to_trade
        )
        self._account_id = account_id

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

        if aggregated < self._min_confidence_to_trade:
            return FinalDecision(
                approve=False,
                aggregated_confidence=aggregated,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
                use_trailing_stop=False,
                reasoning=f"Confiança agregada ({aggregated:.0%}) abaixo do mínimo exigido ({self._min_confidence_to_trade:.0%}).",
            )

        # Modelo robusto refina stop/take com base no ATR e no contexto —
        # aqui delegamos a decisão fina de níveis pro LLM, com o ATR como âncora.
        atr_pct = (data.atr_reference / data.entry_price) * 100 if data.entry_price else 1.0

        system_prompt = (
            "Você é o agente final de risco de um comitê de trading. Já foi decidido "
            "aprovar a operação; sua tarefa é definir stop-loss, take-profit e se usa "
            "trailing stop, com base na volatilidade (ATR) do ativo. Seja conservador "
            "dado que o capital é pequeno. Mesmo em ativos de baixíssima volatilidade "
            f"(ATR% pequeno), nunca proponha stop-loss abaixo de {settings.min_stop_loss_pct:.1f}% "
            f"nem take-profit abaixo de {settings.min_take_profit_pct:.1f}% -- distâncias menores "
            "que isso costumam ser só ruído normal de preço, não um sinal de risco de verdade."
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
            account_id=self._account_id,
        )
        refined.approve = True
        refined.aggregated_confidence = aggregated

        # Piso programático (não só via prompt) -- achado em 16/09/2026, ver
        # arquitetura-tecnica.md 9.9: em ativos de baixíssima volatilidade
        # (ex: tokens lastreados em ouro), stop/take ancorados só no ATR
        # ficavam tão colados no preço de entrada que ruído normal já
        # disparava a saída segundos depois de abrir a posição. O prompt
        # acima já pede pro modelo respeitar o piso, mas isso aqui garante
        # mesmo se o modelo não seguir a instrução à risca.
        if refined.stop_loss_pct < settings.min_stop_loss_pct:
            refined.stop_loss_pct = settings.min_stop_loss_pct
        if refined.take_profit_pct < settings.min_take_profit_pct:
            refined.take_profit_pct = settings.min_take_profit_pct

        # Teto programático (21/09/2026, ver arquitetura-tecnica.md 9.20/9.21, item
        # crítico #3) -- rede de segurança contra alucinação do LLM (valor >=100%
        # deixaria o stop_price negativo, desativando o stop de fato).
        if refined.stop_loss_pct > settings.max_stop_loss_pct:
            logger.warning(
                "RiskCommitteeAgent propôs stop_loss_pct=%.2f%% (acima do teto de %.1f%%) -- "
                "limitado ao teto.", refined.stop_loss_pct, settings.max_stop_loss_pct,
            )
            refined.stop_loss_pct = settings.max_stop_loss_pct
        if refined.take_profit_pct > settings.max_take_profit_pct:
            logger.warning(
                "RiskCommitteeAgent propôs take_profit_pct=%.2f%% (acima do teto de %.1f%%) -- "
                "limitado ao teto.", refined.take_profit_pct, settings.max_take_profit_pct,
            )
            refined.take_profit_pct = settings.max_take_profit_pct

        return refined

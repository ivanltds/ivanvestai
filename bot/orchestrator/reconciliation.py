"""Resolve divergência entre ViabilityAgent e PortfolioComparisonAgent
numa mesma oportunidade: cada um recebe o parecer do outro e o
ViabilityAgent revota uma única vez (o PortfolioComparisonAgent é
determinístico — regras de capital não "revotam", só informam)."""
from __future__ import annotations

from agents.base import AgentVerdict
from agents.portfolio_comparison_agent import PortfolioCheck
from agents.viability_agent import ViabilityAgent
from agents.market_scanner_agent import ScannerOpportunity
from core.llm_client import call_structured


def has_divergence(viability: AgentVerdict, portfolio: PortfolioCheck) -> bool:
    viability_positive = viability.decision == "approve" and viability.confidence >= 0.6
    return viability_positive != portfolio.approved


def reconcile(
    viability_agent: ViabilityAgent,
    opportunity: ScannerOpportunity,
    viability_verdict: AgentVerdict,
    portfolio_check: PortfolioCheck,
) -> AgentVerdict:
    """Uma única rodada extra: o ViabilityAgent recebe o parecer de risco
    de portfólio e revota, levando em conta as restrições de capital."""
    system_prompt = (
        "Você é o agente de viabilidade técnica revisando seu parecer após "
        "receber o retorno do agente de risco de portfólio. Ajuste sua "
        "confiança se as restrições de capital/correlação mudarem a "
        "atratividade da operação — mas não mude de ideia só por educação, "
        "seja objetivo."
    )
    user_prompt = (
        f"Seu parecer original: decisão={viability_verdict.decision}, "
        f"confiança={viability_verdict.confidence}, raciocínio={viability_verdict.reasoning}\n\n"
        f"Parecer do agente de risco de portfólio: aprovado={portfolio_check.approved}, "
        f"motivos={portfolio_check.reasons}\n\n"
        f"Par: {opportunity.pair} | estratégia: {opportunity.strategy}\n\n"
        "Revote considerando as duas visões."
    )

    return call_structured(
        agent_name=f"{viability_agent.name}_reconciliation",
        model=viability_agent.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=AgentVerdict,
    )

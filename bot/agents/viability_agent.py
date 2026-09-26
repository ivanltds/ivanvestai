"""Agente de oscilação/viabilidade: refina as oportunidades do scanner,
cruza com o contexto de notícias (peso técnica > notícia) e devolve um
veredito de confiança técnica.

Modelo: por padrão o robusto da OpenAI (é aqui que a qualidade da decisão
importa mais -- é um FILTRO, não a decisão final, mas informa o comitê).
Se settings.filter_agent_provider="deepseek" (opt-in manual, ver
config/settings.py e core/llm_client.py), usa deepseek_model no lugar --
mais barato, mas ainda não validado quanto à qualidade do filtro nesta
tarefa especificamente; acompanhar aprovação/rejeição após a troca."""
from __future__ import annotations

import uuid

from agents.base import AgentVerdict, BaseAgent
from agents.market_scanner_agent import ScannerOpportunity
from config.settings import settings
from core.llm_client import call_structured
from db.models import NewsItem


class ViabilityAgent(BaseAgent):
    name = "viability_agent"
    model = (
        settings.deepseek_model
        if settings.filter_agent_provider == "deepseek"
        else settings.openai_model_robust
    )

    def __init__(self, *, account_id: uuid.UUID | None = None) -> None:
        # Multi-conta (multi-conta-plano.md, Fase E, ver 10.10): só usado pra
        # etiquetar o custo desta chamada em api_cost_log -- cycle_runner.py
        # já instancia este agente DE NOVO por conta dentro de
        # _evaluate_opportunities, então account_id aqui é sempre a conta que
        # está avaliando esta oportunidade agora (nunca None de verdade em
        # produção; None só no uso teórico fora do ciclo, ex. testes manuais).
        super().__init__()
        self._account_id = account_id

    def evaluate(self, opportunity: ScannerOpportunity, relevant_news: list[NewsItem]) -> AgentVerdict:
        news_context = "\n".join(
            f"- ({n.sentiment_score:+.2f}) {n.summary_pt}" for n in relevant_news
        ) or "Nenhuma notícia relevante encontrada neste ciclo para este ativo."

        system_prompt = (
            "Você é o agente de viabilidade técnica de um comitê de trading de "
            "criptomoedas. A análise técnica é o filtro principal; notícias servem "
            "como contexto que pode reforçar ou vetar uma entrada, nunca substituir "
            "o sinal técnico. Seja seletivo: só aprove com confiança alta (>=0.80) "
            "quando os indicadores e o contexto realmente convergem."
        )
        user_prompt = (
            f"Par: {opportunity.pair}\n"
            f"Estratégia sugerida: {opportunity.strategy}\n"
            f"Regime de mercado: {opportunity.regime}\n"
            f"Score de confluência técnica: {opportunity.confluence}\n"
            f"Indicadores: {opportunity.votes_summary}\n\n"
            f"Notícias recentes relacionadas:\n{news_context}\n\n"
            "Avalie se vale abrir uma posição de ENTRADA agora."
        )

        return call_structured(
            agent_name=self.name,
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=AgentVerdict,
            account_id=self._account_id,
        )

"""Agente de oscilação/viabilidade: refina as oportunidades do scanner,
cruza com o contexto de notícias (peso técnica > notícia) e devolve um
veredito de confiança técnica, usando o modelo robusto (é aqui que a
qualidade da decisão importa mais)."""
from __future__ import annotations

from agents.base import AgentVerdict, BaseAgent
from agents.market_scanner_agent import ScannerOpportunity
from config.settings import settings
from core.llm_client import call_structured
from db.models import NewsItem


class ViabilityAgent(BaseAgent):
    name = "viability_agent"
    model = settings.openai_model_robust

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
        )

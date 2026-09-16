"""Classe base para agentes do comitê + schemas Pydantic compartilhados
pro Structured Output da OpenAI."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentVerdict(BaseModel):
    """Formato padrão de saída de qualquer agente que participa da
    decisão de uma oportunidade — usado com Structured Outputs."""

    decision: Literal["approve", "reject", "abstain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="Justificativa objetiva, 2-5 frases, em português.")


class BaseAgent:
    name: str = "base_agent"
    model: str = ""

    def __init__(self) -> None:
        # `model = ""` é um valor válido e deliberado: marca um agente
        # determinístico sem LLM (execution_agent, market_scanner_agent,
        # portfolio_agent, portfolio_comparison_agent -- ver comentários em
        # cada um). Só `name` vazio é erro de verdade (agente sem identidade
        # definida). Bug encontrado nesta revisão: a checagem original
        # (`not self.model`) tratava "" como "não definido" e quebrava
        # justamente os agentes determinísticos -- nunca tinha aparecido
        # porque nenhum deles tinha sido instanciado de fato até o primeiro
        # dry-run completo (ver arquitetura-tecnica.md 9.6/9.7).
        if not self.name:
            raise NotImplementedError("Subclasses devem definir `name`.")

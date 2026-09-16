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
        if not self.name or not self.model:
            raise NotImplementedError("Subclasses devem definir `name` e `model`.")

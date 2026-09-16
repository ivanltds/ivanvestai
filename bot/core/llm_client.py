"""Cliente OpenAI com Structured Outputs (JSON Schema via Pydantic) e
log de custo por chamada em api_cost_log — alimenta o contador de
gasto de API do dashboard.

Preços por 1M tokens são aproximados e devem ser conferidos
periodicamente na página de pricing da OpenAI; usados só pra dar uma
estimativa de custo, não pra faturamento.
"""
from __future__ import annotations

from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config.settings import settings
from db.models import ApiCostLog
from db.session import get_session

_client = OpenAI(api_key=settings.openai_api_key)

T = TypeVar("T", bound=BaseModel)

# USD por 1M tokens (aprox.) — ajustar conforme pricing vigente da OpenAI.
_PRICING = {
    settings.openai_model_cheap: {"input": 0.15, "output": 0.60},
    settings.openai_model_robust: {"input": 2.50, "output": 10.00},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICING.get(model, {"input": 1.0, "output": 3.0})
    return (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]


def call_structured(
    *,
    agent_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
) -> T:
    """Chama a OpenAI com Structured Outputs, devolvendo uma instância
    validada de `response_model`. Registra o custo em api_cost_log."""
    completion = _client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
    )

    usage = completion.usage
    with get_session() as session:
        session.add(
            ApiCostLog(
                agent_name=agent_name,
                model=model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                estimated_cost_usd=_estimate_cost(
                    model, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0
                ),
            )
        )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError(f"[{agent_name}] LLM não retornou um objeto estruturado válido.")
    return parsed

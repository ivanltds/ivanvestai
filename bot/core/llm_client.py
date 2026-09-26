"""Cliente LLM (OpenAI, e opcionalmente DeepSeek) com log de custo por
chamada em api_cost_log — alimenta o contador de gasto de API do
dashboard.

Provedores:
- OpenAI: Structured Outputs nativo (`.beta.chat.completions.parse`,
  JSON Schema estrito via Pydantic) — sempre usado por risk_committee_agent
  (decisão final de risco sobre capital real, nunca migrado pro modo JSON
  manual do DeepSeek, de propósito), e é o padrão pros demais agentes.
- DeepSeek (opcional, ver config/settings.py `filter_agent_provider`): a API
  é compatível com o SDK da OpenAI (mesma classe `OpenAI`, só muda a
  `base_url`), mas NÃO tem o modo estrito de Structured Outputs — só JSON
  mode (`response_format={"type": "json_object"}`). Por isso, pra modelos
  DeepSeek, o schema do Pydantic vai embutido no prompt e a validação é
  manual (`response_model.model_validate_json`) depois da resposta. Isso é
  estruturalmente mais sujeito a falha de parse do que o modo nativo da
  OpenAI — por isso as chamadas que podem rodar em DeepSeek (viability_agent,
  sua reconciliação, position_review_agent e, desde 25/09/2026, news_agent)
  têm try/except por oportunidade/ativo/notícia em orchestrator/
  cycle_runner.py, agents/position_review_agent.py e agents/news_agent.py,
  pra uma falha de parse pular só aquele item, nunca cancelar o ciclo
  inteiro. `risk_committee_agent` é a única exceção deliberada -- é quem
  decide aprovar/rejeitar a entrada e definir stop/take com capital real,
  e fica no caminho mais testado/confiável (Structured Outputs nativo da
  OpenAI) mesmo com filter_agent_provider="deepseek" ligado.

Preços por 1M tokens são aproximados e devem ser conferidos
periodicamente:
- OpenAI: https://openai.com/api/pricing/
- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/ (pesquisado
  em 20/09/2026 — os nomes atuais são deepseek-flash e deepseek-v4-pro,
  substituindo os antigos deepseek-chat/deepseek-reasoner, que a API ainda
  aceita como alias). A DeepSeek cobra tarifas diferentes fora do horário
  de pico (~50% de desconto) e pra cache-hit vs. cache-miss no input;
  aqui é sempre usada a tarifa de PICO + CACHE-MISS (o pior caso), pra
  nunca subestimar o custo real — é uma aproximação conservadora, não uma
  medição exata do que cada chamada custou (o objeto `usage` da DeepSeek
  traz `prompt_tokens_details.prompt_cache_hit_tokens`/`..._miss_tokens`
  se algum dia quisermos refinar isso).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config.settings import settings
from db.models import ApiCostLog
from db.session import get_session

# timeout curto + 1 retry: o default do SDK é 10 min, o que travaria o ciclo inteiro numa chamada pendurada.
_openai_client = OpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)

# Só instancia o client da DeepSeek se houver key configurada -- sem key,
# _deepseek_client fica None e uma tentativa de chamar um modelo DeepSeek
# levanta um erro claro em vez de um 401 confuso da API.
_deepseek_client: OpenAI | None = None
if settings.deepseek_api_key:
    _deepseek_client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url="https://api.deepseek.com",
        timeout=30.0,
        max_retries=1,
    )

T = TypeVar("T", bound=BaseModel)

# USD por 1M tokens (aprox.) — ajustar conforme pricing vigente de cada provedor.
_PRICING = {
    settings.openai_model_cheap: {"input": 0.15, "output": 0.60},
    settings.openai_model_robust: {"input": 2.50, "output": 10.00},
    # DeepSeek -- tarifa de pico + cache-miss no input (pior caso, ver docstring acima).
    "deepseek-flash": {"input": 0.30, "output": 1.20},
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96},
    # Nomes legados que a API da DeepSeek ainda aceita, roteados pro mesmo preço dos atuais:
    "deepseek-chat": {"input": 0.30, "output": 1.20},
    "deepseek-v4-flash": {"input": 0.30, "output": 1.20},
    "deepseek-reasoner": {"input": 1.32, "output": 3.96},
}

# Qualquer modelo neste conjunto usa o caminho DeepSeek (JSON mode + validação
# manual) em vez do Structured Outputs nativo da OpenAI.
_DEEPSEEK_MODELS = {
    "deepseek-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-v4-flash",
    "deepseek-reasoner",
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICING.get(model, {"input": 1.0, "output": 3.0})
    return (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]


logger = logging.getLogger(__name__)


def _log_cost(
    agent_name: str, model: str, input_tokens: int, output_tokens: int,
    account_id: uuid.UUID | None = None,
) -> None:
    # Achado 24/09/2026 (arquitetura-tecnica.md 9.20 item 25): antes esta
    # gravação (só contabilidade de custo, não afeta a decisão em si) rodava
    # sem try/except -- uma falha momentânea do Postgres aqui descartava uma
    # resposta do LLM já recebida e validada, tratando-a como falha do
    # agente inteiro. Best-effort: loga e segue, nunca derruba a chamada.
    # account_id (multi-conta-plano.md, Fase E, ver 10.10): NULL pros agentes
    # compartilhados entre contas (news_agent -- roda uma vez por ciclo, não
    # dá pra atribuir a uma conta só); preenchido pros agentes que já rodam
    # por conta (viability_agent, sua reconciliação, position_review_agent,
    # risk_committee_agent) -- cada chamador passa o account_id certo.
    try:
        with get_session() as session:
            session.add(
                ApiCostLog(
                    agent_name=agent_name,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_usd=_estimate_cost(model, input_tokens, output_tokens),
                    account_id=account_id,
                )
            )
    except Exception:
        logger.warning(
            "Falha ao gravar api_cost_log pra %s (%s) -- custo não contabilizado desta "
            "chamada, mas a resposta do LLM segue válida.", agent_name, model, exc_info=True,
        )


def _call_openai(
    agent_name: str, model: str, system_prompt: str, user_prompt: str, response_model: type[T],
    account_id: uuid.UUID | None = None,
) -> T:
    completion = _openai_client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
    )

    usage = completion.usage
    _log_cost(
        agent_name, model,
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
        account_id=account_id,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError(f"[{agent_name}] LLM não retornou um objeto estruturado válido.")
    return parsed


def _call_deepseek(
    agent_name: str, model: str, system_prompt: str, user_prompt: str, response_model: type[T],
    account_id: uuid.UUID | None = None,
) -> T:
    if _deepseek_client is None:
        raise RuntimeError(
            f"[{agent_name}] modelo '{model}' pede a API da DeepSeek, mas DEEPSEEK_API_KEY "
            "está vazia no .env -- preencha a key antes de usar FILTER_AGENT_PROVIDER=deepseek."
        )

    # DeepSeek não tem Structured Outputs nativo -- só JSON mode. O schema
    # do Pydantic vai embutido no prompt e a validação é manual abaixo.
    schema_json = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
    json_instructions = (
        "\n\nResponda APENAS com um objeto JSON válido (sem markdown, sem texto "
        "fora do JSON, sem comentários) que siga exatamente este JSON Schema:\n"
        f"{schema_json}"
    )

    completion = _deepseek_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt + json_instructions},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )

    usage = completion.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    _log_cost(agent_name, model, input_tokens, output_tokens, account_id=account_id)

    content = completion.choices[0].message.content
    if not content:
        raise ValueError(f"[{agent_name}] DeepSeek não retornou conteúdo na resposta.")
    # Levanta ValidationError se o JSON não bater com o schema -- o chamador
    # (cycle_runner / position_review_agent) trata isso como falha pontual
    # daquela oportunidade/ativo, não do ciclo inteiro.
    return response_model.model_validate_json(content)


def call_structured(
    *,
    agent_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    account_id: uuid.UUID | None = None,
) -> T:
    """Chama o provedor certo pro modelo pedido (OpenAI ou DeepSeek),
    devolvendo uma instância validada de `response_model`. Registra o
    custo em api_cost_log em ambos os casos. `account_id` (multi-conta-plano.md,
    Fase E, ver 10.10) -- opcional, omitido = custo compartilhado (NULL),
    alimenta o painel de custo por conta em /settings quando informado."""
    if model in _DEEPSEEK_MODELS:
        return _call_deepseek(agent_name, model, system_prompt, user_prompt, response_model, account_id=account_id)
    return _call_openai(agent_name, model, system_prompt, user_prompt, response_model, account_id=account_id)

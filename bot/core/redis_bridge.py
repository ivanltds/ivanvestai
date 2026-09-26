"""Ponte com o Upstash Redis: pub/sub de eventos, cache de leitura rápida,
fila de comandos do dashboard -> bot, e lock de ciclo.

Usa a API REST do Upstash (upstash-redis) em vez do protocolo Redis
binário — funciona bem tanto do bot local quanto seria compatível com
ambiente serverless do lado do dashboard.

Robustez (21/09/2026, ver arquitetura-tecnica.md 9.20/9.21, item crítico #5):
nenhuma função aqui deve deixar uma falha do Upstash Redis propagar sem
tratamento -- em especial `acquire_cycle_lock`, que corria ANTES do
try/finally de `run_cycle()`: uma falha do Redis nesse instante impedia
`_safe_manage_open_positions()` de rodar, que é justamente a checagem de
stop/take das posições reais abertas. Cada função aqui agora captura
qualquer exceção, loga, e degrada da forma mais segura possível -- pra
lock/publish/cache isso quase sempre significa "falha aberta" (agir como
se a operação tivesse êxito), porque bloquear a gestão de posições por
causa do Redis é pior do que raramente rodar em paralelo/perder um evento
de cache.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from upstash_redis import Redis

from config.settings import settings

logger = logging.getLogger("ivanvestai.redis_bridge")

_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis(url=settings.upstash_redis_rest_url, token=settings.upstash_redis_rest_token)
    return _redis


# --- Pub/Sub (o Upstash REST não suporta SUBSCRIBE nativo do protocolo Redis;
# publicamos como uma lista/stream que o dashboard lê via SSE fazendo polling
# curto do lado do servidor Next.js, que é o padrão recomendado pro REST API) ---

def publish_event(channel: str, payload: dict[str, Any]) -> None:
    """Best-effort: uma falha do Redis aqui nunca deve derrubar o ciclo --
    o Postgres já é a fonte de verdade de longo prazo (ver docstring do módulo)."""
    try:
        key = f"events:{channel}"
        message = json.dumps({"ts": time.time(), **payload})
        client = _client()
        client.lpush(key, message)
        client.ltrim(key, 0, 49)  # mantém só os últimos 50 eventos por canal
        client.expire(key, 60 * 60 * 6)  # 6h, o Postgres é a fonte de verdade de longo prazo
    except Exception:
        logger.warning("Falha ao publicar evento no Redis (canal=%s) -- ignorada.", channel, exc_info=True)


# --- Cache de leitura rápida -------------------------------------------

def cache_set(key: str, value: dict[str, Any], ttl_seconds: int = 60) -> None:
    try:
        _client().set(f"cache:{key}", json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning("Falha ao gravar cache no Redis (key=%s) -- ignorada.", key, exc_info=True)


def cache_get(key: str) -> dict[str, Any] | None:
    try:
        raw = _client().get(f"cache:{key}")
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("Falha ao ler cache no Redis (key=%s) -- devolvendo None.", key, exc_info=True)
        return None


# --- Fila de comandos (dashboard -> bot) --------------------------------

def enqueue_command(command: str, payload: dict[str, Any] | None = None) -> None:
    try:
        _client().lpush("commands:bot", json.dumps({"command": command, "payload": payload or {}, "ts": time.time()}))
    except Exception:
        logger.warning("Falha ao enfileirar comando no Redis (command=%s) -- ignorada.", command, exc_info=True)


def drain_commands() -> list[dict[str, Any]]:
    try:
        client = _client()
        commands: list[dict[str, Any]] = []
        while True:
            raw = client.rpop("commands:bot")
            if not raw:
                break
            commands.append(json.loads(raw))
        return commands
    except Exception:
        logger.warning("Falha ao drenar fila de comandos do Redis -- devolvendo lista vazia.", exc_info=True)
        return []


# --- Alerta 1x por dia ------------------------------------------------------

def once_per_day(key: str) -> bool:
    """True na primeira chamada do dia pra essa `key` (SET NX com validade de 24h).
    Se o Redis falhar, devolve True (melhor um alerta repetido do que nenhum)."""
    try:
        return bool(_client().set(f"once:{key}", "1", nx=True, ex=24 * 3600))
    except Exception:
        return True


# --- Lock de ciclo --------------------------------------------------------

_lock_token: str | None = None


def acquire_cycle_lock(ttl_seconds: int = 600) -> bool:
    """Lock com token do dono: `release_cycle_lock` só apaga se o lock ainda é
    NOSSO (se o TTL expirou e outro processo pegou, não derruba o dele).

    Fail-open deliberado (21/09/2026): se o Redis falhar aqui, devolve True
    (finge que conseguiu o lock) em vez de propagar a exceção -- isso roda
    ANTES do try/finally de run_cycle(), então uma exceção aqui impedia até
    a gestão de posições abertas de rodar. Pior caso do fail-open: dois
    processos gerenciando posições ao mesmo tempo (raro, só um bot local
    rodando); pior caso de deixar propagar: nenhuma checagem de stop/take
    acontece enquanto o Redis estiver fora do ar."""
    global _lock_token
    token = str(uuid.uuid4())
    try:
        acquired = bool(_client().set("lock:cycle", token, nx=True, ex=ttl_seconds))
    except Exception:
        logger.error("Falha ao adquirir lock de ciclo no Redis -- seguindo mesmo assim (fail-open).", exc_info=True)
        return True
    if acquired:
        _lock_token = token
    return acquired


def release_cycle_lock() -> None:
    global _lock_token
    token, _lock_token = _lock_token, None
    if token is None:
        return
    try:
        client = _client()
        if client.get("lock:cycle") == token:
            client.delete("lock:cycle")
    except Exception:
        logger.warning("Falha ao liberar lock de ciclo no Redis -- vai expirar sozinho pelo TTL.", exc_info=True)

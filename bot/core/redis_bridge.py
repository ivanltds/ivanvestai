"""Ponte com o Upstash Redis: pub/sub de eventos, cache de leitura rápida,
fila de comandos do dashboard -> bot, e lock de ciclo.

Usa a API REST do Upstash (upstash-redis) em vez do protocolo Redis
binário — funciona bem tanto do bot local quanto seria compatível com
ambiente serverless do lado do dashboard.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from upstash_redis import Redis

from config.settings import settings

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
    key = f"events:{channel}"
    message = json.dumps({"ts": time.time(), **payload})
    client = _client()
    client.lpush(key, message)
    client.ltrim(key, 0, 49)  # mantém só os últimos 50 eventos por canal
    client.expire(key, 60 * 60 * 6)  # 6h, o Postgres é a fonte de verdade de longo prazo


# --- Cache de leitura rápida -------------------------------------------

def cache_set(key: str, value: dict[str, Any], ttl_seconds: int = 60) -> None:
    _client().set(f"cache:{key}", json.dumps(value), ex=ttl_seconds)


def cache_get(key: str) -> dict[str, Any] | None:
    raw = _client().get(f"cache:{key}")
    return json.loads(raw) if raw else None


# --- Fila de comandos (dashboard -> bot) --------------------------------

def enqueue_command(command: str, payload: dict[str, Any] | None = None) -> None:
    _client().lpush("commands:bot", json.dumps({"command": command, "payload": payload or {}, "ts": time.time()}))


def drain_commands() -> list[dict[str, Any]]:
    client = _client()
    commands: list[dict[str, Any]] = []
    while True:
        raw = client.rpop("commands:bot")
        if not raw:
            break
        commands.append(json.loads(raw))
    return commands


# --- Lock de ciclo --------------------------------------------------------

def acquire_cycle_lock(ttl_seconds: int = 120) -> bool:
    token = str(uuid.uuid4())
    acquired = _client().set("lock:cycle", token, nx=True, ex=ttl_seconds)
    return bool(acquired)


def release_cycle_lock() -> None:
    _client().delete("lock:cycle")

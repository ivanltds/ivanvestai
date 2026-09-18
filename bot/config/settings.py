"""Configuração central do bot.

Carrega defaults do .env; parâmetros que também aparecem na tela de
configurações do dashboard (tabela `settings` no Postgres) são lidos
por cima desses defaults em tempo de execução — ver db.models.Setting
e core.config_store.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # OpenAI
    openai_api_key: str = ""
    openai_model_cheap: str = "gpt-4o-mini"
    openai_model_robust: str = "gpt-4o"

    # Postgres
    database_url: str = "postgresql+psycopg://user:password@localhost:5432/ivanvestai"

    # Redis (Upstash REST)
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    # E-mail
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_app_password: str = ""
    alert_email_to: str = "ivanltds@gmail.com"

    # Web Push
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_claims_email: str = "mailto:ivanltds@gmail.com"

    # Segurança de capital: enquanto True (padrão), o ExecutionAgent NUNCA chama
    # place_market_order/place_limit_order -- simula o fill pelo preço atual e
    # marca Position/Trade com is_paper=True. De propósito, NÃO é sobrescrito
    # pela tabela `settings` (nada de comando remoto do dashboard consegue
    # ligar/desligar isso) -- só muda com uma edição manual do .env local,
    # deliberadamente mais difícil de fazer sem querer.
    dry_run: bool = True

    # Parâmetros operacionais (defaults — sobrescritos pela tabela settings quando presentes)
    cycle_interval_minutes: int = 15
    # Orçamento de tempo pra AVALIAR oportunidades de entrada (LLM). Checado entre
    # oportunidades -- uma ordem já em andamento nunca é interrompida no meio.
    entry_decision_timeout_seconds: int = 120
    min_confidence_to_trade: float = 0.80
    min_confidence_to_exit: float = 0.75  # PositionReviewAgent: confiança mínima pra vender uma posição pré-existente
    # PositionReviewAgent: intervalo mínimo entre duas revisões do MESMO ativo (controla custo de LLM:
    # sem isso cada ativo da carteira era reavaliado com gpt-4o a cada ciclo de 15 min).
    position_review_interval_minutes: int = 240

    # Piso de stop/take (RiskCommitteeAgent) -- achado em 16/09/2026 (primeiro
    # dry-run do comitê completo, ver arquitetura-tecnica.md 9.9): o agente
    # ancora stop/take no ATR do ativo, e pra ativos de baixíssima volatilidade
    # por natureza (tokens lastreados em ouro, stablecoins) isso gerava
    # distâncias tão pequenas que ruído normal de preço já disparava a saída
    # segundos depois de abrir a posição. Não substitui a decisão do agente
    # (que continua livre pra propor algo mais largo) -- só evita esse caso
    # degenerado de stop/take grudado no preço de entrada.
    min_stop_loss_pct: float = 1.0
    min_take_profit_pct: float = 1.5
    max_allocation_pct_per_trade: float = 0.50
    daily_loss_alert_pct: float = 0.10
    top_n_pairs: int = 100
    safety_stablecoin: str = "USDT"
    timezone: str = "America/Sao_Paulo"

    sector_map_path: Path = CONFIG_DIR / "sector_map.json"
    macro_calendar_path: Path = CONFIG_DIR / "macro_calendar.json"


settings = Settings()

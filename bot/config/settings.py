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

    # Logs (ver core/logging_setup.py): um arquivo por dia em `log_dir` + tabela bot_logs no Postgres.
    log_dir: str = "logs"  # relativo a bot/ (ou caminho absoluto)
    log_retention_days: int = 30
    log_to_db: bool = True

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
    #
    # Revisão de 19/09/2026: análise contra dados da Binance (analyze_decisions_vs_market.py)
    # mostrou que stop de 1% / take de 1,5% ficam dentro do ruído do preço (net ~0 depois de
    # 0,2% de taxas). Pisos subidos pra 2% / 4% (relação 2:1) como EXPERIMENTO -- continuam configuráveis
    # via .env (MIN_STOP_LOSS_PCT / MIN_TAKE_PROFIT_PCT) e a análise deve ser reexecutada
    # conforme os dados amadurecem. Só valem pra posições NOVAS (stop/take já gravados não mudam).
    min_stop_loss_pct: float = 2.0
    min_take_profit_pct: float = 4.0
    # Intervalo do monitor rápido de stop/take/trailing das posições abertas (entre os
    # ciclos de 15 min). 0 desliga. Ver orchestrator/cycle_runner.monitor_open_positions.
    risk_monitor_seconds: int = 60
    max_allocation_pct_per_trade: float = 0.50
    daily_loss_alert_pct: float = 0.10
    top_n_pairs: int = 100
    safety_stablecoin: str = "USDT"
    timezone: str = "America/Sao_Paulo"

    sector_map_path: Path = CONFIG_DIR / "sector_map.json"
    macro_calendar_path: Path = CONFIG_DIR / "macro_calendar.json"


settings = Settings()

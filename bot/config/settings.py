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

    # Multi-conta (ver multi-conta-plano.md, seção 5.1): chave de criptografia
    # simétrica (Fernet) usada pra cifrar/decifrar a api_key/api_secret de cada
    # conta gravada na tabela `accounts` -- essa chave em si NUNCA vai pro
    # Postgres, só fica aqui no .env local. Gerar uma nova: `python -m core.crypto`.
    # Ainda sem efeito no comportamento do bot hoje (Fase A do plano é só o
    # modelo de dados/migração; os agentes continuam usando binance_api_key/
    # binance_api_secret acima até a Fase B).
    accounts_encryption_key: str = ""

    # OpenAI
    openai_api_key: str = ""
    openai_model_cheap: str = "gpt-4o-mini"
    openai_model_robust: str = "gpt-4o"

    # DeepSeek (opcional -- só é usado se filter_agent_provider="deepseek").
    # A API da DeepSeek é compatível com o SDK da OpenAI (mesma classe
    # `OpenAI`, só muda a base_url), mas não tem Structured Outputs nativo
    # -- ver core/llm_client.py pro detalhe de como isso é tratado.
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"

    # Provedor de LLM pros 3 agentes-filtro (viability_agent + sua
    # reconciliação + position_review_agent). "openai" (padrão, comportamento
    # atual) ou "deepseek". O risk_committee_agent (decisão final, aprova
    # mover capital real) NUNCA lê este flag -- fica sempre em
    # openai_model_robust, de propósito. Só muda à mão, editando este
    # arquivo/.env -- nunca pelo dashboard. Mesmo padrão de DRY_RUN (ver
    # arquitetura-tecnica.md 9.7) e BYPASS_MACRO_RISK_WINDOW (9.11).
    filter_agent_provider: str = "openai"

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
    # PositionReviewAgent: allowlist de ativos que NUNCA podem ser vendidos por esse
    # agente, mesmo com veredito "sell" e confiança alta (achado 24/09/2026, ver
    # arquitetura-tecnica.md 9.21 item 10). BNB é o caso motivador: é a reserva de
    # taxa da conta (paga fee na Binance), não uma posição especulativa -- vender
    # ela por engano interrompe o desconto de taxa em BNB da conta inteira.
    # Configurável (CSV) pra proteger outros ativos "operacionais" no futuro sem
    # precisar mexer em código -- só editar o .env.
    position_review_protected_assets: str = "BNB"

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
    # Teto de stop/take (21/09/2026, ver arquitetura-tecnica.md 9.20/9.21, item
    # crítico #3): só existia piso. Se o LLM alucinar um valor >=100%, o cálculo
    # de stop_price = preço * (1 - stop_loss_pct/100) fica negativo -- um preço
    # que o mercado nunca alcança, desativando de fato a proteção de stop-loss.
    # 10% escolhido pelo Ivan (bem acima de qualquer piso/ATR real observado até
    # aqui, então não deve afetar decisões normais do comitê).
    max_stop_loss_pct: float = 10.0
    max_take_profit_pct: float = 10.0
    # OCO (stop + take na própria exchange, ver core/order_utils.py e
    # agents/execution_agent.py) -- desligado por padrão, mesmo padrão de opt-in
    # manual só via .env já usado por dry_run/bypass_macro_risk_window/
    # filter_agent_provider. Código novo, nunca testado contra a API real da
    # Binance nesta sessão -- Ivan liga deliberadamente quando quiser validar,
    # de preferência observando de perto a primeira entrada real depois de ligar.
    enable_oco: bool = False
    # Intervalo do monitor rápido de stop/take/trailing das posições abertas (entre os
    # ciclos de 15 min). 0 desliga. Ver orchestrator/cycle_runner.monitor_open_positions.
    risk_monitor_seconds: int = 60
    max_allocation_pct_per_trade: float = 0.50
    # Teto de alocação separado (mais apertado) pra oportunidades que batem no
    # critério de "meme coin"/alto risco tolerado (core.risk_rules.meme_coin_eligible
    # -- 2 de 3 sinais elevados: volume, sentimento social, momentum). Achado
    # 24/09/2026 (arquitetura-tecnica.md 9.21 item 13): a função já existia mas
    # nunca era chamada em lugar nenhum -- toda oportunidade, meme coin ou não,
    # usava o mesmo teto de 50%. Valor de 5% conforme o docstring original da
    # função ("tolerância de 5% da carteira"), aplicado em
    # PortfolioComparisonAgent.check() no lugar de max_allocation_pct_per_trade
    # quando a oportunidade é elegível.
    meme_coin_max_allocation_pct: float = 0.05
    daily_loss_alert_pct: float = 0.10
    top_n_pairs: int = 100
    safety_stablecoin: str = "USDT"
    timezone: str = "America/Sao_Paulo"

    sector_map_path: Path = CONFIG_DIR / "sector_map.json"
    macro_calendar_path: Path = CONFIG_DIR / "macro_calendar.json"


settings = Settings()

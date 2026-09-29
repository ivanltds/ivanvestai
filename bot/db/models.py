"""Modelo de dados (Postgres) — fonte de verdade compartilhada entre bot/ e web/.

Ver seção 4 de arquitetura-tecnica.md para o desenho original.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    keys_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class Account(Base):
    """Uma conta Binance gerida pelo bot (ver multi-conta-plano.md). Hoje só
    existe uma linha (a conta única migrada por migrate_add_accounts.py) --
    o desenho já suporta N contas, cadastradas via manage_accounts.py.
    Credenciais NUNCA em texto plano: core/crypto.py cifra antes de gravar,
    e o dashboard nunca lê/mostra esses dois campos de volta (write-only,
    ver seção 5.1 do plano)."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    label: Mapped[str] = mapped_column(String, nullable=False)
    binance_api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    binance_api_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Mesma filosofia de fricção deliberada do settings.dry_run global (ver
    # config/settings.py) -- só muda via manage_accounts.py, rodado no PC do
    # Ivan, nunca pelo dashboard. Ainda não lido por nenhum agente (Fase B
    # do plano é quem passa a usar isto de verdade).
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class Setting(Base):
    """Chave/valor da tela de configurações — sobrescreve os defaults do .env.

    Chave composta desde 25/09/2026 (multi-conta-plano.md, Fase E, ver 10.8):
    `id` (UUID) é a PK de verdade; a unicidade real é garantida por DOIS
    índices parciais -- não dá pra usar uma PK/UNIQUE composta comum em
    (account_id, key) porque account_id é NULLABLE e o Postgres nunca trata
    dois NULL como iguais numa constraint normal (duas linhas com account_id
    NULL e a mesma key passariam batido):
      - `uq_settings_global_key`: no máximo 1 linha por `key` com
        account_id IS NULL -- config "master"/default, vale pra TODAS as
        contas que não tiverem override próprio (bot_status master, seção
        5.3 do plano; e o valor-base de qualquer outra chave até uma conta
        específica divergir).
      - `uq_settings_account_key`: no máximo 1 linha por (account_id, key)
        com account_id IS NOT NULL -- override de UMA conta específica (ex:
        CONTA MICAEL com max_allocation_pct_per_trade diferente da CONTA
        IVAN).
    `core/config_store.py` (load_runtime_config) mescla os dois níveis: lê o
    master primeiro, depois aplica por cima o override da conta pedida, se
    existir. Ver migrate_add_settings_composite_key.py pra migração desta
    troca de chave (as 7 linhas que antes só tinham o id da CONTA IVAN como
    rótulo informativo voltaram pra account_id NULL nessa migração, pra não
    mudar nenhum valor de configuração no dia da troca -- ver o script)."""

    __tablename__ = "settings"
    __table_args__ = (
        Index("uq_settings_global_key", "key", unique=True, postgresql_where=text("account_id IS NULL")),
        Index("uq_settings_account_key", "account_id", "key", unique=True, postgresql_where=text("account_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now())


class WalletSnapshot(Base):
    __tablename__ = "wallet_snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    asset: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_usdt: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String, default="bot")  # bot | manual

    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    title_original: Mapped[str] = mapped_column(Text, nullable=False)
    summary_pt: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False)  # -1 a +1
    url: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String, index=True, unique=True)


class MarketMetric(Base):
    """Métricas de mercado além do preço, gravadas de hora em hora pelo coletor
    (core/market_metrics.py, 29/09/2026). Motivo: a API da Binance só guarda 30
    dias de contratos em aberto e de proporção de posições -- sem gravar nós
    mesmos, nunca haverá histórico pra testar esses dados no simulador.
    Uma linha por (hora, par). `fear_greed` é do mercado todo (repetido em cada
    linha da mesma coleta). Fonte do Medo e Ganância: alternative.me (exige
    crédito à fonte onde o dado for exibido). Nenhum campo é usado em decisão
    de trade -- só coleta."""

    __tablename__ = "market_metrics"
    __table_args__ = (Index("ix_market_metrics_symbol_ts", "symbol", "timestamp"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)          # último funding liquidado (fração, 0.0001 = 0,01%)
    funding_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)         # contratos (moeda base)
    open_interest_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)      # todas as contas
    top_trader_long_short_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # top traders, por posição
    taker_buy_sell_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # agressão compradora/vendedora
    fear_greed: Mapped[int | None] = mapped_column(Integer, nullable=True)            # 0-100, alternative.me


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cycle_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    pair: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)  # trend|mean_reversion|breakout|scalping
    market_regime: Mapped[str] = mapped_column(String, nullable=False)
    indicators_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|approved|rejected
    final_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)

    decisions: Mapped[list["CommitteeDecision"]] = relationship(back_populates="opportunity")


class CommitteeDecision(Base):
    """= agent_runs — racional completo de cada agente por oportunidade, pra auditoria."""

    __tablename__ = "committee_decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # approve|reject|abstain
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())

    opportunity: Mapped["Opportunity"] = relationship(back_populates="decisions")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pair: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open|closed
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trailing_reference_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_flag: Mapped[str] = mapped_column(String, default="none")  # none|immediate|optimized
    rebuy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Lista OCO (stop + take) criada na Binance pra proteger esta posição real; NULL = sem
    # proteção na exchange (só stop/take por software). Ver agents/execution_agent.py.
    oco_order_list_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # True = simulado (settings.dry_run) -- nunca é capital real
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    position_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    pair: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)  # buy|sell
    order_type: Mapped[str] = mapped_column(String, nullable=False)  # market|limit
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    fee_asset: Mapped[str] = mapped_column(String, default="BNB")
    reason: Mapped[str] = mapped_column(String, nullable=False)  # committee|manual_flag|manual_dashboard
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # True = simulado (settings.dry_run) -- nunca é capital real
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class DailyEquity(Base):
    """Um "início de dia" de patrimônio -- base do circuit breaker (ver
    core/equity.py). Até a Fase C (multi-conta-plano.md) a PK era só `date`
    (uma linha por dia -- suficiente pra uma conta só). Com 2+ contas ativas
    ao mesmo tempo, cada uma precisa do seu próprio "início de dia" no mesmo
    calendário, então a PK virou um `id` substituto, com um índice único em
    (date, account_id) garantindo no máximo uma linha por conta por dia (a
    mesma garantia que a PK antiga dava, agora por conta). Ver
    migrate_add_daily_equity_pk.py."""

    __tablename__ = "daily_equity"
    __table_args__ = (UniqueConstraint("date", "account_id", name="uq_daily_equity_date_account"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    date: Mapped[dt.date] = mapped_column(nullable=False, index=True)
    equity_brl: Mapped[float] = mapped_column(Float, nullable=False)
    equity_usdt: Mapped[float] = mapped_column(Float, nullable=False)
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto (mesma
    # razão histórica das outras tabelas), mas a partir da Fase C
    # (cycle_runner.py) todo INSERT novo já vem com account_id preenchido.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class PaperTrade(Base):
    """Trades SIMULADOS do paper trading ao vivo (run_paper_trading.py, ver
    arquitetura-tecnica.md 9.5/9.6) -- nunca envolvem ordem real na Binance
    nem saldo real. Tabela separada de `trades`/`positions` (reservadas para
    operação com capital real) de propósito, pra nunca confundir uma
    simulação com uma operação de verdade -- inclusive visualmente no
    dashboard (página /paper-trading em vez de /dashboard)."""

    __tablename__ = "paper_trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    strategy: Mapped[str] = mapped_column(String, nullable=False, index=True)  # committee|kotegawa|rapf_filtros|rapf_sem_filtros
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event: Mapped[str] = mapped_column(String, nullable=False)  # entry|exit
    direction: Mapped[str] = mapped_column(String, nullable=False)  # long (spot-only, ver notas nos backtests)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)  # stop_loss|take_profit -- só em exit
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # só em exit
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)


class PositionReview(Base):
    """Veredito do PositionReviewAgent pra uma posição PRÉ-EXISTENTE da
    carteira real (ativo comprado manualmente antes do bot existir, ou há
    muito tempo, sem uma Position aberta pelo bot cuidando da saída) --
    "vale a pena continuar segurando isso ou é melhor vender?". Uma linha
    por avaliação (histórico, não upsert) -- o dashboard mostra a mais
    recente por ativo. Ver arquitetura-tecnica.md 9.8."""

    __tablename__ = "position_reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # hold|sell
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    value_usdt: Mapped[float] = mapped_column(Float, nullable=False)
    acted: Mapped[bool] = mapped_column(Boolean, default=False)  # True = o bot já vendeu com base nesta avaliação
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # True = venda simulada (settings.dry_run) -- só relevante quando acted=True
    price_at_review: Mapped[float | None] = mapped_column(Float, nullable=True)  # preço unitário no momento do veredito
    # (value_usdt / quantity) -- guardado pra, mais adiante, comparar com o preço
    # futuro do ativo e medir se o veredito hold/sell teria sido acertado (ver
    # arquitetura-tecnica.md 9.10 -- não dava pra medir "acerto" sem isso).
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class ApiCostLog(Base):
    __tablename__ = "api_cost_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # Multi-conta (ver multi-conta-plano.md) -- NULLABLE por enquanto: só
    # passa a ser preenchido pelos agentes a partir da Fase B do plano; até
    # lá, migrate_add_accounts.py mantém isso apontando pra conta única já
    # existente. Vira NOT NULL numa migração futura, depois da Fase B validada.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)


class BotLog(Base):
    """Log do bot (arquivo + esta tabela) pra consulta/análise de desempenho
    e diagnóstico. Escrito em lote por uma thread de fundo (core/logging_setup.py)
    -- nunca bloqueia nem derruba o ciclo. Retenção: LOG_RETENTION_DAYS (padrão 30)."""

    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String, nullable=False, index=True)  # DEBUG|INFO|WARNING|ERROR|CRITICAL
    logger: Mapped[str] = mapped_column(String, nullable=False)  # ex: ivanvestai.cycle_runner, ivanvestai.vlog
    cycle_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)  # modo do bot quando a linha foi gerada
    message: Mapped[str] = mapped_column(Text, nullable=False)

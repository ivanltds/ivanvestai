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
    Integer,
    String,
    Text,
    func,
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


class Setting(Base):
    """Chave/valor da tela de configurações — sobrescreve os defaults do .env."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
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

    decisions: Mapped[list["CommitteeDecision"]] = relationship(back_populates="opportunity")


class CommitteeDecision(Base):
    """= agent_runs — racional completo de cada agente por oportunidade, pra auditoria."""

    __tablename__ = "committee_decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"))
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
    status: Mapped[str] = mapped_column(String, default="open")  # open|closed
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trailing_reference_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_flag: Mapped[str] = mapped_column(String, default="none")  # none|immediate|optimized
    rebuy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # True = simulado (settings.dry_run) -- nunca é capital real


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    position_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
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


class DailyEquity(Base):
    __tablename__ = "daily_equity"

    date: Mapped[dt.date] = mapped_column(primary_key=True)
    equity_brl: Mapped[float] = mapped_column(Float, nullable=False)
    equity_usdt: Mapped[float] = mapped_column(Float, nullable=False)


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


class ApiCostLog(Base):
    __tablename__ = "api_cost_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


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

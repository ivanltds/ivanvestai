"""Orquestra um ciclo completo do comitê (a cada 15 min por padrão).
Ver arquitetura-tecnica.md seção 3.2 para o fluxo de referência."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid

from agents.execution_agent import ExecutionAgent
from agents.market_scanner_agent import MarketScannerAgent
from agents.portfolio_agent import PortfolioAgent
from agents.portfolio_comparison_agent import PortfolioComparisonAgent
from agents.risk_committee_agent import RiskCommitteeAgent, RiskCommitteeInput
from agents.viability_agent import ViabilityAgent
from core import redis_bridge
from core.config_store import load_runtime_config
from core.notifier import alert
from core.risk_rules import circuit_breaker_triggered, in_macro_risk_window
from db.models import CommitteeDecision, DailyEquity, NewsItem, Opportunity, Position
from db.session import get_session
from orchestrator.reconciliation import has_divergence, reconcile

logger = logging.getLogger("ivanvestai.cycle_runner")


async def run_cycle() -> None:
    from bot.agents.news_agent import NewsAgent as _NewsAgent  # import local (evita ciclo)

    config = load_runtime_config()
    if config.bot_status != "running":
        logger.info("Bot pausado (settings.bot_status != running) — ciclo ignorado.")
        return

    if not redis_bridge.acquire_cycle_lock(ttl_seconds=config.entry_decision_timeout_seconds + 60):
        logger.warning("Ciclo anterior ainda em andamento (lock ativo) — pulando este disparo.")
        return

    cycle_id = str(uuid.uuid4())
    now = dt.datetime.now(dt.timezone.utc)

    try:
        in_window, event_label = in_macro_risk_window(now)
        if in_window:
            logger.info("Dentro da janela de risco macro (%s) — sem novas entradas neste ciclo.", event_label)

        # --- Passo 1: agentes de coleta em paralelo -------------------
        news_agent_instance = _NewsAgent()
        portfolio_agent = PortfolioAgent()
        scanner_agent = MarketScannerAgent()

        news_items, wallet_snapshots, opportunities = await asyncio.gather(
            asyncio.to_thread(news_agent_instance.run),
            asyncio.to_thread(portfolio_agent.run),
            asyncio.to_thread(scanner_agent.run),
        )

        total_equity = PortfolioAgent.total_equity_usdt(wallet_snapshots)
        redis_bridge.cache_set("balance", {"total_equity_usdt": total_equity, "ts": now.isoformat()})
        redis_bridge.publish_event("positions", {"total_equity_usdt": total_equity})

        _check_circuit_breaker(total_equity, config.daily_loss_alert_pct)

        if in_window or not opportunities:
            return

        # --- Passo 2-6: viabilidade + portfólio + reconciliação + veto final,
        # com timeout só para decisão de ENTRADA -------------------------
        try:
            await asyncio.wait_for(
                _evaluate_opportunities(opportunities, news_items, total_equity, cycle_id),
                timeout=config.entry_decision_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout de decisão de entrada (%ss) estourado — ciclo cancelado por segurança.",
                            config.entry_decision_timeout_seconds)
            redis_bridge.publish_event("alerts", {"type": "timeout", "message": "Timeout de decisão de entrada"})

        # --- Gestão de posições abertas (sem timeout — saída pode ser deliberada) ---
        _manage_open_positions()

    finally:
        redis_bridge.release_cycle_lock()


async def _evaluate_opportunities(
    opportunities: list, news_items: list[NewsItem], total_equity: float, cycle_id: str
) -> None:
    viability_agent = ViabilityAgent()
    portfolio_agent = PortfolioComparisonAgent()
    risk_committee = RiskCommitteeAgent()
    execution_agent = ExecutionAgent()

    with get_session() as session:
        open_positions = session.query(Position).filter_by(status="open").all()

    for opp in opportunities:
        relevant_news = [n for n in news_items if opp.pair.replace("USDT", "") in n.title_original.upper()]

        viability_verdict = await asyncio.to_thread(viability_agent.evaluate, opp, relevant_news)
        portfolio_check = await asyncio.to_thread(portfolio_agent.check, opp, total_equity, open_positions)

        if has_divergence(viability_verdict, portfolio_check):
            viability_verdict = await asyncio.to_thread(
                reconcile, viability_agent, opp, viability_verdict, portfolio_check
            )

        news_avg = sum(n.sentiment_score for n in relevant_news) / len(relevant_news) if relevant_news else 0.0
        atr_ref = opp.votes_summary.get("atr_reference", {}).get("value", {}).get("atr", 0.0)

        final = await asyncio.to_thread(
            risk_committee.decide,
            RiskCommitteeInput(
                pair=opp.pair,
                viability_verdict=viability_verdict,
                portfolio_check=portfolio_check,
                news_sentiment_avg=news_avg,
                atr_reference=atr_ref or 0.0,
                entry_price=0.0,  # preenchido no momento da execução real
            ),
        )

        with get_session() as session:
            opportunity_row = Opportunity(
                cycle_id=cycle_id,
                pair=opp.pair,
                strategy=opp.strategy,
                market_regime=opp.regime,
                indicators_json=opp.votes_summary,
                status="approved" if final.approve else "rejected",
                final_confidence=final.aggregated_confidence,
            )
            session.add(opportunity_row)
            session.flush()

            session.add(CommitteeDecision(
                opportunity_id=opportunity_row.id, agent_name=viability_agent.name,
                decision=viability_verdict.decision, confidence=viability_verdict.confidence,
                reasoning=viability_verdict.reasoning, model_used=viability_agent.model,
            ))
            session.add(CommitteeDecision(
                opportunity_id=opportunity_row.id, agent_name=portfolio_agent.name,
                decision="approve" if portfolio_check.approved else "reject",
                confidence=1.0 if portfolio_check.approved else 0.0,
                reasoning="; ".join(portfolio_check.reasons), model_used="rule-based",
            ))
            session.add(CommitteeDecision(
                opportunity_id=opportunity_row.id, agent_name=risk_committee.name,
                decision="approve" if final.approve else "reject",
                confidence=final.aggregated_confidence, reasoning=final.reasoning,
                model_used=risk_committee.model,
            ))

        redis_bridge.publish_event(
            "decisions",
            {"pair": opp.pair, "approved": final.approve, "confidence": final.aggregated_confidence},
        )

        if final.approve:
            quantity = portfolio_check.suggested_order_value_usdt  # simplificado: refinar conversão valor->quantidade com preço real
            await asyncio.to_thread(execution_agent.open_position, opp.pair, quantity, final)


def _manage_open_positions() -> None:
    """Checa stop/take/trailing e a flag de venda manual das posições abertas.
    Sem timeout — decisão de saída pode ser mais deliberada."""
    execution_agent = ExecutionAgent()
    with get_session() as session:
        positions = session.query(Position).filter_by(status="open").all()

    for position in positions:
        try:
            from bot.core.binance_client import binance_client
            current_price = float(binance_client._client.get_symbol_ticker(symbol=position.pair)["price"])  # noqa: SLF001
        except Exception:
            continue

        if position.sell_flag == "immediate":
            execution_agent.sell_position(position, reason="manual_flag", immediate=True)
            continue

        if position.stop_price and current_price <= position.stop_price:
            execution_agent.sell_position(position, reason="stop_loss", immediate=True)
            continue
        if position.take_price and current_price >= position.take_price:
            execution_agent.sell_position(position, reason="take_profit", immediate=True)
            continue

        execution_agent.update_trailing_stop(position, current_price)


def _check_circuit_breaker(total_equity_now: float, alert_pct: float) -> None:
    today = dt.date.today()
    with get_session() as session:
        row = session.get(DailyEquity, today)
        if row is None:
            session.add(DailyEquity(date=today, equity_brl=0.0, equity_usdt=total_equity_now))
            return
        start_of_day_equity = row.equity_usdt

    if circuit_breaker_triggered(start_of_day_equity, total_equity_now, alert_pct):
        alert(
            "Circuit breaker: perda diária relevante",
            f"Equity caiu de {start_of_day_equity:.2f} para {total_equity_now:.2f} USDT "
            f"(limite de alerta: {alert_pct:.0%}). Isso é só um aviso — o bot continua operando "
            "normalmente, conforme configurado.",
        )
        redis_bridge.publish_event("alerts", {"type": "circuit_breaker", "equity_now": total_equity_now})

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
from agents.position_review_agent import PositionReviewAgent
from agents.risk_committee_agent import RiskCommitteeAgent, RiskCommitteeInput
from agents.viability_agent import ViabilityAgent
from core import redis_bridge, vlog
from core.binance_client import binance_client
from core.config_store import load_runtime_config
from core.notifier import alert
from core.risk_rules import circuit_breaker_triggered, in_macro_risk_window, round_step_size
from config.settings import settings
from db.models import CommitteeDecision, DailyEquity, NewsItem, Opportunity, Position, WalletSnapshot
from db.session import get_session
from orchestrator.reconciliation import has_divergence, reconcile

logger = logging.getLogger("ivanvestai.cycle_runner")


async def run_cycle(*, ignore_macro_window: bool = False) -> None:
    """`ignore_macro_window`: SÓ pra teste manual explícito (ver
    run_cycle_once.py --ignore-macro-window) -- o scheduler automático do
    main.py sempre chama `run_cycle()` sem esse argumento.

    A partir de 16/09/2026 (ver arquitetura-tecnica.md 9.11) existe uma
    SEGUNDA forma de ignorar a janela, essa sim usada pelo scheduler
    automático: `config.bypass_macro_risk_window`, lida a cada ciclo da
    tabela `settings` (dashboard -> /settings, com aviso de risco explícito
    na tela). Diferente do `ignore_macro_window` (só teste manual pontual),
    esse fica ligado até o Ivan desligar de novo -- é uma decisão consciente
    de operar durante FOMC/CPI, não um atalho de debug.

    NENHUM dos dois afeta a segurança de capital (isso continua 100%
    governado por settings.dry_run, nunca sobrescrito por comando remoto) --
    só controlam se o bot considera abrir posição NOVA durante a janela.
    Gestão de posições já abertas nunca foi bloqueada pela janela macro."""
    from agents.news_agent import NewsAgent as _NewsAgent  # import local (evita ciclo)

    config = load_runtime_config()
    if config.bot_status != "running":
        logger.info("Bot pausado (settings.bot_status != running) — ciclo ignorado.")
        return

    if not redis_bridge.acquire_cycle_lock(ttl_seconds=config.entry_decision_timeout_seconds + 60):
        logger.warning("Ciclo anterior ainda em andamento (lock ativo) — pulando este disparo.")
        return

    cycle_id = str(uuid.uuid4())
    now = dt.datetime.now(dt.timezone.utc)
    vlog.cycle_banner(cycle_id, settings.dry_run)

    try:
        in_window, event_label = in_macro_risk_window(now)
        if in_window and ignore_macro_window:
            logger.warning(
                "Janela de risco macro (%s) ativa, mas IGNORADA a pedido explícito (teste manual).", event_label
            )
            vlog.warn(f"Janela de risco macro ativa ({event_label}) — IGNORADA a pedido explícito (teste manual).")
            in_window = False
        elif in_window and config.bypass_macro_risk_window:
            logger.warning(
                "Janela de risco macro (%s) ativa, mas IGNORADA -- desbloqueada nas configurações do dashboard.",
                event_label,
            )
            vlog.warn(
                f"Janela de risco macro ativa ({event_label}) — IGNORADA (desbloqueada em /settings pelo Ivan)."
            )
            in_window = False
        elif in_window:
            logger.info("Dentro da janela de risco macro (%s) — sem novas entradas neste ciclo.", event_label)
            vlog.warn(f"Janela de risco macro ativa ({event_label}) — sem novas entradas neste ciclo.")

        # --- Passo 1: agentes de coleta em paralelo -------------------
        vlog.section("Passo 1 — Coleta (em paralelo)", emoji="📡")
        vlog.step("📰", "NewsAgent", "buscando notícias relevantes...")
        vlog.step("💼", "PortfolioAgent", "lendo saldo/posições na Binance...")
        vlog.step("🔍", "MarketScannerAgent", "varrendo pares em busca de sinais técnicos...")

        news_agent_instance = _NewsAgent()
        portfolio_agent = PortfolioAgent()
        scanner_agent = MarketScannerAgent()

        news_items, wallet_snapshots, opportunities = await asyncio.gather(
            asyncio.to_thread(news_agent_instance.run),
            asyncio.to_thread(portfolio_agent.run),
            asyncio.to_thread(scanner_agent.run),
        )

        total_equity = PortfolioAgent.total_equity_usdt(wallet_snapshots)
        # Saldo LIVRE na stablecoin de segurança (não o patrimônio total) --
        # necessário porque max_allocation_pct_per_trade sugere um valor em %
        # do patrimônio TOTAL, mas a maior parte dele pode estar em outros
        # ativos (BTC, ETH etc), não em USDT disponível pra comprar algo novo.
        # Achado em 16/09/2026 (BinanceAPIException -2010 "insufficient
        # balance" na primeira ordem real, ver arquitetura-tecnica.md 9.13).
        available_stablecoin = next(
            (s.value_usdt for s in wallet_snapshots if s.asset == settings.safety_stablecoin), 0.0
        )
        redis_bridge.cache_set("balance", {"total_equity_usdt": total_equity, "ts": now.isoformat()})
        redis_bridge.publish_event("positions", {"total_equity_usdt": total_equity})

        vlog.money(f"Patrimônio total: ${total_equity:,.2f} USDT (livre em {settings.safety_stablecoin}: ${available_stablecoin:,.2f})")
        vlog.ok(f"{len(news_items)} notícia(s) coletada(s) | {len(opportunities)} oportunidade(s) do scanner")

        _check_circuit_breaker(total_equity, config.daily_loss_alert_pct)

        # Revisão de posições PRÉ-EXISTENTES na carteira (compradas manualmente
        # antes do bot existir, ou há muito tempo) -- roda todo ciclo,
        # independente de haver oportunidade de ENTRADA nova ou de estar numa
        # janela de risco macro (essas restrições são sobre abrir posição nova,
        # não sobre reavaliar o que já existe). Ver arquitetura-tecnica.md 9.8.
        vlog.section("Revisão de posições pré-existentes na carteira", emoji="🧐")
        await asyncio.to_thread(_review_wallet_positions, wallet_snapshots)

        if in_window or not opportunities:
            vlog.section("Ciclo encerrado", emoji="🏁", color="yellow")
            if not opportunities:
                vlog.warn("Nenhuma oportunidade encontrada pelo scanner neste ciclo — nada a avaliar.")
            vlog.ok("Coleta e checagens de segurança concluídas sem erros.")
            return

        # --- Passo 2-6: viabilidade + portfólio + reconciliação + veto final,
        # com timeout só para decisão de ENTRADA -------------------------
        vlog.section("Passo 2-6 — Viabilidade, portfólio e comitê de risco", emoji="🧭")
        try:
            await asyncio.wait_for(
                _evaluate_opportunities(opportunities, news_items, total_equity, available_stablecoin, cycle_id),
                timeout=config.entry_decision_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout de decisão de entrada (%ss) estourado — ciclo cancelado por segurança.",
                            config.entry_decision_timeout_seconds)
            vlog.fail(f"Timeout de decisão ({config.entry_decision_timeout_seconds}s) — ciclo cancelado por segurança.")
            redis_bridge.publish_event("alerts", {"type": "timeout", "message": "Timeout de decisão de entrada"})
        except Exception:
            # Rede de segurança extra além do try/except em volta da execução da
            # ordem logo acima: qualquer outro erro não previsto na avaliação de
            # oportunidades (ex: erro de rede/DB no meio do loop) NÃO pode
            # impedir a gestão de posições abertas de rodar -- é ela que checa
            # stop/take das posições REAIS já compradas. Ver arquitetura-tecnica.md
            # 9.13.
            logger.exception("Erro inesperado avaliando oportunidades — seguindo pra gestão de posições abertas.")
            vlog.fail("Erro inesperado avaliando oportunidades — ciclo segue pra gestão de posições abertas.")
            redis_bridge.publish_event(
                "alerts", {"type": "cycle_error", "message": "Erro inesperado avaliando oportunidades"}
            )

        # --- Gestão de posições abertas (sem timeout — saída pode ser deliberada) ---
        vlog.section("Gestão de posições abertas", emoji="🛡️")
        _manage_open_positions()

        vlog.banner("🏁  CICLO CONCLUÍDO", f"ciclo {cycle_id[:8]}", color="green")

    finally:
        redis_bridge.release_cycle_lock()


async def _evaluate_opportunities(
    opportunities: list, news_items: list[NewsItem], total_equity: float,
    available_stablecoin: float, cycle_id: str,
) -> None:
    viability_agent = ViabilityAgent()
    portfolio_agent = PortfolioComparisonAgent()
    risk_committee = RiskCommitteeAgent()
    execution_agent = ExecutionAgent()

    with get_session() as session:
        open_positions = session.query(Position).filter_by(status="open").all()

    for opp in opportunities:
        vlog.step("🔎", "Oportunidade", f"{opp.pair} ({opp.strategy}, regime={opp.regime})")
        relevant_news = [n for n in news_items if opp.pair.replace("USDT", "") in n.title_original.upper()]

        viability_verdict = await asyncio.to_thread(viability_agent.evaluate, opp, relevant_news)
        vlog.step("🧭", "ViabilityAgent", f"{viability_verdict.decision} (confiança={viability_verdict.confidence:.0%})")
        portfolio_check = await asyncio.to_thread(
            portfolio_agent.check, opp, total_equity, open_positions, available_stablecoin
        )
        vlog.step("🛡️", "PortfolioComparisonAgent", "aprovado" if portfolio_check.approved else "reprovado")

        if has_divergence(viability_verdict, portfolio_check):
            viability_verdict = await asyncio.to_thread(
                reconcile, viability_agent, opp, viability_verdict, portfolio_check
            )

        news_avg = sum(n.sentiment_score for n in relevant_news) / len(relevant_news) if relevant_news else 0.0
        atr_ref = opp.votes_summary.get("atr_reference", {}).get("value", {}).get("atr", 0.0)

        # Preço atual -- necessário tanto pro ATR% que o RiskCommitteeAgent usa
        # pra calibrar stop/take (antes vinha sempre 0.0 e o atr_pct caía no
        # fallback de 1.0, sem refletir a volatilidade real do ativo) quanto
        # pra converter o valor sugerido em USD numa quantidade real do ativo
        # logo abaixo (bug corrigido nesta revisão -- ver arquitetura-tecnica.md
        # 9.6: o valor em dólar era mandado direto como "quantidade" pra
        # Binance, o que teria gerado uma ordem com erro de grandeza gigantesco).
        try:
            current_price = binance_client.get_last_price(opp.pair)
        except Exception:
            logger.warning("Não consegui buscar o preço atual de %s -- oportunidade pulada neste ciclo.", opp.pair)
            vlog.fail(f"Não consegui buscar o preço atual de {opp.pair} — pulando esta oportunidade.")
            continue

        final = await asyncio.to_thread(
            risk_committee.decide,
            RiskCommitteeInput(
                pair=opp.pair,
                viability_verdict=viability_verdict,
                portfolio_check=portfolio_check,
                news_sentiment_avg=news_avg,
                atr_reference=atr_ref or 0.0,
                entry_price=current_price,
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
            vlog.ok(f"⚖️  RiskCommitteeAgent: APROVADO (confiança={final.aggregated_confidence:.0%}) — indo pra execução.")
        else:
            vlog.warn(f"⚖️  RiskCommitteeAgent: reprovado ({final.reasoning[:80]})")

        if final.approve:
            # Converte o valor sugerido (USD) numa quantidade real do ativo e
            # arredonda pro LOT_SIZE da Binance (bugs corrigidos nesta revisão,
            # ver arquitetura-tecnica.md 9.6 -- antes o valor em dólar era
            # mandado direto como "quantidade" pra Binance sem conversão nem
            # arredondamento, o que teria gerado ordens com erro de grandeza
            # gigantesco e provavelmente rejeitadas pelo filtro LOT_SIZE mesmo
            # assim).
            raw_quantity = portfolio_check.suggested_order_value_usdt / current_price
            try:
                filters = binance_client.get_symbol_filters(opp.pair)
                lot_size = filters.get("LOT_SIZE", {})
                step_size = float(lot_size.get("stepSize", 0) or 0)
                min_qty = float(lot_size.get("minQty", 0) or 0)
            except Exception:
                step_size, min_qty = 0.0, 0.0

            quantity = round_step_size(raw_quantity, step_size) if step_size else raw_quantity

            if quantity <= 0 or quantity < min_qty:
                logger.warning(
                    "Quantidade calculada pra %s (%.8f) ficou abaixo do mínimo da Binance "
                    "(minQty=%.8f) depois de arredondar pro LOT_SIZE -- entrada pulada.",
                    opp.pair, quantity, min_qty,
                )
                vlog.warn(f"Quantidade calculada pra {opp.pair} ficou abaixo do mínimo da Binance — entrada pulada.")
                continue

            # A ordem pode falhar por motivo alheio ao cálculo acima (ex: saldo
            # livre em USDT menor que os 50% sugeridos, porque a maior parte do
            # patrimônio total está em outros ativos -- -2010 "insufficient
            # balance", achado em 16/09/2026 rodando em produção pela primeira
            # vez, ver arquitetura-tecnica.md 9.12/9.13). Sem este try/except, a
            # exceção subia sem tratamento até fora do asyncio.wait_for logo
            # abaixo e derrubava o ciclo inteiro ANTES de _manage_open_positions()
            # rodar -- ou seja, uma tentativa de compra que falha podia pular a
            # checagem de stop/take de posições reais já abertas naquele ciclo.
            try:
                await asyncio.to_thread(execution_agent.open_position, opp.pair, quantity, final)
                tag = "SIMULADA (dry-run)" if settings.dry_run else "REAL"
                vlog.entry(f"{opp.pair}: {quantity} @ ~${current_price:,.4f}  [{tag}]")
            except Exception as exc:
                logger.error("Falha ao executar ordem de compra para %s: %s", opp.pair, exc)
                vlog.fail(f"Falha ao executar ordem para {opp.pair}: {exc} — oportunidade pulada, ciclo continua.")
                redis_bridge.publish_event(
                    "alerts", {"type": "execution_error", "pair": opp.pair, "message": str(exc)}
                )
                continue


def _review_wallet_positions(wallet_snapshots: list[WalletSnapshot]) -> None:
    """Avalia hold/sell pra cada ativo da carteira real que o bot não
    comprou por conta própria (ver PositionReviewAgent). Ignora stablecoin,
    poeira e ativos já geridos por uma Position aberta do bot."""
    reviews = PositionReviewAgent().run(wallet_snapshots)
    if not reviews:
        vlog.ok("Nenhuma posição pré-existente pra revisar neste ciclo "
                 "(tudo poeira, stablecoin, ou já gerenciado pelo bot).")
    else:
        sold = sum(1 for r in reviews if r.acted)
        vlog.ok(f"{len(reviews)} posição(ões) revisada(s), {sold} venda(s) executada(s).")


def _manage_open_positions() -> None:
    """Checa stop/take/trailing e a flag de venda manual das posições abertas.
    Sem timeout — decisão de saída pode ser mais deliberada."""
    execution_agent = ExecutionAgent()
    with get_session() as session:
        positions = session.query(Position).filter_by(status="open").all()

    if not positions:
        vlog.ok("Nenhuma posição aberta pra gerenciar.")
        return

    for position in positions:
        try:
            current_price = binance_client.get_last_price(position.pair)
        except Exception:
            vlog.fail(f"Não consegui buscar o preço atual de {position.pair} — pulando gestão desta posição.")
            continue

        if position.sell_flag == "immediate":
            execution_agent.sell_position(position, reason="manual_flag", immediate=True)
            vlog.exit_(f"{position.pair} @ ~${current_price:,.4f} (flag manual)", positive=True)
            continue

        if position.stop_price and current_price <= position.stop_price:
            execution_agent.sell_position(position, reason="stop_loss", immediate=True)
            vlog.exit_(f"{position.pair} @ ~${current_price:,.4f} (🛑 stop loss)", positive=False)
            continue
        if position.take_price and current_price >= position.take_price:
            execution_agent.sell_position(position, reason="take_profit", immediate=True)
            vlog.exit_(f"{position.pair} @ ~${current_price:,.4f} (🎉 take profit)", positive=True)
            continue

        execution_agent.update_trailing_stop(position, current_price)
        vlog.step("👀", position.pair, f"seguindo aberta @ ~${current_price:,.4f}, sem gatilho de saída.")


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
        vlog.fail(f"Circuit breaker: equity caiu de ${start_of_day_equity:,.2f} pra ${total_equity_now:,.2f} "
                  f"(limite de alerta: {alert_pct:.0%}). Só um aviso — o bot segue operando normalmente.")
        redis_bridge.publish_event("alerts", {"type": "circuit_breaker", "equity_now": total_equity_now})
    else:
        vlog.ok("Circuit breaker: dentro do limite diário de perda.")

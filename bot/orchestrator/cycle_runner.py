"""Orquestra um ciclo completo do comitê (a cada 15 min por padrão).
Ver arquitetura-tecnica.md seção 3.2 para o fluxo de referência."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
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
from core.equity import daily_market_pnl_usdt
from core.logging_setup import cycle_id_var
from core.notifier import alert
from core.risk_rules import circuit_breaker_triggered, in_macro_risk_window, round_step_size
from config.settings import settings
from db.models import CommitteeDecision, DailyEquity, NewsItem, Opportunity, Position, WalletSnapshot
from db.session import get_session
from orchestrator.reconciliation import has_divergence, reconcile

logger = logging.getLogger("ivanvestai.cycle_runner")

# Piso conservador do valor mínimo de ordem da Binance (a maioria dos pares USDT
# exige US$5; o valor exato por par é checado no PortfolioComparisonAgent).
MIN_ORDER_VALUE_USDT = 5.0


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

    # TTL do lock cobre coleta (~100 pares x 3 timeframes) + avaliação + gestão;
    # um TTL curto deixava o lock expirar no meio de um ciclo lento.
    if not redis_bridge.acquire_cycle_lock(ttl_seconds=max(600, config.entry_decision_timeout_seconds * 4)):
        logger.warning("Ciclo anterior ainda em andamento (lock ativo) — pulando este disparo.")
        return

    cycle_id = str(uuid.uuid4())
    cycle_token = cycle_id_var.set(cycle_id)  # cada linha de log (arquivo/banco) sai marcada com o ciclo
    now = dt.datetime.now(dt.timezone.utc)

    try:
        vlog.cycle_banner(cycle_id, settings.dry_run)

        # Gestão de posições abertas (stop/take/trailing/flag manual) PRIMEIRO,
        # antes da coleta (que faz ~300 chamadas HTTP e pode falhar) e antes de
        # qualquer `return` antecipado. Bug corrigido em 18/09/2026: ela ficava
        # no fim do ciclo, depois do `return` de "janela macro ou sem
        # oportunidades" -- ou seja, na maioria dos ciclos (scanner vazio) e no
        # dia inteiro do FOMC as posições reais nunca tinham stop/take checados.
        # Só precisa do banco e do preço atual. Sem timeout -- saída pode ser deliberada.
        vlog.section("Gestão de posições abertas", emoji="🛡️")
        await _safe_manage_open_positions()

        if config.bot_status != "running":
            # "Pausado" bloqueia só ENTRADAS novas. Antes, pausar (kill switch)
            # deixava posições reais sem nenhuma proteção e fazia o force_sell
            # nunca executar (revisão de 18/09/2026).
            logger.info("Bot pausado (settings.bot_status != running) — sem novas entradas; posições foram gerenciadas.")
            vlog.warn("Bot pausado — sem novas entradas (posições abertas já foram gerenciadas).")
            return

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

        try:
            _check_circuit_breaker(total_equity, config.daily_loss_alert_pct)
        except Exception:
            logger.exception("Falha na checagem do circuit breaker — ciclo segue.")
            vlog.fail("Falha na checagem do circuit breaker — ciclo segue mesmo assim.")

        # Revisão de posições PRÉ-EXISTENTES na carteira (compradas manualmente
        # antes do bot existir, ou há muito tempo) -- roda independente de haver
        # oportunidade de ENTRADA nova ou de estar numa janela de risco macro
        # (essas restrições são sobre abrir posição nova, não sobre reavaliar o
        # que já existe). Ver arquitetura-tecnica.md 9.8.
        vlog.section("Revisão de posições pré-existentes na carteira", emoji="🧐")
        try:
            await asyncio.to_thread(_review_wallet_positions, wallet_snapshots)
        except Exception:
            logger.exception("Erro inesperado na revisão de posições pré-existentes — ciclo segue.")
            vlog.fail("Erro na revisão de posições pré-existentes — ciclo segue mesmo assim.")

        if in_window or not opportunities:
            vlog.section("Ciclo encerrado", emoji="🏁", color="yellow")
            if not opportunities:
                vlog.warn("Nenhuma oportunidade encontrada pelo scanner neste ciclo — nada a avaliar.")
            vlog.ok("Coleta e checagens de segurança concluídas sem erros.")
            return

        # --- Passo 2-6: viabilidade + portfólio + reconciliação + veto final ----
        # O orçamento de tempo (entry_decision_timeout_seconds) é um PRAZO checado
        # entre oportunidades dentro de _evaluate_opportunities, não um
        # asyncio.wait_for: cancelar o await não para a thread (asyncio.to_thread),
        # então uma ordem já enviada podia seguir rodando "depois do timeout".
        vlog.section("Passo 2-6 — Viabilidade, portfólio e comitê de risco", emoji="🧭")
        try:
            await _evaluate_opportunities(
                opportunities, news_items, total_equity, available_stablecoin, cycle_id,
                deadline=time.monotonic() + config.entry_decision_timeout_seconds,
            )
        except Exception:
            # Rede de segurança: qualquer erro não previsto na avaliação de
            # oportunidades (ex: erro de rede/DB no meio do loop) não pode
            # derrubar o ciclo. Ver arquitetura-tecnica.md 9.13.
            logger.exception("Erro inesperado avaliando oportunidades.")
            vlog.fail("Erro inesperado avaliando oportunidades — ciclo encerrado.")
            redis_bridge.publish_event(
                "alerts", {"type": "cycle_error", "message": "Erro inesperado avaliando oportunidades"}
            )

        vlog.banner("🏁  CICLO CONCLUÍDO", f"ciclo {cycle_id[:8]}", color="green")

    finally:
        redis_bridge.release_cycle_lock()
        cycle_id_var.reset(cycle_token)


# Um único "gestor" por vez: o ciclo (15 min) e o monitor rápido (60s) chamam a mesma
# gestão, e dois vendendo a mesma posição ao mesmo tempo gerariam ordem duplicada.
_manage_lock = asyncio.Lock()
_last_management_error: dict[str, tuple[str, float]] = {}


async def _safe_manage_open_positions(quiet: bool = False) -> None:
    """Gestão de posições abertas em thread (não bloqueia o event loop) e com
    rede de segurança: qualquer erro fora do loop por posição (ex: falha na
    query inicial) não pode impedir o ciclo de seguir/liberar o lock. Ver
    arquitetura-tecnica.md 9.14."""
    async with _manage_lock:
        try:
            await asyncio.to_thread(_manage_open_positions, quiet)
        except Exception:
            logger.exception("Erro inesperado gerenciando posições abertas.")
            vlog.fail("Erro inesperado gerenciando posições abertas — ciclo segue mesmo assim.")
            redis_bridge.publish_event(
                "alerts", {"type": "cycle_error", "message": "Erro inesperado gerenciando posições abertas"}
            )


async def monitor_open_positions() -> None:
    """Monitor rápido (a cada `risk_monitor_seconds`, padrão 60s), entre os ciclos de
    15 min: só confere stop/take/trailing das posições abertas -- não coleta, não usa
    LLM. Antes o stop só era checado a cada 15 min, então uma queda rápida passava do
    stop sem reação (revisão de 19/09/2026). Se o ciclo (ou outro monitor) já está
    gerindo as posições, pula esta rodada."""
    if _manage_lock.locked():
        return
    await _safe_manage_open_positions(quiet=True)


async def _evaluate_opportunities(
    opportunities: list, news_items: list[NewsItem], total_equity: float,
    available_stablecoin: float, cycle_id: str, deadline: float | None = None,
) -> None:
    viability_agent = ViabilityAgent()
    portfolio_agent = PortfolioComparisonAgent()
    risk_committee = RiskCommitteeAgent()
    execution_agent = ExecutionAgent()

    with get_session() as session:
        open_positions = session.query(Position).filter_by(status="open").all()

    # Melhores primeiro: se o prazo estourar, o que fica sem avaliar é o de menor confluência.
    opportunities = sorted(opportunities, key=lambda o: o.confluence, reverse=True)

    for opp in opportunities:
        # Sem saldo livre pra uma ordem mínima, nenhuma oportunidade restante pode
        # virar entrada -- não gasta chamadas de API avaliando-as (nos logs de
        # 18/09/2026, depois da 1ª compra o saldo caiu pra $0,62 e o ciclo seguiu
        # reprovando dezenas de oportunidades uma a uma).
        if available_stablecoin < MIN_ORDER_VALUE_USDT:
            vlog.warn(
                f"Saldo livre (${available_stablecoin:,.2f}) abaixo do mínimo de ordem "
                f"(${MIN_ORDER_VALUE_USDT:.0f}) — sem novas entradas neste ciclo."
            )
            return

        if deadline is not None and time.monotonic() > deadline:
            logger.warning("Prazo de decisão de entrada estourado — oportunidades restantes ficam pro próximo ciclo.")
            vlog.warn("Prazo de decisão de entrada estourado — oportunidades restantes ficam pro próximo ciclo.")
            redis_bridge.publish_event("alerts", {"type": "timeout", "message": "Prazo de decisão de entrada estourado"})
            return

        vlog.step("🔎", "Oportunidade", f"{opp.pair} ({opp.strategy}, regime={opp.regime})")
        relevant_news = [n for n in news_items if opp.pair.replace("USDT", "") in n.title_original.upper()]

        # Portfólio (regras determinísticas, sem LLM) ANTES do ViabilityAgent
        # (gpt-4o): se reprovou por regra dura (saldo, minNotional, correlação) o
        # comitê veta de qualquer jeito, então a chamada de LLM seria custo puro
        # -- nos logs de 18/09/2026 eram dezenas de chamadas por ciclo (x96 ciclos/dia).
        portfolio_check = await asyncio.to_thread(
            portfolio_agent.check, opp, total_equity, open_positions, available_stablecoin
        )
        if not portfolio_check.approved:
            reasons_text = "; ".join(portfolio_check.reasons)
            vlog.step("🛡️", "PortfolioComparisonAgent", f"reprovado — {reasons_text}")
            _record_portfolio_rejection(cycle_id, opp, portfolio_agent, risk_committee, reasons_text)
            redis_bridge.publish_event("decisions", {"pair": opp.pair, "approved": False, "confidence": 0.0})
            continue
        vlog.step("🛡️", "PortfolioComparisonAgent", f"aprovado — {'; '.join(portfolio_check.reasons)}")

        viability_verdict = await asyncio.to_thread(viability_agent.evaluate, opp, relevant_news)
        vlog.step("🧭", "ViabilityAgent", f"{viability_verdict.decision} (confiança={viability_verdict.confidence:.0%})")

        # Aqui o portfólio já aprovou; reconcilia só se o ViabilityAgent discordar.
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
                trade = await asyncio.to_thread(execution_agent.open_position, opp.pair, quantity, final)
                tag = "SIMULADA (dry-run)" if settings.dry_run else "REAL"
                vlog.entry(f"{opp.pair}: {trade.quantity} @ ~${trade.price:,.4f}  [{tag}]")
                # Estado da própria rodada: o saldo livre gasto e a posição nova
                # valem pras oportunidades seguintes do MESMO ciclo (antes todas
                # assumiam o saldo inicial e as compras seguintes falhavam com -2010).
                available_stablecoin = max(available_stablecoin - trade.quantity * trade.price, 0.0)
                with get_session() as session:
                    open_positions = session.query(Position).filter_by(status="open").all()
            except Exception as exc:
                logger.error("Falha ao executar ordem de compra para %s: %s", opp.pair, exc)
                vlog.fail(f"Falha ao executar ordem para {opp.pair}: {exc} — oportunidade pulada, ciclo continua.")
                redis_bridge.publish_event(
                    "alerts", {"type": "execution_error", "pair": opp.pair, "message": str(exc)}
                )
                continue


def _record_portfolio_rejection(
    cycle_id: str, opp, portfolio_agent: PortfolioComparisonAgent, risk_committee: RiskCommitteeAgent, reasons_text: str
) -> None:
    """Registra uma oportunidade barrada pelas regras de portfólio, sem passar
    pelo ViabilityAgent/LLM (não há linha de viabilidade nesse caso)."""
    with get_session() as session:
        opportunity_row = Opportunity(
            cycle_id=cycle_id, pair=opp.pair, strategy=opp.strategy, market_regime=opp.regime,
            indicators_json=opp.votes_summary, status="rejected", final_confidence=0.0,
        )
        session.add(opportunity_row)
        session.flush()
        session.add(CommitteeDecision(
            opportunity_id=opportunity_row.id, agent_name=portfolio_agent.name,
            decision="reject", confidence=0.0, reasoning=reasons_text, model_used="rule-based",
        ))
        session.add(CommitteeDecision(
            opportunity_id=opportunity_row.id, agent_name=risk_committee.name,
            decision="reject", confidence=0.0,
            reasoning=f"Vetado pelas regras de portfólio (sem chamada de LLM): {reasons_text}",
            model_used="rule-based",
        ))


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


def _manage_open_positions(quiet: bool = False) -> None:
    """Checa stop/take/trailing e a flag de venda manual das posições abertas.
    Sem timeout — decisão de saída pode ser mais deliberada.

    `quiet=True` (monitor rápido a cada minuto): só loga quando há saída ou erro --
    "seguindo aberta" a cada 60s encheria arquivo e tabela bot_logs de ruído."""
    execution_agent = ExecutionAgent()
    with get_session() as session:
        positions = session.query(Position).filter_by(status="open").all()

    if not positions:
        if not quiet:
            vlog.ok("Nenhuma posição aberta pra gerenciar.")
        return

    for position in positions:
        try:
            current_price = binance_client.get_last_price(position.pair)
        except Exception:
            vlog.fail(f"Não consegui buscar o preço atual de {position.pair} — pulando gestão desta posição.")
            continue

        # Cada posição gerenciada isoladamente -- uma venda que falha (ex:
        # -2010 insufficient balance, -2015 permissão, erro de rede) não pode
        # impedir a checagem de stop/take das OUTRAS posições abertas no
        # mesmo ciclo. Achado em 16/09/2026: uma posição de teste em paper
        # (is_paper=True, nenhum ativo real comprado) bateu take profit com
        # dry_run já desligado e o bot tentou vender de verdade um ativo que
        # nunca existiu na conta -- sem este try/except, isso derrubava o
        # _manage_open_positions() inteiro no meio do loop. Ver
        # arquitetura-tecnica.md 9.14 (causa raiz corrigida em
        # agents/execution_agent.py -- venda agora respeita position.is_paper,
        # não settings.dry_run atual; este try/except é a rede de segurança
        # extra pra qualquer outra falha de venda, real ou não).
        try:
            # Tag [SIMULADA]/[REAL] no log de saída usa position.is_paper (a
            # mesma fonte de verdade do fix da seção 9.14), não settings.dry_run
            # atual -- os dois podem divergir numa carteira com posições
            # abertas em regimes diferentes.
            tag = "SIMULADA (paper)" if position.is_paper else "REAL"

            exit_reason = None
            if position.sell_flag == "immediate":
                exit_reason, label, positive = "manual_flag", "flag manual", True
            elif position.stop_price and current_price <= position.stop_price:
                exit_reason, label, positive = "stop_loss", "🛑 stop loss", False
            elif position.take_price and current_price >= position.take_price:
                exit_reason, label, positive = "take_profit", "🎉 take profit", True

            if exit_reason:
                trade = execution_agent.sell_position(position, reason=exit_reason, immediate=True)
                if trade is None:
                    vlog.warn(f"{position.pair}: posição sem saldo real na Binance — fechada só no banco (ver alerta).")
                else:
                    vlog.exit_(f"{position.pair} @ ~${trade.price:,.4f} ({label})  [{tag}]", positive=positive)
                continue

            execution_agent.update_trailing_stop(position, current_price)
            if not quiet:
                vlog.step("👀", position.pair, f"seguindo aberta @ ~${current_price:,.4f}, sem gatilho de saída.")
        except Exception as exc:
            # Com o monitor rodando a cada minuto, uma venda que falha repetiria o
            # mesmo erro/alerta 60x por hora: só registra de novo se a mensagem mudou
            # ou passaram 15 min desde o último aviso desta posição.
            now_mono = time.monotonic()
            last = _last_management_error.get(str(position.id))
            if last is None or last[0] != str(exc) or now_mono - last[1] > 900:
                _last_management_error[str(position.id)] = (str(exc), now_mono)
                logger.error("Falha ao gerenciar posição %s (id=%s): %s", position.pair, position.id, exc)
                vlog.fail(f"Falha ao gerenciar {position.pair}: {exc} — posição mantida, seguindo pras outras.")
                redis_bridge.publish_event(
                    "alerts", {"type": "position_management_error", "pair": position.pair, "message": str(exc)}
                )
            continue


def _check_circuit_breaker(total_equity_now: float, alert_pct: float) -> None:
    """Alerta de perda diária baseado no P&L de MERCADO do dia (core/equity.py), não na
    diferença bruta de patrimônio: aportes e retiradas manuais não contam como perda
    (achado em 19/09/2026: alerta de "-28%" causado por US$26,6 em NEAR movidos pra fora
    da conta, sem trade nenhum). O e-mail/push sai UMA vez por dia; nos ciclos seguintes
    a situação só aparece no log."""
    today = dt.date.today()
    with get_session() as session:
        row = session.get(DailyEquity, today)
        if row is None:
            session.add(DailyEquity(date=today, equity_brl=0.0, equity_usdt=total_equity_now))
            return
        start_of_day_equity = row.equity_usdt

    pnl_today = daily_market_pnl_usdt(today)
    pnl_pct = pnl_today / start_of_day_equity if start_of_day_equity > 0 else 0.0

    if circuit_breaker_triggered(start_of_day_equity, start_of_day_equity + pnl_today, alert_pct):
        msg = (f"Circuit breaker: variação de MERCADO hoje ${pnl_today:+,.2f} ({pnl_pct:+.1%}) sobre "
               f"${start_of_day_equity:,.2f} no início do dia (limite de alerta: {alert_pct:.0%}). "
               "Aportes/retiradas não contam. Só um aviso — o bot segue operando.")
        if redis_bridge.once_per_day(f"circuit_breaker:{today.isoformat()}"):
            alert("Circuit breaker: perda diária relevante", msg)
            vlog.fail(msg)
            redis_bridge.publish_event("alerts", {"type": "circuit_breaker", "pnl_today": pnl_today})
        else:
            vlog.warn(f"Circuit breaker ainda acima do limite hoje (alerta já enviado): "
                      f"variação de mercado ${pnl_today:+,.2f} ({pnl_pct:+.1%}).")
    else:
        vlog.ok(f"Circuit breaker: variação de mercado hoje ${pnl_today:+,.2f} ({pnl_pct:+.1%}) — dentro do limite.")

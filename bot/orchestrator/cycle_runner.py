"""Orquestra um ciclo completo do comitê (a cada 15 min por padrão).
Ver arquitetura-tecnica.md seção 3.2 para o fluxo de referência."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
import time
import uuid
from zoneinfo import ZoneInfo

from agents.execution_agent import ExecutionAgent
from agents.market_scanner_agent import MarketScannerAgent
from agents.portfolio_agent import PortfolioAgent
from agents.portfolio_comparison_agent import PortfolioComparisonAgent
from agents.position_review_agent import PositionReviewAgent
from agents.risk_committee_agent import FinalDecision, RiskCommitteeAgent, RiskCommitteeInput
from agents.viability_agent import ViabilityAgent
from core import redis_bridge, vlog
from core.account_context import AccountContext, load_active_accounts
from core.binance_client import BinanceClient, binance_client
from core import connection_alert
from core.config_store import RuntimeConfig, load_runtime_config
from core.equity import daily_market_pnl_usdt
from core.logging_setup import cycle_id_var
from core.notifier import alert
from core.order_utils import oco_is_filled, summarize_oco_orders
from core.risk_rules import MemeCoinSignals, circuit_breaker_triggered, in_macro_risk_window, meme_coin_eligible, round_step_size
from config.settings import settings
from core import strategy_profile
from db.models import CommitteeDecision, DailyEquity, NewsItem, Opportunity, Position, Setting, WalletSnapshot
from db.session import get_session
from sqlalchemy import func
from orchestrator.reconciliation import has_divergence, reconcile

logger = logging.getLogger("ivanvestai.cycle_runner")

# Piso conservador do valor mínimo de ordem da Binance (a maioria dos pares USDT
# exige US$5; o valor exato por par é checado no PortfolioComparisonAgent).
MIN_ORDER_VALUE_USDT = 5.0


# Travas anti-recompra (28/09/2026, ver config/settings.py pair_cooldown_hours).
# 29/09/2026, a pedido do Ivan: as travas valem POR CONTA. Cada conta é a
# carteira de uma pessoa diferente -- uma conta nunca bloqueia nem influencia a
# decisão da outra (duas contas podem estar na mesma moeda ao mesmo tempo).


def _pair_entry_block_reason(pair: str, account_id) -> str | None:
    """Motivo pra ESTA conta não entrar nesta moeda agora, ou None. Olha só as
    posições da própria conta: posição já aberta, fechamento recente (pausa)
    e quantidade de entradas nas últimas 24h."""
    now = dt.datetime.now(dt.timezone.utc)
    with get_session() as session:
        if session.query(Position.id).filter(Position.pair == pair, Position.account_id == account_id, Position.status == "open").first():
            return "já existe posição aberta nesta moeda nesta conta"
        last_closed = (
            session.query(func.max(Position.closed_at))
            .filter(Position.pair == pair, Position.account_id == account_id, Position.status == "closed")
            .scalar()
        )
        if last_closed is not None:
            if last_closed.tzinfo is None:
                last_closed = last_closed.replace(tzinfo=dt.timezone.utc)
            elapsed_h = (now - last_closed).total_seconds() / 3600
            if elapsed_h < settings.pair_cooldown_hours:
                return (
                    f"pausa pós-fechamento: fechada há {elapsed_h * 60:.0f} min "
                    f"(espera {settings.pair_cooldown_hours:g}h)"
                )
        entries_24h = (
            session.query(func.count(Position.id))
            .filter(Position.pair == pair, Position.account_id == account_id, Position.opened_at >= now - dt.timedelta(hours=24))
            .scalar()
        ) or 0
        if entries_24h >= settings.max_entries_per_pair_per_day:
            return f"já teve {entries_24h} entrada(s) nas últimas 24h (máx {settings.max_entries_per_pair_per_day})"
    return None


_PAUSE_STATE_KEY = "final_pause_state"


def _final_entry_pause(account: AccountContext, total_equity: float, day_pnl_pct: float | None) -> str | None:
    """Pausa de novas entradas do perfil final (core/strategy_profile.py), por
    conta. O estado (pico de patrimônio e fim da pausa) fica numa linha própria
    da tabela settings (key=final_pause_state, account_id=<conta>), que o
    config_store ignora. Pra zerar o pico depois de uma RETIRADA manual (que
    parece queda), basta apagar essa linha -- o próximo ciclo recomeça do
    patrimônio atual."""
    now = dt.datetime.now(dt.timezone.utc)
    with get_session() as session:
        row = session.query(Setting).filter(Setting.key == _PAUSE_STATE_KEY, Setting.account_id == account.id).first()
        state = strategy_profile.PauseState()
        if row is not None:
            try:
                raw = json.loads(row.value)
                state.peak_equity = raw.get("peak")
                until = raw.get("until")
                state.paused_until = dt.datetime.fromisoformat(until) if until else None
            except (ValueError, TypeError):
                logger.warning("[%s] final_pause_state ilegível (%r) -- recomeçando do zero.", account.label, row.value)
        reason, new_state = strategy_profile.entry_pause_decision(
            now=now, equity=total_equity, day_market_pnl_pct=day_pnl_pct, state=state,
            daily_loss_pct=settings.final_pause_daily_loss_pct,
            drawdown_pct=settings.final_pause_drawdown_pct,
            pause_days=settings.final_pause_days,
        )
        value = json.dumps({
            "peak": new_state.peak_equity,
            "until": new_state.paused_until.isoformat() if new_state.paused_until else None,
        })
        if row is None:
            session.add(Setting(key=_PAUSE_STATE_KEY, value=value, account_id=account.id))
        elif row.value != value:
            row.value = value
    return reason


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
    # um TTL curto deixava o lock expirar no meio de um ciclo lento. Antes era um
    # piso fixo de 600s (10 min) -- fazia sentido quando o ciclo era de 15 min (o
    # TTL nunca passava do próprio intervalo), mas com o ciclo em 5 min (ver
    # multi-conta-plano.md 10.19) um travamento de verdade (processo morrendo sem
    # liberar o lock) ia bloquear DOIS disparos seguintes em vez de só um. Agora o
    # piso acompanha o intervalo configurado -- trava, na pior hipótese, só o
    # próximo disparo, nunca mais que isso.
    lock_ttl_seconds = max(config.cycle_interval_minutes * 60, config.entry_decision_timeout_seconds * 4)
    if not redis_bridge.acquire_cycle_lock(ttl_seconds=lock_ttl_seconds):
        logger.warning("Ciclo anterior ainda em andamento (lock ativo) — pulando este disparo.")
        return

    cycle_id = str(uuid.uuid4())
    cycle_token = cycle_id_var.set(cycle_id)  # cada linha de log (arquivo/banco) sai marcada com o ciclo
    now = dt.datetime.now(dt.timezone.utc)

    try:
        # Multi-conta (multi-conta-plano.md, Fase C): carregado uma vez por ciclo --
        # cada conta ativa (com seu próprio BinanceClient/dry_run já resolvidos) é
        # iterada abaixo pra coleta de carteira, circuit breaker, revisão de
        # posições pré-existentes e avaliação de oportunidades novas. Lista vazia
        # (nenhuma conta ativa) não impede o ciclo de rodar -- só não há nada pra
        # coletar/avaliar; a gestão de posições já abertas roda de qualquer jeito.
        #
        # Achado 25-26/09/2026 (multi-conta-plano.md, ver 10.14): construir o
        # BinanceClient de cada conta aqui dispara um ping() de rede (dentro do
        # próprio python-binance, sem retry nenhum do lado do bot) -- um soluço
        # transitório na Binance/rede (timeout de handshake SSL, read timeout)
        # nesse ping derrubava ESTE `run_cycle` inteiro sem chegar nem a tentar
        # `_safe_manage_open_positions()` logo abaixo, ou seja, um problema de
        # rede de alguns segundos custava o ciclo de 15 min inteiro pras DUAS
        # contas (nenhuma leitura de carteira, nenhum circuit breaker, nenhuma
        # avaliação de oportunidade). O monitor de 60s já tem essa mesma rede de
        # segurança (`_safe_manage_open_positions` abaixo); aqui não tinha nenhuma.
        # Degrada exatamente como o caso já tratado de "nenhuma conta ativa"
        # logo abaixo -- não bloqueia a gestão de posições já abertas.
        try:
            accounts = load_active_accounts()
        except Exception:
            logger.exception("Falha carregando contas ativas -- ciclo segue sem coleta/avaliação desta vez.")
            vlog.fail("Falha carregando contas ativas (rede/Binance) -- ciclo segue mesmo assim, sem novas entradas.")
            redis_bridge.publish_event(
                "alerts", {"type": "cycle_error", "message": "Falha carregando contas ativas (rede/Binance)"}
            )
            accounts = []
        if not accounts:
            logger.critical("Nenhuma conta ativa encontrada em accounts -- ciclo não tem carteira pra coletar.")
        vlog.cycle_banner(cycle_id, [(a.label, a.dry_run) for a in accounts])

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
        # NewsAgent e MarketScannerAgent rodam UMA VEZ por ciclo, compartilhados
        # entre todas as contas (notícias e oportunidades técnicas não dependem
        # de qual conta Binance está sendo lida -- só a carteira/saldo é por
        # conta). PortfolioAgent passa a rodar uma vez POR CONTA logo abaixo
        # (multi-conta-plano.md, Fase C).
        vlog.section("Passo 1 — Coleta (em paralelo)", emoji="📡")
        vlog.step("📰", "NewsAgent", "buscando notícias relevantes...")
        vlog.step("🔍", "MarketScannerAgent", "varrendo pares em busca de sinais técnicos...")

        news_agent_instance = _NewsAgent()
        scanner_agent = MarketScannerAgent()

        news_items, opportunities = await asyncio.gather(
            asyncio.to_thread(news_agent_instance.run),
            asyncio.to_thread(scanner_agent.run),
        )
        vlog.ok(f"{len(news_items)} notícia(s) coletada(s) | {len(opportunities)} oportunidade(s) do scanner")

        # --- Passo 1b: carteira + circuit breaker + revisão, POR CONTA --------
        # Cada conta tem seu próprio saldo, seu próprio "início de dia"
        # (DailyEquity por account_id, ver migrate_add_daily_equity_pk.py) e sua
        # própria revisão de posições pré-existentes -- mas todas usam as MESMAS
        # notícias/oportunidades coletadas acima. O total agregado (soma de
        # todas as contas) é o que vai pro cache "balance" que o dashboard lê
        # hoje (visão combinada -- ver multi-conta-plano.md seção 6; visão por
        # conta fica pra Fase E).
        vlog.section("Passo 1b — Carteira, circuit breaker e revisão (por conta)", emoji="💼")
        # account_states carrega account_config desde 25/09/2026 (multi-conta-plano.md,
        # Fase E, ver 10.8): cada conta agora lê SEU PRÓPRIO RuntimeConfig
        # (load_runtime_config(account_id=account.id), que mescla os overrides
        # "master" da tabela settings com os específicos dessa conta, se existirem)
        # em vez do `config` único e global usado até aqui -- é isso que faz um
        # teto de alocação/confiança mínima diferente por conta valer de verdade.
        entry_pause_reasons: dict = {}

        async def _collect_account_state(
            account: AccountContext,
        ) -> tuple[AccountContext, float, float, RuntimeConfig] | None:
            """Carteira + circuit breaker + revisão pré-existente de UMA conta.
            Extraído do loop sequencial original (multi-conta-plano.md, Fase C/E)
            pra rodar em PARALELO com as demais contas via asyncio.gather logo
            abaixo -- pedido explícito do Ivan em 25/09/2026 (achado: com o loop
            sequencial, uma conta lenta ou com erro atrasava a leitura das
            demais antes mesmo da avaliação de oportunidades começar, ver
            multi-conta-plano.md 10.12). Cada chamada usa seu próprio
            BinanceClient (credencial da própria conta) e sua própria sessão de
            banco (get_session() cria uma Session nova a cada chamada, nunca
            compartilhada -- ver db/session.py) -- seguro rodar concorrente.
            Devolve None se a leitura de carteira falhar (conta pulada neste
            ciclo, comportamento idêntico ao loop sequencial de antes)."""
            account_config = load_runtime_config(account_id=account.id)
            vlog.step("💼", "PortfolioAgent", f"[{account.label}] lendo saldo/posições na Binance...")
            portfolio_agent = PortfolioAgent(binance=account.binance, account_id=account.id)
            try:
                wallet_snapshots = await asyncio.to_thread(portfolio_agent.run)
            except Exception as exc:
                # Alerta dedicado (e-mail/push, com IP atual) se for erro de
                # autenticação -2015/-2014/-2008 -- ver core/connection_alert.py.
                await asyncio.to_thread(connection_alert.report_failure, account.label, exc)
                logger.exception("[%s] Falha ao ler carteira -- conta pulada neste ciclo.", account.label)
                vlog.fail(f"[{account.label}] Falha ao ler carteira — conta pulada neste ciclo.")
                redis_bridge.publish_event(
                    "alerts", {"type": "cycle_error", "message": f"[{account.label}] Falha ao ler carteira"}
                )
                return None

            await asyncio.to_thread(connection_alert.report_success, account.label)
            total_equity = PortfolioAgent.total_equity_usdt(wallet_snapshots)
            # Saldo LIVRE na stablecoin de segurança (não o patrimônio total) --
            # necessário porque max_allocation_pct_per_trade sugere um valor em %
            # do patrimônio TOTAL, mas a maior parte dele pode estar em outros
            # ativos (BTC, ETH etc), não em USDT disponível pra comprar algo novo.
            # Achado em 16/09/2026 (BinanceAPIException -2010 "insufficient
            # balance" na primeira ordem real, ver arquitetura-tecnica.md 9.13).
            available_stablecoin = next(
                (s.value_usdt for s in wallet_snapshots if s.asset == account_config.safety_stablecoin), 0.0
            )
            vlog.money(
                f"[{account.label}] Patrimônio: ${total_equity:,.2f} USDT "
                f"(livre em {account_config.safety_stablecoin}: ${available_stablecoin:,.2f})"
            )

            day_pnl_pct = None
            try:
                day_pnl_pct = _check_circuit_breaker(total_equity, account_config.daily_loss_alert_pct, account)
            except Exception:
                logger.exception("[%s] Falha na checagem do circuit breaker — ciclo segue.", account.label)
                vlog.fail(f"[{account.label}] Falha na checagem do circuit breaker — ciclo segue mesmo assim.")

            # Perfil final (29/09/2026): pausa de NOVAS entradas por perda do dia
            # ou queda do pico -- nunca mexe na gestão de posições abertas.
            # Falha ao calcular = pausa por segurança neste ciclo.
            if strategy_profile.is_final():
                try:
                    reason = await asyncio.to_thread(_final_entry_pause, account, total_equity, day_pnl_pct)
                except Exception:
                    logger.exception("[%s] Falha calculando a pausa do perfil final -- sem entradas neste ciclo.", account.label)
                    reason = "falha ao calcular a pausa (segurança)"
                if reason:
                    entry_pause_reasons[account.id] = reason

            # Revisão de posições PRÉ-EXISTENTES na carteira (compradas manualmente
            # antes do bot existir, ou há muito tempo) -- roda independente de haver
            # oportunidade de ENTRADA nova ou de estar numa janela de risco macro
            # (essas restrições são sobre abrir posição nova, não sobre reavaliar o
            # que já existe). Ver arquitetura-tecnica.md 9.8.
            try:
                await asyncio.to_thread(_review_wallet_positions, wallet_snapshots, account, account_config)
            except Exception:
                logger.exception("[%s] Erro inesperado na revisão de posições pré-existentes — ciclo segue.", account.label)
                vlog.fail(f"[{account.label}] Erro na revisão de posições pré-existentes — ciclo segue mesmo assim.")

            return (account, total_equity, available_stablecoin, account_config)

        # Contas processadas em PARALELO desde 25/09/2026 (pedido do Ivan, ver
        # 10.12) -- antes era um `for` sequencial: com N contas ativas, uma
        # carteira lenta ou com erro atrasava a leitura das demais antes mesmo
        # da avaliação de oportunidades (Passo 2-6) começar pra qualquer conta.
        account_results = await asyncio.gather(*(_collect_account_state(account) for account in accounts))
        account_states: list[tuple[AccountContext, float, float, RuntimeConfig]] = [
            r for r in account_results if r is not None
        ]
        total_equity_all = sum(r[1] for r in account_states)

        redis_bridge.cache_set("balance", {"total_equity_usdt": total_equity_all, "ts": now.isoformat()})
        redis_bridge.publish_event("positions", {"total_equity_usdt": total_equity_all})

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
        # Desde 25/09/2026 (pedido do Ivan, ver multi-conta-plano.md 10.12) as
        # contas avaliam oportunidades em PARALELO (asyncio.gather), não mais em
        # fila sequencial -- o `deadline` continua o MESMO instante pra todas
        # (não é dividido nem reiniciado por conta), mas agora isso significa
        # de verdade "cada conta tem até este instante pra decidir", já que
        # rodam ao mesmo tempo -- antes, rodando em fila, uma conta lenta ou
        # com falhas conseguia consumir o prazo inteiro sozinha e deixar a
        # próxima da fila sem avaliar NENHUMA oportunidade (achado real em
        # produção em 25/09/2026, ver 10.12).
        vlog.section("Passo 2-6 — Viabilidade, portfólio e comitê de risco", emoji="🧭")
        # Tudo que _evaluate_opportunities usa por conta (safety_stablecoin,
        # max_allocation_pct_per_trade, min_confidence_to_trade) já vem do
        # account_config DESSA conta, calculado no Passo 1b acima -- cada
        # chamada concorrente usa seus próprios agentes/BinanceClient/sessão de
        # banco (ver docstring de _collect_account_state acima), sem estado
        # compartilhado mutável entre contas.
        deadline = time.monotonic() + config.entry_decision_timeout_seconds

        async def _evaluate_one_account(
            account: AccountContext, total_equity: float, available_stablecoin: float, account_config: RuntimeConfig,
        ) -> None:
            """Isolamento de erro por conta preservado idêntico ao loop
            sequencial de antes (arquitetura-tecnica.md 9.13) -- só o `for`
            virou `asyncio.gather` (ver comentário da seção acima)."""
            pause = entry_pause_reasons.get(account.id)
            if pause:
                logger.warning("[%s] Perfil final: novas entradas pausadas -- %s.", account.label, pause)
                vlog.warn(f"[{account.label}] Novas entradas pausadas — {pause}.")
                return
            try:
                await _evaluate_opportunities(
                    opportunities, news_items, total_equity, available_stablecoin, cycle_id,
                    account, account_config, deadline=deadline,
                )
            except Exception:
                # Rede de segurança: qualquer erro não previsto na avaliação de
                # oportunidades (ex: erro de rede/DB no meio do loop) não pode
                # derrubar o ciclo nem impedir as OUTRAS contas de serem avaliadas.
                # Ver arquitetura-tecnica.md 9.13.
                logger.exception("[%s] Erro inesperado avaliando oportunidades.", account.label)
                vlog.fail(f"[{account.label}] Erro inesperado avaliando oportunidades — conta pulada neste ciclo.")
                redis_bridge.publish_event(
                    "alerts",
                    {"type": "cycle_error", "message": f"[{account.label}] Erro inesperado avaliando oportunidades"},
                )

        await asyncio.gather(
            *(
                _evaluate_one_account(account, total_equity, available_stablecoin, account_config)
                for account, total_equity, available_stablecoin, account_config in account_states
            )
        )

        vlog.banner("🏁  CICLO CONCLUÍDO", f"ciclo {cycle_id[:8]}", color="green")

    finally:
        redis_bridge.release_cycle_lock()
        cycle_id_var.reset(cycle_token)


# Um único "gestor" por vez: o ciclo (15 min) e o monitor rápido (60s) chamam a mesma
# gestão, e dois vendendo a mesma posição ao mesmo tempo gerariam ordem duplicada.
_manage_lock = asyncio.Lock()
_last_management_error: dict[str, tuple[str, float]] = {}

# Watchdog de lock travado (21/09/2026, ver arquitetura-tecnica.md 9.20/9.21, item
# crítico #4): sem timeout na Binance (corrigido em core/binance_client.py), uma
# chamada HTTP travada podia prender `_manage_lock` indefinidamente -- e como o
# monitor rápido de 60s só vê "lock ocupado" e desiste, isso silenciosamente parava
# TODA checagem de stop/take das posições reais abertas, sem alerta nenhum. Com o
# timeout novo isso não deveria mais acontecer, mas o watchdog fica como rede de
# segurança pra qualquer outra causa de travamento.
_manage_lock_acquired_at: float | None = None
_LOCK_STALL_ALERT_SECONDS = 600  # 10 min -- bem mais que qualquer gestão de posições legítima leva
_last_lock_stall_alert = 0.0


async def _safe_manage_open_positions(quiet: bool = False) -> None:
    """Gestão de posições abertas em thread (não bloqueia o event loop) e com
    rede de segurança: qualquer erro fora do loop por posição (ex: falha na
    query inicial) não pode impedir o ciclo de seguir/liberar o lock. Ver
    arquitetura-tecnica.md 9.14."""
    global _manage_lock_acquired_at
    async with _manage_lock:
        _manage_lock_acquired_at = time.monotonic()
        try:
            await asyncio.to_thread(_manage_open_positions, quiet)
        except Exception:
            logger.exception("Erro inesperado gerenciando posições abertas.")
            vlog.fail("Erro inesperado gerenciando posições abertas — ciclo segue mesmo assim.")
            redis_bridge.publish_event(
                "alerts", {"type": "cycle_error", "message": "Erro inesperado gerenciando posições abertas"}
            )
        finally:
            _manage_lock_acquired_at = None


def _check_lock_stall() -> None:
    """Alerta (no máx. 1x a cada _LOCK_STALL_ALERT_SECONDS) se `_manage_lock` está
    preso há mais tempo do que qualquer gestão de posições legítima deveria levar --
    sintoma de uma chamada de rede travada."""
    global _last_lock_stall_alert
    acquired_at = _manage_lock_acquired_at
    if acquired_at is None:
        return
    stalled_for = time.monotonic() - acquired_at
    if stalled_for < _LOCK_STALL_ALERT_SECONDS:
        return
    now_mono = time.monotonic()
    if now_mono - _last_lock_stall_alert < _LOCK_STALL_ALERT_SECONDS:
        return
    _last_lock_stall_alert = now_mono
    msg = (
        f"_manage_lock preso há {stalled_for:.0f}s -- gestão de posições (stop/take/trailing) pode "
        "estar travada numa chamada de rede. Posições reais abertas podem estar sem proteção ativa. "
        "Considere reiniciar o bot."
    )
    logger.critical(msg)
    vlog.fail(msg)
    alert("Gestão de posições travada", msg)
    redis_bridge.publish_event("alerts", {"type": "position_management_stalled", "message": msg})


async def monitor_open_positions() -> None:
    """Monitor rápido (a cada `risk_monitor_seconds`, padrão 60s), entre os ciclos de
    15 min: só confere stop/take/trailing das posições abertas -- não coleta, não usa
    LLM. Antes o stop só era checado a cada 15 min, então uma queda rápida passava do
    stop sem reação (revisão de 19/09/2026). Se o ciclo (ou outro monitor) já está
    gerindo as posições, pula esta rodada."""
    if _manage_lock.locked():
        _check_lock_stall()
        return
    await _safe_manage_open_positions(quiet=True)


async def _evaluate_opportunities(
    opportunities: list, news_items: list[NewsItem], total_equity: float,
    available_stablecoin: float, cycle_id: str, account: AccountContext, config: RuntimeConfig,
    deadline: float | None = None,
) -> None:
    # Multi-conta (multi-conta-plano.md, Fase C): todo agente usado aqui roda com o
    # BinanceClient, dry_run e limiares DESSA conta -- uma oportunidade pode ser
    # aprovada pra uma conta e reprovada (ou executada com números diferentes) pra
    # outra, cada uma com seu próprio saldo/posições.
    # account_id abaixo (Fase E, ver 10.10) é só pra etiquetar o custo de LLM
    # de cada chamada em api_cost_log -- nao muda nenhum comportamento de
    # decisao, so alimenta o painel de custo por conta.
    viability_agent = ViabilityAgent(account_id=account.id)
    portfolio_agent = PortfolioComparisonAgent(
        binance=account.binance, safety_stablecoin=config.safety_stablecoin,
        max_allocation_pct_per_trade=config.max_allocation_pct_per_trade,
    )
    risk_committee = RiskCommitteeAgent(
        min_confidence_to_trade=config.min_confidence_to_trade, account_id=account.id,
    )
    final_profile = strategy_profile.is_final()
    execution_agent = ExecutionAgent(
        binance=account.binance, dry_run=account.dry_run,
        safety_stablecoin=config.safety_stablecoin, account_id=account.id,
        final_profile=final_profile,
    )

    with get_session() as session:
        open_positions = session.query(Position).filter_by(status="open", account_id=account.id).all()

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

        vlog.step("🔎", "Oportunidade", f"[{account.label}] {opp.pair} ({opp.strategy}, regime={opp.regime})")
        # Travas anti-recompra (28/09/2026): checadas ANTES de qualquer chamada
        # de LLM, pra não gastar custo avaliando uma moeda que não pode entrar.
        try:
            block_reason = await asyncio.to_thread(_pair_entry_block_reason, opp.pair, account.id)
        except Exception:
            logger.warning("[%s] Falha checando travas anti-recompra de %s -- oportunidade pulada por segurança.", account.label, opp.pair)
            continue
        if block_reason:
            vlog.step("⏸️", "Anti-recompra", f"[{account.label}] {opp.pair} pulada — {block_reason}.")
            continue
        # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 3): antes comparava
        # o ticker como substring crua dentro do título em maiúsculas, sem
        # word-boundary -- tickers curtos/coincidentes com palavras comuns em
        # inglês (ex: "ONE" casando com "one", "SOL" com "SOLUTION") geravam
        # falso positivo, contaminando os 20% de peso de notícia na confiança do
        # RiskCommitteeAgent e o sinal social do meme_coin_eligible. Agora exige
        # fronteira de palavra (\b) ao redor do ticker.
        ticker = opp.pair.replace("USDT", "")
        ticker_pattern = re.compile(rf"\b{re.escape(ticker)}\b")
        relevant_news = [n for n in news_items if ticker_pattern.search(n.title_original.upper())]
        news_avg = sum(n.sentiment_score for n in relevant_news) / len(relevant_news) if relevant_news else 0.0

        # Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 13): meme_coin_eligible
        # já existia em core/risk_rules.py mas nunca era chamada por nenhum agente.
        # Os 2 sinais de candle (volume_zscore/momentum) vêm do scanner; o 3º
        # (sentimento social) só fica pronto aqui, depois do NewsAgent -- por isso
        # o cálculo mora no orquestrador, não dentro do MarketScannerAgent.
        opp_meme_eligible = meme_coin_eligible(
            MemeCoinSignals(
                volume_zscore=opp.volume_zscore,
                social_sentiment_score=news_avg,
                price_momentum_4h_pct=opp.price_momentum_4h_pct,
                price_momentum_24h_pct=opp.price_momentum_24h_pct,
            )
        )

        # Portfólio (regras determinísticas, sem LLM) ANTES do ViabilityAgent
        # (gpt-4o): se reprovou por regra dura (saldo, minNotional, correlação) o
        # comitê veta de qualquer jeito, então a chamada de LLM seria custo puro
        # -- nos logs de 18/09/2026 eram dezenas de chamadas por ciclo (x96 ciclos/dia).
        portfolio_check = await asyncio.to_thread(
            portfolio_agent.check, opp, total_equity, open_positions, available_stablecoin, opp_meme_eligible
        )
        if not portfolio_check.approved:
            reasons_text = "; ".join(portfolio_check.reasons)
            vlog.step("🛡️", "PortfolioComparisonAgent", f"[{account.label}] reprovado — {reasons_text}")
            _record_portfolio_rejection(cycle_id, opp, portfolio_agent, risk_committee, reasons_text, account)
            redis_bridge.publish_event(
                "decisions", {"pair": opp.pair, "account": account.label, "approved": False, "confidence": 0.0}
            )
            continue
        vlog.step("🛡️", "PortfolioComparisonAgent", f"[{account.label}] aprovado — {'; '.join(portfolio_check.reasons)}")

        if final_profile:
            # Perfil final (29/09/2026, core/strategy_profile.py): a entrada é
            # decidida por REGRA (o scanner já aplicou filtro de BTC e universo,
            # o PortfolioComparisonAgent acabou de aprovar capital/correlação).
            # A IA de viabilidade roda só como SOMBRA: a opinião dela é gravada
            # pra medir depois se ajudaria, mas não aprova nem veta. O
            # RiskCommitteeAgent não é chamado -- stop vem de 2 x ATR(14) do 1h.
            viability_verdict = None
            try:
                viability_verdict = await asyncio.to_thread(viability_agent.evaluate, opp, relevant_news)
                vlog.step("🧭", "ViabilityAgent (sombra)", f"{viability_verdict.decision} "
                          f"(confiança={viability_verdict.confidence:.0%}) — só registrado, não decide")
            except Exception:
                logger.warning("[%s] ViabilityAgent (sombra) falhou em %s -- entrada segue pela regra.", account.label, opp.pair)
            try:
                current_price = account.binance.get_last_price(opp.pair)
                df_1h = account.binance.get_klines_df(opp.pair, "1h", limit=120, closed_only=True)
                stop_pct = strategy_profile.stop_pct_from_atr_1h(df_1h, current_price)
            except Exception as exc:
                logger.warning("[%s] Perfil final: sem preço/ATR de 1h pra %s (%r) -- oportunidade pulada.", account.label, opp.pair, exc)
                vlog.fail(f"[{account.label}] Sem preço/ATR de 1h pra {opp.pair} — pulando esta oportunidade.")
                continue
            final = FinalDecision(
                approve=True,
                aggregated_confidence=1.0,
                stop_loss_pct=stop_pct,
                take_profit_pct=0.0,
                use_trailing_stop=True,
                reasoning=(f"Perfil final: entrada por regra. Stop inicial {stop_pct:.2f}% (2 x ATR 1h); sem alvo; "
                           f"stop móvel após +{settings.final_trailing_activation_pct:g}% a "
                           f"{settings.final_trailing_distance_pct:g}% do topo."),
            )
        else:
            # try/except por oportunidade: uma falha pontual do LLM (parse
            # malformado -- risco mais alto quando filter_agent_provider=deepseek,
            # ver core/llm_client.py) pula só esta oportunidade, não cancela as
            # demais do ciclo (a proteção de asyncio.wait_for em volta do ciclo
            # inteiro, da correção 9.13, continua existindo, mas é bem mais
            # grossa que isto).
            try:
                viability_verdict = await asyncio.to_thread(viability_agent.evaluate, opp, relevant_news)
            except Exception:
                logger.warning("ViabilityAgent falhou ao avaliar %s -- oportunidade pulada neste ciclo.", opp.pair)
                vlog.fail(f"ViabilityAgent falhou ao avaliar {opp.pair} — pulando esta oportunidade.")
                continue
            vlog.step("🧭", "ViabilityAgent", f"{viability_verdict.decision} (confiança={viability_verdict.confidence:.0%})")

            # Aqui o portfólio já aprovou; reconcilia só se o ViabilityAgent discordar.
            if has_divergence(viability_verdict, portfolio_check):
                try:
                    viability_verdict = await asyncio.to_thread(
                        reconcile, viability_agent, opp, viability_verdict, portfolio_check, account.id
                    )
                except Exception:
                    logger.warning("Reconciliação falhou para %s -- oportunidade pulada neste ciclo.", opp.pair)
                    vlog.fail(f"Reconciliação falhou para {opp.pair} — pulando esta oportunidade.")
                    continue

            # Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 16): `atr_ref or 0.0`
            # não protegia contra NaN -- `bool(float("nan"))` é True em Python, então
            # `nan or 0.0` devolve `nan`, não 0.0. Isso alimentaria o RiskCommitteeAgent
            # (atr_pct no prompt do LLM, usado pra calibrar stop/take) com "ATR%: nan".
            # Rede de segurança aqui (core/indicators.py já levanta exceção se o ATR vier
            # NaN de score_breakout, então isso não deveria mais disparar na prática --
            # mas cobre qualquer outro caminho que ainda não tenha essa checagem).
            atr_ref_raw = opp.votes_summary.get("atr_reference", {}).get("value", {}).get("atr", 0.0)
            atr_ref = atr_ref_raw if atr_ref_raw and not math.isnan(atr_ref_raw) else 0.0

            # Preço atual -- necessário tanto pro ATR% que o RiskCommitteeAgent usa
            # pra calibrar stop/take (antes vinha sempre 0.0 e o atr_pct caía no
            # fallback de 1.0, sem refletir a volatilidade real do ativo) quanto
            # pra converter o valor sugerido em USD numa quantidade real do ativo
            # logo abaixo (bug corrigido nesta revisão -- ver arquitetura-tecnica.md
            # 9.6: o valor em dólar era mandado direto como "quantidade" pra
            # Binance, o que teria gerado uma ordem com erro de grandeza gigantesco).
            try:
                current_price = account.binance.get_last_price(opp.pair)
            except Exception:
                logger.warning("[%s] Não consegui buscar o preço atual de %s -- oportunidade pulada neste ciclo.", account.label, opp.pair)
                vlog.fail(f"[{account.label}] Não consegui buscar o preço atual de {opp.pair} — pulando esta oportunidade.")
                continue

            # try/except aqui é novo em 25/09/2026: agora que risk_committee_agent
            # também pode rodar em DeepSeek (a pedido do Ivan, ver docstring da
            # classe), uma falha de parse pontual (mais provável no modo JSON
            # manual do DeepSeek do que no Structured Outputs nativo da OpenAI)
            # não pode mais abortar a avaliação de TODAS as oportunidades
            # restantes desta conta no ciclo -- pula só esta, igual ao
            # ViabilityAgent/reconciliação logo acima.
            try:
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
            except Exception:
                logger.warning("[%s] RiskCommitteeAgent falhou ao decidir %s -- oportunidade pulada neste ciclo.", account.label, opp.pair)
                vlog.fail(f"[{account.label}] RiskCommitteeAgent falhou ao decidir {opp.pair} — pulando esta oportunidade.")
                continue

        with get_session() as session:
            opportunity_row = Opportunity(
                cycle_id=cycle_id,
                pair=opp.pair,
                strategy=opp.strategy,
                market_regime=opp.regime,
                indicators_json=opp.votes_summary,
                status="approved" if final.approve else "rejected",
                final_confidence=final.aggregated_confidence,
                account_id=account.id,
            )
            session.add(opportunity_row)
            session.flush()

            if viability_verdict is not None:
                session.add(CommitteeDecision(
                    opportunity_id=opportunity_row.id, agent_name=viability_agent.name,
                    decision=viability_verdict.decision, confidence=viability_verdict.confidence,
                    reasoning=("[sombra -- não decidiu] " if final_profile else "") + viability_verdict.reasoning,
                    model_used=viability_agent.model,
                ))
            session.add(CommitteeDecision(
                opportunity_id=opportunity_row.id, agent_name=portfolio_agent.name,
                decision="approve" if portfolio_check.approved else "reject",
                confidence=1.0 if portfolio_check.approved else 0.0,
                reasoning="; ".join(portfolio_check.reasons), model_used="rule-based",
            ))
            session.add(CommitteeDecision(
                opportunity_id=opportunity_row.id,
                agent_name="strategy_rules_final" if final_profile else risk_committee.name,
                decision="approve" if final.approve else "reject",
                confidence=final.aggregated_confidence, reasoning=final.reasoning,
                model_used="rule-based" if final_profile else risk_committee.model,
            ))

        redis_bridge.publish_event(
            "decisions",
            {
                "pair": opp.pair, "account": account.label,
                "approved": final.approve, "confidence": final.aggregated_confidence,
            },
        )

        if final.approve and final_profile:
            vlog.ok(f"📐 [{account.label}] Regra do perfil final: APROVADO (stop inicial {final.stop_loss_pct:.2f}%, sem alvo) — indo pra execução.")
        elif final.approve:
            vlog.ok(f"⚖️  [{account.label}] RiskCommitteeAgent: APROVADO (confiança={final.aggregated_confidence:.0%}) — indo pra execução.")
        else:
            vlog.warn(f"⚖️  [{account.label}] RiskCommitteeAgent: reprovado ({final.reasoning[:80]})")

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
                filters = account.binance.get_symbol_filters(opp.pair)
                lot_size = filters.get("LOT_SIZE", {})
                step_size = float(lot_size.get("stepSize", 0) or 0)
                min_qty = float(lot_size.get("minQty", 0) or 0)
            except Exception:
                step_size, min_qty = 0.0, 0.0

            quantity = round_step_size(raw_quantity, step_size) if step_size else raw_quantity

            if quantity <= 0 or quantity < min_qty:
                logger.warning(
                    "[%s] Quantidade calculada pra %s (%.8f) ficou abaixo do mínimo da Binance "
                    "(minQty=%.8f) depois de arredondar pro LOT_SIZE -- entrada pulada.",
                    account.label, opp.pair, quantity, min_qty,
                )
                vlog.warn(f"[{account.label}] Quantidade calculada pra {opp.pair} ficou abaixo do mínimo da Binance — entrada pulada.")
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
                tag = "SIMULADA (dry-run)" if account.dry_run else "REAL"
                vlog.entry(f"[{account.label}] {opp.pair}: {trade.quantity} @ ~${trade.price:,.4f}  [{tag}]")
                # Estado da própria rodada: o saldo livre gasto e a posição nova
                # valem pras oportunidades seguintes do MESMO ciclo, DESSA conta
                # (antes todas assumiam o saldo inicial e as compras seguintes
                # falhavam com -2010).
                available_stablecoin = max(available_stablecoin - trade.quantity * trade.price, 0.0)
                with get_session() as session:
                    open_positions = session.query(Position).filter_by(status="open", account_id=account.id).all()
            except Exception as exc:
                logger.error("[%s] Falha ao executar ordem de compra para %s: %s", account.label, opp.pair, exc)
                vlog.fail(f"[{account.label}] Falha ao executar ordem para {opp.pair}: {exc} — oportunidade pulada, ciclo continua.")
                redis_bridge.publish_event(
                    "alerts",
                    {"type": "execution_error", "account": account.label, "pair": opp.pair, "message": str(exc)},
                )
                continue


def _record_portfolio_rejection(
    cycle_id: str, opp, portfolio_agent: PortfolioComparisonAgent, risk_committee: RiskCommitteeAgent,
    reasons_text: str, account: AccountContext,
) -> None:
    """Registra uma oportunidade barrada pelas regras de portfólio, sem passar
    pelo ViabilityAgent/LLM (não há linha de viabilidade nesse caso)."""
    with get_session() as session:
        opportunity_row = Opportunity(
            cycle_id=cycle_id, pair=opp.pair, strategy=opp.strategy, market_regime=opp.regime,
            indicators_json=opp.votes_summary, status="rejected", final_confidence=0.0,
            account_id=account.id,
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


def _review_wallet_positions(wallet_snapshots: list[WalletSnapshot], account: AccountContext, config: RuntimeConfig) -> None:
    """Avalia hold/sell pra cada ativo da carteira real que o bot não
    comprou por conta própria (ver PositionReviewAgent). Ignora stablecoin,
    poeira e ativos já geridos por uma Position aberta do bot.

    Multi-conta (multi-conta-plano.md, Fase C): roda com o BinanceClient e o
    dry_run DESSA conta, e só considera Position abertas dela mesma (senão um
    ativo da carteira da CONTA MICAEL podia ser visto como "já gerenciado pelo
    bot" por causa de uma Position aberta da CONTA IVAN com o mesmo par)."""
    reviews = PositionReviewAgent(
        binance=account.binance, dry_run=account.dry_run,
        safety_stablecoin=config.safety_stablecoin, account_id=account.id,
    ).run(wallet_snapshots)
    if not reviews:
        vlog.ok(f"[{account.label}] Nenhuma posição pré-existente pra revisar neste ciclo "
                 "(tudo poeira, stablecoin, ou já gerenciado pelo bot).")
    else:
        sold = sum(1 for r in reviews if r.acted)
        vlog.ok(f"[{account.label}] {len(reviews)} posição(ões) revisada(s), {sold} venda(s) executada(s).")


def _manage_open_positions(quiet: bool = False) -> None:
    """Checa stop/take/trailing e a flag de venda manual das posições abertas.
    Sem timeout — decisão de saída pode ser mais deliberada.

    `quiet=True` (monitor rápido a cada minuto): só loga quando há saída ou erro --
    "seguindo aberta" a cada 60s encheria arquivo e tabela bot_logs de ruído.

    Multi-conta (multi-conta-plano.md, Fase C): esta função gerencia posições de
    TODAS as contas ativas numa só passada -- cada posição é resolvida pra sua
    própria conta (position.account_id) e ganha o ExecutionAgent/BinanceClient
    certo (as credenciais/dry_run dessa conta, nunca os de outra). Posição sem
    account_id (registro legado, anterior à Fase A) ou de uma conta que não
    está mais ativa cai na primeira conta ativa (mesma ordem de display_order
    do resto do bot) -- nunca fica sem gestão nenhuma."""
    accounts = load_active_accounts()
    accounts_by_id = {a.id: a for a in accounts}
    default_account = accounts[0] if accounts else None

    with get_session() as session:
        positions = session.query(Position).filter_by(status="open").all()

    if not positions:
        if not quiet:
            vlog.ok("Nenhuma posição aberta pra gerenciar.")
        return

    if default_account is None:
        logger.critical(
            "Nenhuma conta ativa encontrada -- não é possível gerenciar %d posição(ões) aberta(s).", len(positions)
        )
        vlog.fail(f"Nenhuma conta ativa — não consigo gerenciar {len(positions)} posição(ões) aberta(s).")
        alert(
            "Nenhuma conta ativa",
            f"O bot tem {len(positions)} posição(ões) aberta(s) mas nenhuma conta ativa em `accounts` -- "
            "stop/take/trailing NÃO estão sendo checados. Confira a tabela accounts e o main.py.",
        )
        return

    execution_agents: dict[uuid.UUID, ExecutionAgent] = {}

    def _agent_for(account: AccountContext) -> ExecutionAgent:
        agent = execution_agents.get(account.id)
        if agent is None:
            agent = ExecutionAgent(binance=account.binance, dry_run=account.dry_run, account_id=account.id)
            execution_agents[account.id] = agent
        return agent

    for position in positions:
        account = accounts_by_id.get(position.account_id, default_account)
        execution_agent = _agent_for(account)
        try:
            current_price = account.binance.get_last_price(position.pair)
        except Exception:
            vlog.fail(f"[{account.label}] Não consegui buscar o preço atual de {position.pair} — pulando gestão desta posição.")
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

            # Posição protegida por OCO na exchange (settings.enable_oco, ver
            # arquitetura-tecnica.md 9.22): a saída, se acontecer, já é decidida
            # ali -- não tenta vender por software enquanto a lista segue aberta
            # (o saldo está travado nas ordens; uma venda por software falharia
            # com -2010). Flag manual "immediate" é a exceção: segue pro fluxo
            # normal abaixo, que cancela o OCO antes de vender de verdade (ver
            # ExecutionAgent.sell_position).
            if position.oco_order_list_id and position.sell_flag != "immediate":
                if _reconcile_oco(execution_agent, position, tag, account.binance):
                    continue
                if not quiet:
                    vlog.step("🔒", position.pair, f"[{account.label}] protegida por OCO na exchange, aguardando.")
                continue

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
                    vlog.warn(f"[{account.label}] {position.pair}: posição sem saldo real na Binance — fechada só no banco (ver alerta).")
                else:
                    vlog.exit_(f"[{account.label}] {position.pair} @ ~${trade.price:,.4f} ({label})  [{tag}]", positive=positive)
                continue

            execution_agent.update_trailing_stop(position, current_price)
            if not quiet:
                vlog.step("👀", position.pair, f"[{account.label}] seguindo aberta @ ~${current_price:,.4f}, sem gatilho de saída.")
        except Exception as exc:
            # Alerta dedicado de perda de autenticação (tem seu próprio controle
            # de repetição: 1º aviso imediato, lembrete a cada 1h).
            connection_alert.report_failure(account.label, exc)
            # Com o monitor rodando a cada minuto, uma venda que falha repetiria o
            # mesmo erro/alerta 60x por hora: só registra de novo se a mensagem mudou
            # ou passaram 15 min desde o último aviso desta posição.
            now_mono = time.monotonic()
            last = _last_management_error.get(str(position.id))
            if last is None or last[0] != str(exc) or now_mono - last[1] > 900:
                _last_management_error[str(position.id)] = (str(exc), now_mono)
                logger.error("[%s] Falha ao gerenciar posição %s (id=%s): %s", account.label, position.pair, position.id, exc)
                vlog.fail(f"[{account.label}] Falha ao gerenciar {position.pair}: {exc} — posição mantida, seguindo pras outras.")
                redis_bridge.publish_event(
                    "alerts",
                    {"type": "position_management_error", "account": account.label, "pair": position.pair, "message": str(exc)},
                )
            continue


def _reconcile_oco(execution_agent: ExecutionAgent, position: Position, tag: str, binance: BinanceClient) -> bool:
    """Confere se a lista OCO da posição já foi resolvida na Binance. Devolve
    True se a posição foi fechada aqui (perna executada, ou lista cancelada
    externamente e revertida pra stop/take por software); False se ainda está
    aberta OU o status não pôde ser lido (falha de rede -- tratado como "ainda
    aberta", tenta de novo no próximo monitor/ciclo)."""
    try:
        status = binance.get_oco_order(position.oco_order_list_id)
    except Exception:
        logger.warning(
            "Não consegui checar o status do OCO de %s (orderListId=%s) -- tento de novo depois.",
            position.pair, position.oco_order_list_id,
        )
        return False

    if not oco_is_filled(status):
        return False

    try:
        orders = binance.get_oco_sub_orders(position.pair, position.oco_order_list_id)
    except Exception:
        logger.error(
            "OCO de %s resolvido mas não consegui buscar o detalhe das ordens -- posição mantida aberta, "
            "confira manualmente.", position.pair,
        )
        vlog.fail(f"{position.pair}: OCO resolvido mas detalhe indisponível — confira manualmente.")
        return False

    summary = summarize_oco_orders(orders)
    if summary is None:
        # Lista resolvida sem nenhuma perna executar (ex: cancelada manualmente
        # no app da Binance, fora do bot) -- volta pra stop/take por software.
        with get_session() as session:
            db_position = session.get(Position, position.id)
            if db_position is not None:
                db_position.oco_order_list_id = None
        vlog.warn(f"{position.pair}: lista OCO foi cancelada sem executar — voltando pro stop/take por software.")
        return False

    execution_agent.record_oco_exit(position, summary)
    positive = summary["reason"] == "take_profit"
    label = "🎉 take profit (OCO)" if positive else "🛑 stop loss (OCO)"
    vlog.exit_(f"{position.pair} @ ~${summary['price']:,.4f} ({label})  [{tag}]", positive=positive)
    return True


def _check_circuit_breaker(total_equity_now: float, alert_pct: float, account: AccountContext) -> float | None:
    """Alerta de perda diária baseado no P&L de MERCADO do dia (core/equity.py), não na
    diferença bruta de patrimônio: aportes e retiradas manuais não contam como perda
    (achado em 19/09/2026: alerta de "-28%" causado por US$26,6 em NEAR movidos pra fora
    da conta, sem trade nenhum). O e-mail/push sai UMA vez por dia (por CONTA -- ver
    `redis_bridge.once_per_day` abaixo); nos ciclos seguintes a situação só aparece no log.

    Multi-conta (multi-conta-plano.md, Fase C): `account` decide qual linha de
    DailyEquity (agora por (date, account_id), ver migrate_add_daily_equity_pk.py) e
    qual fatia de WalletSnapshot (core.equity.daily_market_pnl_usdt(day, account_id=...))
    usar -- cada conta tem seu próprio "início de dia" e seu próprio P&L de mercado,
    nunca misturados entre contas."""
    # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 5): dt.date.today()
    # usa o fuso do SISTEMA OPERACIONAL da máquina, não settings.timezone (o
    # mesmo valor que o AsyncIOScheduler usa desde a Fase 5, 9.25, e que
    # core/equity.py passou a usar explicitamente nesta mesma correção) -- se
    # o fuso do Windows do Ivan alguma vez divergir, o corte de "dia" do
    # circuit breaker desalinhava silenciosamente do resto do bot.
    today = dt.datetime.now(ZoneInfo(settings.timezone)).date()
    # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): equity_brl era
    # sempre gravado como 0.0 -- nenhum outro lugar do código populava essa
    # coluna. O bot roda no PC do Ivan no Brasil (não numa região dos EUA da
    # Vercel, que é o que causava o bloqueio HTTP 451 documentado em
    # arquitetura-tecnica.md 9.8 pro lado do dashboard) -- então buscar a
    # cotação USDT/BRL direto da Binance aqui deve funcionar normalmente.
    # Best-effort: se falhar, grava 0.0 como antes (nunca bloqueia o
    # circuit breaker, que é calculado em USDT).
    try:
        equity_brl = total_equity_now * binance_client.get_last_price("USDTBRL")
    except Exception:
        logger.warning("[%s] Não foi possível buscar cotação USDT/BRL -- gravando equity_brl=0.0 hoje.", account.label)
        equity_brl = 0.0
    with get_session() as session:
        row = session.query(DailyEquity).filter_by(date=today, account_id=account.id).first()
        if row is None:
            # Achado 25/09/2026 (multi-conta-plano.md, ver 10.13): se a primeira
            # leitura do dia pegar a conta com saldo quase zero (ex: bem antes de
            # um depósito, ou logo após corrigir uma credencial quebrada -- foi
            # exatamente o caso da CONTA MICAEL em 25/09: $1.88 às 21:03, $44.84
            # vinte minutos depois), esse valor virava o "início do dia" PRA O
            # RESTO DO DIA INTEIRO -- qualquer variação de mercado de poucos
            # dólares nas posições compradas DEPOIS do depósito virava uma
            # porcentagem gigante (-32% sobre uma base de $1.88, por exemplo)
            # sem nenhuma perda real proporcional acontecer (alerta real em
            # produção, achado investigando junto com o Ivan). Abaixo de
            # MIN_ORDER_VALUE_USDT ainda não estabelece baseline -- tenta de
            # novo no próximo ciclo, até pegar uma leitura que reflita o saldo
            # de verdade da conta. Uma conta que nunca passar desse piso o dia
            # inteiro simplesmente não tem circuit breaker nesse dia -- não tem
            # capital de verdade em risco pra proteger de qualquer forma.
            if total_equity_now < MIN_ORDER_VALUE_USDT:
                logger.info(
                    "[%s] Patrimônio de hoje ($%.2f) abaixo de $%.2f -- ainda não estabelece baseline do circuit "
                    "breaker, tenta de novo no próximo ciclo.", account.label, total_equity_now, MIN_ORDER_VALUE_USDT,
                )
                return None
            session.add(DailyEquity(
                date=today, equity_brl=equity_brl, equity_usdt=total_equity_now, account_id=account.id,
            ))
            return 0.0
        start_of_day_equity = row.equity_usdt

    pnl_today = daily_market_pnl_usdt(today, account_id=account.id)
    pnl_pct = pnl_today / start_of_day_equity if start_of_day_equity > 0 else 0.0

    if circuit_breaker_triggered(start_of_day_equity, start_of_day_equity + pnl_today, alert_pct):
        msg = (f"[{account.label}] Circuit breaker: variação de MERCADO hoje ${pnl_today:+,.2f} ({pnl_pct:+.1%}) sobre "
               f"${start_of_day_equity:,.2f} no início do dia (limite de alerta: {alert_pct:.0%}). "
               "Aportes/retiradas não contam. Só um aviso — o bot segue operando.")
        if redis_bridge.once_per_day(f"circuit_breaker:{account.id}:{today.isoformat()}"):
            alert(f"Circuit breaker: perda diária relevante ({account.label})", msg)
            vlog.fail(msg)
            redis_bridge.publish_event(
                "alerts", {"type": "circuit_breaker", "account": account.label, "pnl_today": pnl_today}
            )
        else:
            vlog.warn(f"[{account.label}] Circuit breaker ainda acima do limite hoje (alerta já enviado): "
                      f"variação de mercado ${pnl_today:+,.2f} ({pnl_pct:+.1%}).")
    else:
        vlog.ok(f"[{account.label}] Circuit breaker: variação de mercado hoje ${pnl_today:+,.2f} ({pnl_pct:+.1%}) — dentro do limite.")
    # Devolve a variação de mercado do dia (fração) -- usada pela pausa de
    # entradas do perfil final (core/strategy_profile.py). Só leitura; o
    # alerta acima continua exatamente como antes.
    return pnl_pct

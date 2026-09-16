"""Revisa as posições PRÉ-EXISTENTES da carteira real (ativos que já
estavam na Binance antes do bot existir, ou comprados manualmente, sem
uma Position aberta pelo bot cuidando da saída) e decide: vale a pena
continuar segurando ("hold") ou é melhor vender agora ("sell").

Diferente do ViabilityAgent (decisão de ENTRADA), este agente avalia uma
posição que JÁ EXISTE -- o custo de compra original não deve enviesar a
decisão (custo afundado), só o quadro técnico atual e a relevância/liquidez
do ativo importam.

Não avalia: stablecoins, poeira (valor abaixo de DUST_THRESHOLD_USDT -- não
compensa nem a taxa de venda) e ativos que já têm uma Position aberta
gerenciada pelo bot (essas já têm stop/take/trailing cuidando da saída em
orchestrator.cycle_runner._manage_open_positions -- avaliar de novo aqui
seria redundante e poderia conflitar).

SEGURANÇA (ver arquitetura-tecnica.md 9.6/9.8): a venda passa pela MESMA
regra de dry_run de qualquer outra saída do bot (ExecutionAgent._fill_price)
-- com dry_run=True (padrão), a venda é só simulada (Trade.is_paper=True,
nenhuma ordem sai pra Binance). Só quando settings.dry_run é desligado
manualmente (.env, nunca via dashboard) é que a venda de verdade acontece,
e mesmo assim só quando a confiança do veredito >= min_confidence_to_exit."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agents.base import BaseAgent
from agents.execution_agent import ExecutionAgent
from config.settings import settings
from core import vlog
from core.binance_client import binance_client
from core.indicators import confluence_score, market_regime, score_mean_reversion, score_trend_following
from core.risk_rules import is_stablecoin
from db.models import Position, PositionReview, WalletSnapshot
from db.session import get_session

# ~1 BRL na cotação usual -- valor pequeno o bastante pra não compensar nem a
# taxa de venda na Binance. Independente do threshold de exibição "menos de
# 1 BRL" do dashboard (web/lib/fx.ts busca a cotação ao vivo no client); aqui
# é só um corte conservador em USDT pra não gastar chamada de LLM avaliando
# poeira.
DUST_THRESHOLD_USDT = 0.20


class PositionReviewVerdict(BaseModel):
    decision: Literal["hold", "sell"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="Justificativa objetiva, 2-4 frases, em português.")


class PositionReviewAgent(BaseAgent):
    name = "position_review_agent"
    model = settings.openai_model_robust

    def _already_bot_managed(self, asset: str, open_positions: list[Position]) -> bool:
        pair = f"{asset}{settings.safety_stablecoin}"
        return any(p.pair == pair for p in open_positions)

    def _technical_snapshot(self, pair: str) -> dict | None:
        try:
            df_4h = binance_client.get_klines_df(pair, "4h", limit=120)
            df_1h = binance_client.get_klines_df(pair, "1h", limit=120)
            df_15m = binance_client.get_klines_df(pair, "15m", limit=120)
            regime = market_regime(df_4h)
            votes = score_trend_following(df_1h, df_15m) if regime == "trend" else score_mean_reversion(df_15m)
            return {
                "regime": regime,
                "confluence": confluence_score(votes),
                "votes": {v.name: {"vote": v.vote, "value": v.value} for v in votes},
            }
        except Exception:
            # Ativo sem par líquido o bastante na Binance (ex: só existe contra
            # outra stablecoin, ou listagem muito recente) -- segue sem contexto
            # técnico; o LLM decide só com o que tem (valor, quantidade, custo).
            return None

    def evaluate(self, snapshot: WalletSnapshot) -> PositionReviewVerdict:
        pair = f"{snapshot.asset}{settings.safety_stablecoin}"
        technicals = self._technical_snapshot(pair)

        cost_context = (
            f"Preço médio de compra: {snapshot.avg_buy_price}"
            if snapshot.avg_buy_price
            else "Preço médio de compra desconhecido (sem histórico de compra desse par na Binance "
            "-- provavelmente depósito/transferência, não compra direta)."
        )
        tech_context = (
            f"Regime de mercado: {technicals['regime']}, confluência técnica: {technicals['confluence']}, "
            f"indicadores: {technicals['votes']}"
            if technicals
            else "Sem dados técnicos suficientes pra este par na Binance (histórico insuficiente ou par ilíquido)."
        )

        system_prompt = (
            "Você é o agente de revisão de carteira de um comitê de trading de "
            "criptomoedas. Avalia uma posição que JÁ EXISTE na carteira (comprada "
            "manualmente antes do bot, ou há muito tempo) e decide: mantém ('hold') "
            "ou vende ('sell') agora. NÃO é uma decisão de entrada -- é sobre o que "
            "já está comprado. Ignore o preço de compra original como âncora "
            "emocional (custo afundado não deve enviesar a decisão); considere só "
            "o quadro técnico atual e a relevância/liquidez do ativo. Capital é "
            "pequeno e limitado -- prefira manter capital em ativos com tese clara "
            "a segurar posições esquecidas sem convicção. Só recomende venda com "
            "confiança alta (>=0.75) quando o quadro for claramente desfavorável "
            "ou o ativo tiver perdido relevância/liquidez; em dúvida, prefira 'hold' "
            "com confiança mais baixa a arriscar vender algo que ainda faz sentido."
        )
        user_prompt = (
            f"Ativo: {snapshot.asset}\nQuantidade: {snapshot.quantity}\n"
            f"Valor atual: ${snapshot.value_usdt:,.2f} USDT\n{cost_context}\n{tech_context}\n\n"
            "Vale a pena continuar segurando esta posição ou é melhor vender agora?"
        )

        from core.llm_client import call_structured  # import local (evita ciclo com db.session)

        return call_structured(
            agent_name=self.name,
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=PositionReviewVerdict,
        )

    def run(self, wallet_snapshots: list[WalletSnapshot]) -> list[PositionReview]:
        with get_session() as session:
            open_positions = session.query(Position).filter_by(status="open").all()

        execution_agent = ExecutionAgent()
        reviews: list[PositionReview] = []

        for snapshot in wallet_snapshots:
            if snapshot.asset == settings.safety_stablecoin or is_stablecoin(snapshot.asset):
                continue
            if snapshot.value_usdt < DUST_THRESHOLD_USDT:
                continue
            if self._already_bot_managed(snapshot.asset, open_positions):
                continue

            try:
                verdict = self.evaluate(snapshot)
            except Exception:
                vlog.fail(f"PositionReviewAgent: falha ao avaliar {snapshot.asset} -- pulando neste ciclo.")
                continue

            vlog.step(
                "🧐", "PositionReviewAgent",
                f"{snapshot.asset}: {verdict.decision} (confiança={verdict.confidence:.0%})",
            )

            acted = False
            if verdict.decision == "sell" and verdict.confidence >= settings.min_confidence_to_exit:
                try:
                    trade = execution_agent.sell_wallet_asset(
                        snapshot.asset, snapshot.quantity, reason="position_review"
                    )
                    if trade is not None:
                        acted = True
                        tag = "SIMULADA (dry-run)" if settings.dry_run else "REAL"
                        vlog.exit_(f"{snapshot.asset}: venda recomendada e executada [{tag}]", positive=False)
                    else:
                        vlog.warn(
                            f"{snapshot.asset}: venda recomendada, mas quantidade ficou abaixo do "
                            "mínimo da Binance depois do arredondamento -- mantida por segurança."
                        )
                except Exception:
                    vlog.fail(f"Falha ao tentar vender {snapshot.asset} -- posição mantida por segurança.")

            # Preço unitário no momento do veredito -- guardado pra dar pra medir,
            # mais adiante, se hold/sell teria sido a decisão certa (comparando
            # com o preço futuro do ativo). Ver arquitetura-tecnica.md 9.10.
            price_at_review = (snapshot.value_usdt / snapshot.quantity) if snapshot.quantity else None

            with get_session() as session:
                review = PositionReview(
                    asset=snapshot.asset,
                    decision=verdict.decision,
                    confidence=verdict.confidence,
                    reasoning=verdict.reasoning,
                    value_usdt=snapshot.value_usdt,
                    acted=acted,
                    is_paper=settings.dry_run,
                    price_at_review=price_at_review,
                )
                session.add(review)
                reviews.append(review)

        return reviews

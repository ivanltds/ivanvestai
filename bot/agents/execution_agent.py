"""Executa a ordem aprovada (mercado ou limit conforme liquidez),
registra a operação, gerencia trailing stop e a flag de venda manual.

SEGURANÇA (dry-run, ver arquitetura-tecnica.md 9.6): enquanto
`settings.dry_run` for True (padrão), este agente NUNCA chama
`place_market_order`/`place_limit_order` -- simula o fill pelo preço atual
(`binance_client.get_last_price`) e marca a Position/Trade resultante com
`is_paper=True`, pra nunca ficar indistinguível de uma operação com capital
real no Postgres/dashboard. Só quando `settings.dry_run=False` (mudança
manual no `.env`, nunca via comando remoto do dashboard, de propósito) é que
ordens de verdade saem daqui."""
from __future__ import annotations

import datetime as dt

from agents.base import BaseAgent
from agents.risk_committee_agent import FinalDecision
from config.settings import settings
from core.binance_client import binance_client
from core.risk_rules import round_step_size
from db.models import Position, Trade
from db.session import get_session

# Pares considerados "muito líquidos" usam ordem a mercado; o resto usa
# limit com tolerância curta (ver indicadores-estrategias.md).
HIGH_LIQUIDITY_PAIRS = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}


class ExecutionAgent(BaseAgent):
    name = "execution_agent"
    model = ""  # execução determinística

    def _order_type_for(self, pair: str) -> str:
        return "market" if pair in HIGH_LIQUIDITY_PAIRS else "limit"

    def _fill_price(self, pair: str, side: str, quantity: float, order_type: str) -> float:
        """Preço de preenchimento. Em dry-run é sempre o ticker atual
        (simulado -- nenhuma ordem é enviada). Em modo real, é o preço de
        fill reportado pela Binance, com fallback pro ticker se a resposta
        não trouxer `fills` (ex: alguns tipos de ordem limit)."""
        if settings.dry_run:
            return binance_client.get_last_price(pair)

        if order_type == "market":
            order = binance_client.place_market_order(pair, side, quantity)
        else:
            ticker_price = binance_client.get_last_price(pair)
            order = binance_client.place_limit_order(pair, side, quantity, ticker_price)

        return float(order.get("fills", [{}])[0].get("price", 0)) or binance_client.get_last_price(pair)

    def open_position(self, pair: str, quantity: float, decision: FinalDecision) -> Trade:
        order_type = self._order_type_for(pair)
        fill_price = self._fill_price(pair, "BUY", quantity, order_type)

        with get_session() as session:
            position = Position(
                pair=pair,
                quantity=quantity,
                avg_entry_price=fill_price,
                stop_price=fill_price * (1 - decision.stop_loss_pct / 100),
                take_price=fill_price * (1 + decision.take_profit_pct / 100),
                trailing_active=decision.use_trailing_stop,
                trailing_reference_price=fill_price if decision.use_trailing_stop else None,
                is_paper=settings.dry_run,
            )
            session.add(position)
            session.flush()

            trade = Trade(
                position_id=position.id,
                pair=pair,
                side="buy",
                order_type=order_type,
                quantity=quantity,
                price=fill_price,
                reason="committee",
                is_paper=settings.dry_run,
            )
            session.add(trade)

        return trade

    def sell_position(self, position: Position, reason: str, immediate: bool = True) -> Trade:
        """`immediate=True` vende a mercado agora; `immediate=False` é o
        modo 'agente otimiza o momento' — nesse caso quem chama essa função
        já é o próprio ciclo decidindo que chegou a hora de vender."""
        order_type = "market" if immediate or position.pair in HIGH_LIQUIDITY_PAIRS else "limit"
        fill_price = self._fill_price(position.pair, "SELL", position.quantity, order_type)

        with get_session() as session:
            db_position = session.get(Position, position.id)
            db_position.status = "closed"
            db_position.closed_at = dt.datetime.now(dt.timezone.utc)

            trade = Trade(
                position_id=position.id,
                pair=position.pair,
                side="sell",
                order_type=order_type,
                quantity=position.quantity,
                price=fill_price,
                reason=reason,
                is_paper=db_position.is_paper,
            )
            session.add(trade)

        return trade

    def sell_wallet_asset(self, asset: str, quantity: float, reason: str) -> Trade | None:
        """Vende um ativo da carteira REAL que não tem uma Position aberta
        pelo bot (ex: comprado manualmente antes do bot existir) -- usado
        pelo PositionReviewAgent quando decide que não vale mais a pena
        segurar. Diferente de `sell_position`, não existe uma Position pra
        fechar (Trade.position_id fica None); a MESMA regra de dry_run de
        `_fill_price` se aplica -- com dry_run=True (padrão) é só simulado.

        Arredonda pro LOT_SIZE da Binance antes de vender de verdade (mesma
        lógica usada pra entradas em orchestrator/cycle_runner.py) -- se a
        quantidade ficar zerada ou abaixo do mínimo depois do arredondamento,
        não vende (mantém a posição por segurança em vez de arriscar uma
        ordem rejeitada ou de tamanho errado)."""
        pair = f"{asset}{settings.safety_stablecoin}"
        sell_quantity = quantity

        if not settings.dry_run:
            try:
                filters = binance_client.get_symbol_filters(pair)
                step_size = float(filters.get("LOT_SIZE", {}).get("stepSize", 0) or 0)
                min_qty = float(filters.get("LOT_SIZE", {}).get("minQty", 0) or 0)
            except Exception:
                step_size, min_qty = 0.0, 0.0

            sell_quantity = round_step_size(quantity, step_size) if step_size else quantity
            if sell_quantity <= 0 or sell_quantity < min_qty:
                return None

        order_type = self._order_type_for(pair)
        fill_price = self._fill_price(pair, "SELL", sell_quantity, order_type)

        with get_session() as session:
            trade = Trade(
                position_id=None,
                pair=pair,
                side="sell",
                order_type=order_type,
                quantity=sell_quantity,
                price=fill_price,
                reason=reason,
                is_paper=settings.dry_run,
            )
            session.add(trade)

        return trade

    def update_trailing_stop(self, position: Position, current_price: float, trail_pct: float = 2.0) -> None:
        if not position.trailing_active:
            return
        if current_price > (position.trailing_reference_price or 0):
            new_stop = current_price * (1 - trail_pct / 100)
            with get_session() as session:
                db_position = session.get(Position, position.id)
                db_position.trailing_reference_price = current_price
                if new_stop > (db_position.stop_price or 0):
                    db_position.stop_price = new_stop

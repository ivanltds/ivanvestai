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

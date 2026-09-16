"""Executa a ordem aprovada (mercado ou limit conforme liquidez),
registra a operação, gerencia trailing stop e a flag de venda manual."""
from __future__ import annotations

from agents.base import BaseAgent
from agents.risk_committee_agent import FinalDecision
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

    def open_position(self, pair: str, quantity: float, decision: FinalDecision) -> Trade:
        order_type = self._order_type_for(pair)
        if order_type == "market":
            order = binance_client.place_market_order(pair, "BUY", quantity)
        else:
            ticker_price = float(binance_client._client.get_symbol_ticker(symbol=pair)["price"])  # noqa: SLF001
            order = binance_client.place_limit_order(pair, "BUY", quantity, ticker_price)

        fill_price = float(order.get("fills", [{}])[0].get("price", 0)) or float(
            binance_client._client.get_symbol_ticker(symbol=pair)["price"]  # noqa: SLF001
        )

        with get_session() as session:
            position = Position(
                pair=pair,
                quantity=quantity,
                avg_entry_price=fill_price,
                stop_price=fill_price * (1 - decision.stop_loss_pct / 100),
                take_price=fill_price * (1 + decision.take_profit_pct / 100),
                trailing_active=decision.use_trailing_stop,
                trailing_reference_price=fill_price if decision.use_trailing_stop else None,
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
            )
            session.add(trade)

        return trade

    def sell_position(self, position: Position, reason: str, immediate: bool = True) -> Trade:
        """`immediate=True` vende a mercado agora; `immediate=False` é o
        modo 'agente otimiza o momento' — nesse caso quem chama essa função
        já é o próprio ciclo decidindo que chegou a hora de vender."""
        order_type = "market" if immediate or position.pair in HIGH_LIQUIDITY_PAIRS else "limit"

        if order_type == "market":
            order = binance_client.place_market_order(position.pair, "SELL", position.quantity)
        else:
            ticker_price = float(binance_client._client.get_symbol_ticker(symbol=position.pair)["price"])  # noqa: SLF001
            order = binance_client.place_limit_order(position.pair, "SELL", position.quantity, ticker_price)

        fill_price = float(order.get("fills", [{}])[0].get("price", 0)) or float(
            binance_client._client.get_symbol_ticker(symbol=position.pair)["price"]  # noqa: SLF001
        )

        with get_session() as session:
            db_position = session.get(Position, position.id)
            db_position.status = "closed"
            import datetime as dt

            db_position.closed_at = dt.datetime.now(dt.timezone.utc)

            trade = Trade(
                position_id=position.id,
                pair=position.pair,
                side="sell",
                order_type=order_type,
                quantity=position.quantity,
                price=fill_price,
                reason=reason,
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

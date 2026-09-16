"""Lê saldo/posições da subconta Binance e registra snapshot da carteira."""
from __future__ import annotations

from agents.base import BaseAgent
from config.settings import settings
from core.binance_client import binance_client
from db.models import WalletSnapshot
from db.session import get_session


class PortfolioAgent(BaseAgent):
    name = "portfolio_agent"
    model = ""  # agente determinístico — não usa LLM

    def run(self) -> list[WalletSnapshot]:
        balances = binance_client.get_account_balances()
        snapshots: list[WalletSnapshot] = []

        with get_session() as session:
            for balance in balances:
                asset = balance["asset"]
                quantity = float(balance["free"]) + float(balance["locked"])
                if asset == settings.safety_stablecoin:
                    value_usdt = quantity
                    avg_buy_price = None
                else:
                    # Preço médio de compra viria idealmente do histórico de trades
                    # (my_trades da Binance); aqui usamos o último preço como
                    # aproximação de valor de mercado — refinar com histórico real.
                    try:
                        price = float(
                            binance_client._client.get_symbol_ticker(  # noqa: SLF001 — wrapper fino, ok no MVP
                                symbol=f"{asset}{settings.safety_stablecoin}"
                            )["price"]
                        )
                    except Exception:
                        price = 0.0
                    value_usdt = quantity * price
                    avg_buy_price = None  # TODO: calcular via get_my_trades() histórico

                snapshot = WalletSnapshot(
                    asset=asset,
                    quantity=quantity,
                    avg_buy_price=avg_buy_price,
                    value_usdt=value_usdt,
                    source="bot",
                )
                session.add(snapshot)
                snapshots.append(snapshot)

        return snapshots

    @staticmethod
    def total_equity_usdt(snapshots: list[WalletSnapshot]) -> float:
        return sum(s.value_usdt for s in snapshots)

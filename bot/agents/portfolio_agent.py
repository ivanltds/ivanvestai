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

    def _avg_buy_price(self, asset: str) -> float | None:
        """Preço médio de compra real, via histórico de execuções
        (`get_my_trades`) -- corrigido nesta revisão (era `None` sempre, ver
        arquitetura-tecnica.md 9.3/9.6). Simplificação MVP assumida: média
        ponderada só dos lados de COMPRA (não faz FIFO nem desconta o que já
        foi vendido) -- correto se a posição nunca teve venda parcial, o que
        é o caso de uma carteira pequena que nunca operou de verdade; se um
        dia houver vendas parciais no meio do histórico, isso deixa de ser
        exatamente o custo contábil restante."""
        symbol = f"{asset}{settings.safety_stablecoin}"
        try:
            trades = binance_client.get_my_trades(symbol)
        except Exception:
            return None

        buys = [t for t in trades if t.get("isBuyer")]
        total_qty = sum(float(t["qty"]) for t in buys)
        if total_qty <= 0:
            return None
        total_cost = sum(float(t["qty"]) * float(t["price"]) for t in buys)
        return total_cost / total_qty

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
                    avg_buy_price = self._avg_buy_price(asset)
                    try:
                        price = binance_client.get_last_price(f"{asset}{settings.safety_stablecoin}")
                    except Exception:
                        price = avg_buy_price or 0.0
                    value_usdt = quantity * price

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

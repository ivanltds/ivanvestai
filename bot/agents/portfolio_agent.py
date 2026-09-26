"""Lê saldo/posições da subconta Binance e registra snapshot da carteira."""
from __future__ import annotations

import uuid

from agents.base import BaseAgent
from config.settings import settings
from core import vlog
from core.binance_client import BinanceClient, binance_client
from db.models import WalletSnapshot
from db.session import get_session


class PortfolioAgent(BaseAgent):
    name = "portfolio_agent"
    model = ""  # agente determinístico — não usa LLM

    def __init__(
        self,
        *,
        binance: BinanceClient | None = None,
        safety_stablecoin: str | None = None,
        account_id: uuid.UUID | None = None,
    ) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B/C): parâmetros opcionais,
        # caem pro comportamento global de hoje se omitidos -- ver mesmo
        # comentário em execution_agent.py. `account_id` (novo) é gravado em
        # cada WalletSnapshot criado -- fica None (comportamento de hoje) se
        # não informado.
        super().__init__()
        self._binance = binance if binance is not None else binance_client
        self._safety_stablecoin = safety_stablecoin if safety_stablecoin is not None else settings.safety_stablecoin
        self._account_id = account_id

    def _avg_buy_price(self, asset: str) -> float | None:
        """Preço médio de compra real, via histórico de execuções
        (`get_my_trades`) -- corrigido nesta revisão (era `None` sempre, ver
        arquitetura-tecnica.md 9.3/9.6). Simplificação MVP assumida: média
        ponderada só dos lados de COMPRA (não faz FIFO nem desconta o que já
        foi vendido) -- correto se a posição nunca teve venda parcial, o que
        é o caso de uma carteira pequena que nunca operou de verdade; se um
        dia houver vendas parciais no meio do histórico, isso deixa de ser
        exatamente o custo contábil restante."""
        symbol = f"{asset}{self._safety_stablecoin}"
        try:
            trades = self._binance.get_my_trades(symbol)
        except Exception:
            return None

        buys = [t for t in trades if t.get("isBuyer")]
        total_qty = sum(float(t["qty"]) for t in buys)
        if total_qty <= 0:
            return None
        total_cost = sum(float(t["qty"]) * float(t["price"]) for t in buys)
        return total_cost / total_qty

    def run(self) -> list[WalletSnapshot]:
        balances = self._binance.get_account_balances()
        snapshots: list[WalletSnapshot] = []

        with get_session() as session:
            for balance in balances:
                asset = balance["asset"]
                quantity = float(balance["free"]) + float(balance["locked"])
                if asset == self._safety_stablecoin:
                    value_usdt = quantity
                    avg_buy_price = None
                else:
                    avg_buy_price = self._avg_buy_price(asset)
                    try:
                        # Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 11): nem
                        # todo ativo da carteira tem par direto contra a stablecoin --
                        # get_last_price_via_bridge tenta o par direto e, se não
                        # existir, converte via BTC/BNB/ETH antes de desistir (ver
                        # core/binance_client.py). Evita que o ativo "suma" do
                        # patrimônio total por engano.
                        price = self._binance.get_last_price_via_bridge(asset, self._safety_stablecoin)
                    except Exception as exc:
                        price = avg_buy_price or 0.0
                        vlog.warn(
                            f"PortfolioAgent: não foi possível precificar {asset} (direto nem via "
                            f"ponte BTC/BNB/ETH): {exc}. Usando avg_buy_price como fallback "
                            f"({price}); se None, contribuição pro patrimônio total ficou 0.0."
                        )
                    value_usdt = quantity * price

                snapshot = WalletSnapshot(
                    asset=asset,
                    quantity=quantity,
                    avg_buy_price=avg_buy_price,
                    value_usdt=value_usdt,
                    source="bot",
                    account_id=self._account_id,
                )
                session.add(snapshot)
                snapshots.append(snapshot)

        return snapshots

    @staticmethod
    def total_equity_usdt(snapshots: list[WalletSnapshot]) -> float:
        return sum(s.value_usdt for s in snapshots)

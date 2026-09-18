"""Funções puras (sem I/O) de apoio à execução de ordens -- separadas do
ExecutionAgent pra poderem ser testadas sem Binance/Postgres.

Achado na revisão de 18/09/2026: o ExecutionAgent gravava a Position com a
quantidade PEDIDA e o preço do primeiro fill, sem olhar o que a Binance de
fato executou (`executedQty`) nem a taxa cobrada no próprio ativo comprado.
Isso gerava posições "fantasma"/quantidade maior que o saldo real (-2010 na
venda seguinte).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Fill:
    """Resultado efetivo de uma execução (real ou simulada)."""

    price: float       # preço médio ponderado de execução
    quantity: float    # quantidade LÍQUIDA que ficou na carteira (BUY: já sem a taxa cobrada no ativo base)
    fee: float = 0.0
    fee_asset: str = ""
    executed_quantity: float = 0.0  # quantidade bruta executada (BUY: antes de descontar taxa no ativo base)


def format_quantity(quantity: float) -> str:
    """Quantidade em notação decimal simples. `str(1e-05)` vira '1e-05', que a
    Binance rejeita com -1100 (caracteres ilegais) -- então nunca mandar float cru."""
    return format(Decimal(str(quantity)), "f")


def parse_market_fill(order: dict, side: str, base_asset: str, fallback_price: float) -> Fill:
    """Extrai preço médio, quantidade líquida e taxa da resposta de
    `create_order` (type=MARKET, newOrderRespType padrão = FULL para MARKET).

    Levanta ValueError se nada foi executado -- quem chama NÃO deve registrar
    posição/trade nesse caso."""
    executed = float(order.get("executedQty") or 0)
    if executed <= 0:
        raise ValueError(f"Ordem não executada (status={order.get('status')}, executedQty={executed}).")

    fills = order.get("fills") or []
    quote_spent = float(order.get("cummulativeQuoteQty") or 0)
    if quote_spent > 0:
        price = quote_spent / executed
    elif fills:
        total_qty = sum(float(f["qty"]) for f in fills)
        price = sum(float(f["qty"]) * float(f["price"]) for f in fills) / total_qty if total_qty else fallback_price
    else:
        price = fallback_price

    # Taxa: agregada por ativo. Só a cobrada no ativo BASE reduz a quantidade
    # comprada; taxa em BNB/USDT não mexe na quantidade.
    fee_by_asset: dict[str, float] = {}
    for f in fills:
        asset = f.get("commissionAsset") or ""
        fee_by_asset[asset] = fee_by_asset.get(asset, 0.0) + float(f.get("commission") or 0)

    net_quantity = executed
    if side.upper() == "BUY":
        net_quantity = max(executed - fee_by_asset.get(base_asset, 0.0), 0.0)

    # Reporta uma taxa só (a de maior valor absoluto) -- Trade tem um único par fee/fee_asset.
    if fee_by_asset:
        fee_asset, fee = max(fee_by_asset.items(), key=lambda kv: kv[1])
    else:
        fee_asset, fee = "", 0.0

    return Fill(price=price, quantity=net_quantity, fee=fee, fee_asset=fee_asset, executed_quantity=executed)


def trailing_distance_pct(reference_price: float | None, stop_price: float | None, default_pct: float = 2.0) -> float:
    """Distância (em %) entre a referência do trailing e o stop atual -- é a
    distância que o comitê definiu na abertura. Mantê-la (em vez de um 2%
    fixo) impede o trailing de APERTAR o stop além do que foi decidido."""
    if reference_price and stop_price and 0 < stop_price < reference_price:
        return (reference_price - stop_price) / reference_price * 100
    return default_pct

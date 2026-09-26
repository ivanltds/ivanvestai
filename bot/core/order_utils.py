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


# --- OCO (stop + take na exchange) -----------------------------------------------------

def round_to_tick(price: float | Decimal, tick: float | Decimal, mode: str) -> Decimal:
    """Arredonda `price` pro múltiplo do `tickSize` (PRICE_FILTER da Binance).
    mode: 'down' | 'up' | 'nearest'."""
    from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

    p, t = Decimal(str(price)), Decimal(str(tick))
    rounding = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}[mode]
    return (p / t).to_integral_value(rounding=rounding) * t


def oco_price_levels(
    last_price: float, take_price: float, stop_price: float, tick: float, limit_gap: float = 0.002
) -> tuple[Decimal, Decimal, Decimal]:
    """(take, stop_trigger, stop_limit) arredondados pro tick, pra um OCO de VENDA.

    O stop é STOP_LOSS_LIMIT: dispara em `stop_trigger` e vende a limite em `stop_limit`
    (`limit_gap` abaixo do gatilho, pra ordem realmente executar num movimento rápido).
    Levanta ValueError se o preço atual já passou de um dos níveis (a Binance exige
    stop < último preço < take) -- nesse caso a saída fica com o stop por software."""
    take = round_to_tick(take_price, tick, "up")
    stop = round_to_tick(stop_price, tick, "down")
    limit = round_to_tick(stop * (1 - Decimal(str(limit_gap))), tick, "down")
    last = Decimal(str(last_price))
    if not (limit > 0 and limit < stop < last < take):
        raise ValueError(
            f"níveis inválidos pra OCO: limite {limit} < stop {stop} < último {last} < take {take} não vale."
        )
    return take, stop, limit


def oco_is_filled(oco_status: dict) -> bool:
    """True se a lista OCO já foi resolvida (perna executada ou lista cancelada
    por completo) -- `listOrderStatus` vem de `get_oco_order`. Enquanto for
    "EXECUTING", a lista segue ativa na exchange e não deve ser mexida."""
    return (oco_status or {}).get("listOrderStatus") == "ALL_DONE"


def summarize_oco_orders(orders: list[dict]) -> dict | None:
    """Resume o resultado de uma lista OCO já encerrada a partir das ORDENS das duas pernas
    (respostas de get_order). Devolve None se nenhuma perna executou (lista cancelada);
    senão {'reason': 'take_profit'|'stop_loss', 'quantity', 'price', 'order_id', 'time_ms'}."""
    filled = [o for o in orders if float(o.get("executedQty") or 0) > 0]
    if not filled:
        return None
    leg = max(filled, key=lambda o: float(o.get("executedQty") or 0))
    qty = float(leg["executedQty"])
    quote = float(leg.get("cummulativeQuoteQty") or 0)
    price = quote / qty if quote > 0 else float(leg.get("price") or 0)
    reason = "take_profit" if leg.get("type") in ("LIMIT_MAKER", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT", "LIMIT") else "stop_loss"
    return {
        "reason": reason, "quantity": qty, "price": price,
        "order_id": leg.get("orderId"), "time_ms": leg.get("updateTime") or leg.get("time"),
    }

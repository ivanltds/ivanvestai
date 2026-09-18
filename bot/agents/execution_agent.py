"""Executa a ordem aprovada (sempre a mercado), registra a operação,
gerencia trailing stop e a flag de venda manual.

SEGURANÇA (dry-run, ver arquitetura-tecnica.md 9.6): enquanto
`settings.dry_run` for True (padrão), este agente NUNCA chama
`place_market_order` -- simula o fill pelo preço atual
(`binance_client.get_last_price`) e marca a Position/Trade resultante com
`is_paper=True`, pra nunca ficar indistinguível de uma operação com capital
real no Postgres/dashboard. Só quando `settings.dry_run=False` (mudança
manual no `.env`, nunca via comando remoto do dashboard, de propósito) é que
ordens de verdade saem daqui.

IMPORTANTE (achado em 16/09/2026, ver arquitetura-tecnica.md 9.14): pra
ABRIR posição nova (`open_position`) e pra vender direto da carteira
(`sell_wallet_asset`), é correto usar `settings.dry_run` ATUAL -- é uma
decisão sendo tomada agora. Mas pra FECHAR uma posição já existente
(`sell_position`), o que importa é se AQUELA posição foi aberta em paper ou
não (`position.is_paper`) -- nunca o `settings.dry_run` atual. Se o Ivan
liga o bot em dry_run=True, acumula posições simuladas (nenhum ativo real
comprado), e depois desliga o dry_run, essas posições simuladas continuam
abertas e precisam continuar sendo fechadas em simulação quando baterem
stop/take -- do contrário o bot tenta vender na Binance de verdade um
ativo que nunca foi comprado de verdade, o que ou falha com -2010
(insufficient balance) ou, pior, vende um ativo real que por acaso exista
na carteira sem relação nenhuma com a posição simulada.

TIPO DE ORDEM (revisão de 18/09/2026): sempre MARKET. Antes, pares fora de
BTC/ETH/BNB/SOL usavam limit IOC no preço do ticker, que podia expirar sem
executar -- e a Position era gravada mesmo assim (posição fantasma, -2010 na
venda). Com ordens de ~US$30-40 nos pares do top N por volume, o slippage a
mercado é desprezível frente à taxa. Toda ordem real agora grava o que a
Binance de fato executou (`executedQty`, preço médio, taxa) -- ver
core/order_utils.py."""
from __future__ import annotations

import datetime as dt
import logging

from agents.base import BaseAgent
from agents.risk_committee_agent import FinalDecision
from config.settings import settings
from core.binance_client import binance_client
from core.notifier import alert
from core.order_utils import Fill, parse_market_fill, trailing_distance_pct
from core.risk_rules import round_step_size
from db.models import Position, Trade
from db.session import get_session

logger = logging.getLogger("ivanvestai.execution")

ORDER_TYPE = "market"


class ExecutionAgent(BaseAgent):
    name = "execution_agent"
    model = ""  # execução determinística

    @staticmethod
    def _base_asset(pair: str) -> str:
        return pair.removesuffix(settings.safety_stablecoin)

    def _execute(self, pair: str, side: str, quantity: float, is_paper: bool) -> Fill:
        """Executa (ou simula) uma ordem a mercado e devolve o que foi de fato
        preenchido. Em dry-run é o ticker atual (nenhuma ordem é enviada).

        `is_paper` é decidido por quem chama -- `settings.dry_run` atual pra
        uma decisão nova (abrir posição, vender direto da carteira), ou o
        `is_paper` da própria posição pra fechar uma posição já existente
        (ver docstring do módulo). Levanta ValueError se a ordem real não
        executou nada."""
        ticker_price = binance_client.get_last_price(pair)
        if is_paper:
            return Fill(price=ticker_price, quantity=quantity, executed_quantity=quantity)

        order = binance_client.place_market_order(pair, side, quantity)
        return parse_market_fill(order, side, self._base_asset(pair), ticker_price)

    def _prepare_real_sell(self, pair: str, requested: float) -> tuple[float, bool]:
        """Quantidade vendável de verdade (limitada ao saldo livre e arredondada
        pro LOT_SIZE). Devolve (quantidade, orfa): `orfa=True` quando não existe
        saldo nenhum desse ativo (posição fantasma -- nada a vender).
        Levanta RuntimeError se existe saldo mas está preso em ordem aberta."""
        asset = self._base_asset(pair)
        free, locked = binance_client.get_asset_balance(asset)
        try:
            lot = binance_client.get_symbol_filters(pair).get("LOT_SIZE", {})
            step_size = float(lot.get("stepSize", 0) or 0)
            min_qty = float(lot.get("minQty", 0) or 0)
        except Exception:
            step_size, min_qty = 0.0, 0.0

        quantity = min(requested, free)
        if step_size:
            quantity = round_step_size(quantity, step_size)
        if quantity > 0 and quantity >= min_qty:
            return quantity, False

        if free + locked < max(min_qty, 1e-12):
            return 0.0, True
        raise RuntimeError(
            f"{pair}: saldo livre {free} (preso em ordem: {locked}) insuficiente pra vender {requested} "
            f"(minQty={min_qty})."
        )

    def open_position(self, pair: str, quantity: float, decision: FinalDecision) -> Trade:
        is_paper = settings.dry_run
        fill = self._execute(pair, "BUY", quantity, is_paper)

        try:
            with get_session() as session:
                position = Position(
                    pair=pair,
                    quantity=fill.quantity,
                    avg_entry_price=fill.price,
                    stop_price=fill.price * (1 - decision.stop_loss_pct / 100),
                    take_price=fill.price * (1 + decision.take_profit_pct / 100),
                    trailing_active=decision.use_trailing_stop,
                    trailing_reference_price=fill.price if decision.use_trailing_stop else None,
                    is_paper=is_paper,
                )
                session.add(position)
                session.flush()

                trade = Trade(
                    position_id=position.id,
                    pair=pair,
                    side="buy",
                    order_type=ORDER_TYPE,
                    quantity=fill.executed_quantity or fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    fee_asset=fill.fee_asset or "BNB",
                    reason="committee",
                    is_paper=is_paper,
                )
                session.add(trade)
        except Exception:
            if not is_paper:
                self._alert_unrecorded("COMPRA", pair, fill)
            raise

        return trade

    def sell_position(self, position: Position, reason: str, immediate: bool = True) -> Trade | None:
        """Vende a mercado agora. `immediate` é mantido por compatibilidade
        (o ciclo sempre chama com True); toda saída é a mercado.

        Devolve None quando a posição era "órfã" (nenhum saldo real do ativo
        na Binance -- posição fantasma de uma execução que nunca aconteceu):
        nesse caso ela é só fechada no banco, sem Trade, e um alerta é enviado."""
        is_paper = position.is_paper  # position.is_paper (não settings.dry_run atual) -- ver docstring do módulo e 9.14.

        if is_paper:
            fill = self._execute(position.pair, "SELL", position.quantity, True)
        else:
            quantity, orphaned = self._prepare_real_sell(position.pair, position.quantity)
            if orphaned:
                self._close_orphan(position)
                return None
            fill = self._execute(position.pair, "SELL", quantity, False)

        try:
            with get_session() as session:
                db_position = session.get(Position, position.id)
                db_position.status = "closed"
                db_position.closed_at = dt.datetime.now(dt.timezone.utc)

                trade = Trade(
                    position_id=position.id,
                    pair=position.pair,
                    side="sell",
                    order_type=ORDER_TYPE,
                    quantity=fill.executed_quantity or fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    fee_asset=fill.fee_asset or "BNB",
                    reason=reason,
                    is_paper=db_position.is_paper,
                )
                session.add(trade)
        except Exception:
            if not is_paper:
                self._alert_unrecorded("VENDA", position.pair, fill)
            raise

        return trade

    def _close_orphan(self, position: Position) -> None:
        logger.error("Posição %s (%s) sem saldo real na Binance -- fechada no banco sem Trade.", position.id, position.pair)
        with get_session() as session:
            db_position = session.get(Position, position.id)
            db_position.status = "closed"
            db_position.closed_at = dt.datetime.now(dt.timezone.utc)
        alert(
            f"Posição órfã fechada: {position.pair}",
            f"A posição {position.id} ({position.pair}, qty={position.quantity}) não tinha saldo real na "
            "Binance (provável ordem que nunca executou). Foi marcada como fechada no banco, sem Trade.",
        )

    def _alert_unrecorded(self, action: str, pair: str, fill: Fill) -> None:
        """A ordem REAL saiu, mas gravar no banco falhou -- exige olho humano."""
        logger.critical("Ordem real de %s em %s EXECUTADA mas NÃO registrada no banco: %s", action, pair, fill)
        alert(
            f"URGENTE: {action} executada e não registrada ({pair})",
            f"Ordem real executada na Binance ({fill}) mas a gravação no Postgres falhou. "
            "Confira a carteira e o banco manualmente.",
        )

    def sell_wallet_asset(self, asset: str, quantity: float, reason: str) -> Trade | None:
        """Vende um ativo da carteira REAL que não tem uma Position aberta
        pelo bot (ex: comprado manualmente antes do bot existir) -- usado
        pelo PositionReviewAgent quando decide que não vale mais a pena
        segurar. Diferente de `sell_position`, não existe uma Position pra
        fechar (Trade.position_id fica None); a MESMA regra de dry_run de
        `_execute` se aplica -- com dry_run=True (padrão) é só simulado.

        Em modo real limita ao saldo livre e arredonda pro LOT_SIZE -- se a
        quantidade ficar zerada ou abaixo do mínimo, não vende (mantém a
        posição por segurança em vez de arriscar uma ordem rejeitada)."""
        pair = f"{asset}{settings.safety_stablecoin}"
        is_paper = settings.dry_run

        if is_paper:
            sell_quantity = quantity
        else:
            try:
                sell_quantity, orphaned = self._prepare_real_sell(pair, quantity)
            except RuntimeError:
                return None
            if orphaned:
                return None

        fill = self._execute(pair, "SELL", sell_quantity, is_paper)

        try:
            with get_session() as session:
                trade = Trade(
                    position_id=None,
                    pair=pair,
                    side="sell",
                    order_type=ORDER_TYPE,
                    quantity=fill.executed_quantity or fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    fee_asset=fill.fee_asset or "BNB",
                    reason=reason,
                    is_paper=is_paper,
                )
                session.add(trade)
        except Exception:
            if not is_paper:
                self._alert_unrecorded("VENDA", pair, fill)
            raise

        return trade

    def update_trailing_stop(self, position: Position, current_price: float) -> None:
        """Sobe o stop mantendo a MESMA distância definida pelo comitê na
        abertura (derivada de referência - stop) -- antes era 2% fixo, o que
        apertava o stop além do decidido na primeira alta pequena."""
        if not position.trailing_active:
            return
        if current_price > (position.trailing_reference_price or 0):
            trail_pct = trailing_distance_pct(position.trailing_reference_price, position.stop_price)
            new_stop = current_price * (1 - trail_pct / 100)
            with get_session() as session:
                db_position = session.get(Position, position.id)
                db_position.trailing_reference_price = current_price
                if new_stop > (db_position.stop_price or 0):
                    db_position.stop_price = new_stop

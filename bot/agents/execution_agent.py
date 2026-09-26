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
import uuid

from agents.base import BaseAgent
from agents.risk_committee_agent import FinalDecision
from config.settings import settings
from core.binance_client import BinanceClient, binance_client
from core.notifier import alert
from core.order_utils import Fill, oco_price_levels, parse_market_fill, trailing_distance_pct
from core.risk_rules import round_step_size
from db.models import Position, Trade
from db.session import get_session

logger = logging.getLogger("ivanvestai.execution")

ORDER_TYPE = "market"
ORDER_TYPE_OCO = "oco"  # saída resolvida pela lista OCO na exchange, não por uma ordem nova do bot


class ExecutionAgent(BaseAgent):
    name = "execution_agent"
    model = ""  # execução determinística

    def __init__(
        self,
        *,
        binance: BinanceClient | None = None,
        dry_run: bool | None = None,
        safety_stablecoin: str | None = None,
        enable_oco: bool | None = None,
        account_id: uuid.UUID | None = None,
    ) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B/C): todo parâmetro é
        # opcional e cai pro comportamento de hoje (client/config globais) se
        # omitido -- só cycle_runner.py, ao processar cada conta ativa, passa
        # os valores DESSA conta explicitamente. Lido uma vez aqui na
        # construção (não por chamada) porque cada instância de ExecutionAgent
        # já é sempre criada do zero a cada uso em cycle_runner.py -- não há
        # instância de vida longa que precisasse reler isso depois.
        # `account_id` etiqueta toda Position/Trade nova criada por esta
        # instância (open_position/sell_wallet_asset) -- None mantém o
        # comportamento antigo (sem etiqueta), usado por chamadores legados.
        super().__init__()
        self._binance = binance if binance is not None else binance_client
        self._dry_run = dry_run if dry_run is not None else settings.dry_run
        self._safety_stablecoin = safety_stablecoin if safety_stablecoin is not None else settings.safety_stablecoin
        self._enable_oco = enable_oco if enable_oco is not None else settings.enable_oco
        self._account_id = account_id

    def _base_asset(self, pair: str) -> str:
        return pair.removesuffix(self._safety_stablecoin)

    def _execute(self, pair: str, side: str, quantity: float, is_paper: bool) -> Fill:
        """Executa (ou simula) uma ordem a mercado e devolve o que foi de fato
        preenchido. Em dry-run é o ticker atual (nenhuma ordem é enviada).

        `is_paper` é decidido por quem chama -- `settings.dry_run` atual pra
        uma decisão nova (abrir posição, vender direto da carteira), ou o
        `is_paper` da própria posição pra fechar uma posição já existente
        (ver docstring do módulo). Levanta ValueError se a ordem real não
        executou nada."""
        ticker_price = self._binance.get_last_price(pair)
        if is_paper:
            return Fill(price=ticker_price, quantity=quantity, executed_quantity=quantity)

        order = self._binance.place_market_order(pair, side, quantity)
        return parse_market_fill(order, side, self._base_asset(pair), ticker_price)

    def _prepare_real_sell(self, pair: str, requested: float) -> tuple[float, bool]:
        """Quantidade vendável de verdade (limitada ao saldo livre e arredondada
        pro LOT_SIZE). Devolve (quantidade, orfa): `orfa=True` quando não existe
        saldo nenhum desse ativo (posição fantasma -- nada a vender).
        Levanta RuntimeError se existe saldo mas está preso em ordem aberta."""
        asset = self._base_asset(pair)
        free, locked = self._binance.get_asset_balance(asset)
        try:
            lot = self._binance.get_symbol_filters(pair).get("LOT_SIZE", {})
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
        is_paper = self._dry_run
        try:
            fill = self._execute(pair, "BUY", quantity, is_paper)
        except Exception:
            # Achado 21/09/2026 (arquitetura-tecnica.md 9.20/9.21, item crítico #1):
            # se a ordem executar na Binance mas a resposta HTTP falhar/expirar antes
            # de voltar pro bot, dinheiro real é gasto sem nenhuma Position/Trade
            # criada -- e sem este bloco, sem alerta nenhum avisando que a compra
            # pode ter ido pra frente mesmo com a exceção.
            if not is_paper:
                self._alert_possible_unconfirmed_buy(pair, quantity)
            raise

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
                    account_id=self._account_id,
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
                    # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): antes
                    # "or \"BNB\"" assumia BNB sempre que fill.fee_asset vinha vazio --
                    # inclusive em fills SIMULADOS (paper trade, fee=0.0, nenhuma taxa
                    # de verdade paga em nenhum ativo), carimbando "BNB" numa taxa que
                    # nunca existiu. Só usa o palpite de BNB quando há uma taxa real
                    # (fill.fee > 0) sem o ativo identificado; com fee=0 (comum em
                    # paper/dry-run), fica em branco em vez de inventar um ativo.
                    fee_asset=fill.fee_asset or ("BNB" if fill.fee else ""),
                    reason="committee",
                    is_paper=is_paper,
                    account_id=self._account_id,
                )
                session.add(trade)
        except Exception:
            if not is_paper:
                self._alert_unrecorded("COMPRA", pair, fill)
            raise

        # OCO opcional (settings.enable_oco, default False -- opt-in manual no
        # .env, ver arquitetura-tecnica.md 9.21/9.22): só pra posição REAL sem
        # trailing -- trailing por natureza precisa mover o stop, incompatível
        # com uma ordem OCO já fixada na exchange (ver comentário em
        # db/models.py:Position.oco_order_list_id). Best-effort: falha aqui
        # NUNCA derruba open_position -- a posição já está criada e protegida
        # por stop/take via software (comportamento de sempre) de qualquer jeito.
        if not is_paper and self._enable_oco and not decision.use_trailing_stop:
            self._try_place_oco(position, fill)

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
            if position.oco_order_list_id:
                # Saldo travado nas ordens da lista OCO até ela ser cancelada --
                # sem isso, a venda abaixo falharia com -2010 (insufficient balance)
                # mesmo com o ativo "disponível" na carteira.
                self._cancel_oco(position)
            quantity, orphaned = self._prepare_real_sell(position.pair, position.quantity)
            if orphaned:
                self._close_orphan(position)
                return None
            if quantity < position.quantity:
                # Achado 21/09/2026 (arquitetura-tecnica.md 9.20/9.21, item crítico #2):
                # saldo livre real era menor que a posição, mas ainda acima do mínimo
                # negociável -- vendemos só o que tinha, mas a posição abaixo é fechada
                # no banco como se tivesse vendido tudo. Alerta pra investigar o drift.
                self._alert_partial_sell(position, quantity)
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
                    # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): antes
                    # "or \"BNB\"" assumia BNB sempre que fill.fee_asset vinha vazio --
                    # inclusive em fills SIMULADOS (paper trade, fee=0.0, nenhuma taxa
                    # de verdade paga em nenhum ativo), carimbando "BNB" numa taxa que
                    # nunca existiu. Só usa o palpite de BNB quando há uma taxa real
                    # (fill.fee > 0) sem o ativo identificado; com fee=0 (comum em
                    # paper/dry-run), fica em branco em vez de inventar um ativo.
                    fee_asset=fill.fee_asset or ("BNB" if fill.fee else ""),
                    reason=reason,
                    is_paper=db_position.is_paper,
                    account_id=db_position.account_id,
                )
                session.add(trade)
        except Exception:
            if not is_paper:
                self._alert_unrecorded("VENDA", position.pair, fill)
            raise

        return trade

    def _alert_partial_sell(self, position: Position, sold_quantity: float) -> None:
        """Saldo livre real era menor que `position.quantity`, mas ainda acima do
        mínimo negociável: vendemos só o que tinha, mas a posição vai ser fechada no
        banco como se a venda tivesse sido completa. Não é necessariamente um bug do
        bot -- pode ser transferência manual, taxa paga de forma inesperada etc -- mas
        precisa de olho humano pra investigar o drift entre o rastreado e o real."""
        logger.warning(
            "Venda parcial em %s: posição tinha %.8f, só %.8f estava livre pra vender.",
            position.pair, position.quantity, sold_quantity,
        )
        alert(
            f"Venda parcial: {position.pair}",
            f"A posição {position.id} ({position.pair}) tinha {position.quantity} registrado, mas só "
            f"{sold_quantity} estava livre pra vender na Binance (resto pode estar preso em ordem aberta, "
            "ou o saldo real é menor que o rastreado por outro motivo). Vendemos o que tinha e a posição "
            "foi fechada no banco -- confira se sobrou saldo desse ativo, pra investigar o drift.",
        )

    def _try_place_oco(self, position: Position, fill: Fill) -> bool:
        """Cria a lista OCO (stop + take) na Binance pra proteger a posição recém
        aberta -- assim a saída existe mesmo se o bot cair ou o PC desligar
        (motivação original: incidente de terminal fechado, arquitetura-tecnica.md
        9.16). Best-effort/silencioso na falha: QUALQUER problema (níveis
        inválidos, minNotional, erro de API) só deixa `oco_order_list_id` NULL --
        a posição segue com stop/take por software, exatamente como sempre foi
        antes desta funcionalidade existir. Devolve True se o OCO foi criado."""
        try:
            filters = self._binance.get_symbol_filters(position.pair)
            lot = filters.get("LOT_SIZE", {})
            step_size = float(lot.get("stepSize", 0) or 0)
            quantity = round_step_size(position.quantity, step_size) if step_size else position.quantity
            if quantity <= 0:
                logger.warning("OCO pulado pra %s: quantidade após arredondamento ficou zero.", position.pair)
                return False

            tick = self._binance.get_price_tick_size(position.pair)
            if not tick:
                logger.warning("OCO pulado pra %s: tickSize não encontrado.", position.pair)
                return False

            last_price = self._binance.get_last_price(position.pair)
            take, stop, limit = oco_price_levels(
                last_price=last_price, take_price=position.take_price,
                stop_price=position.stop_price, tick=tick,
            )

            order = self._binance.place_oco_sell(position.pair, quantity, float(take), float(stop), float(limit))
            order_list_id = order.get("orderListId")
            if not order_list_id:
                logger.warning("OCO criado pra %s mas resposta sem orderListId -- não vinculado à posição.", position.pair)
                return False

            with get_session() as session:
                db_position = session.get(Position, position.id)
                if db_position is not None:
                    db_position.oco_order_list_id = order_list_id
            # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 8): antes só o
            # db_position (query nova, dentro desta função) recebia o
            # oco_order_list_id -- o objeto `position` que o chamador (open_position)
            # tem em memória ficava desatualizado, mesma classe de bug já corrigida
            # em update_trailing_stop (item 22, 9.24). Hoje é inofensivo porque
            # open_position não usa `position` depois desta chamada, mas espelha
            # aqui por segurança caso isso mude no futuro.
            position.oco_order_list_id = order_list_id
            logger.info(
                "OCO criado pra %s (orderListId=%s): take=%s stop=%s limit=%s",
                position.pair, order_list_id, take, stop, limit,
            )
            return True
        except Exception:
            logger.warning(
                "Falha ao criar OCO pra %s -- posição segue protegida só por stop/take via software "
                "(comportamento de sempre, sem essa funcionalidade).", position.pair, exc_info=True,
            )
            return False

    def _cancel_oco(self, position: Position) -> None:
        """Cancela a lista OCO ativa da posição -- necessário antes de qualquer
        venda manual/por software da mesma posição (ver docstring de sell_position).
        Best-effort: se falhar, a venda que vem em seguida pode falhar por saldo
        travado (RuntimeError já tratado em _prepare_real_sell), mas não trava o
        fluxo aqui."""
        try:
            self._binance.cancel_oco_order(position.pair, position.oco_order_list_id)
        except Exception:
            logger.warning(
                "Falha ao cancelar OCO de %s (orderListId=%s) antes da venda -- venda em seguida pode falhar.",
                position.pair, position.oco_order_list_id, exc_info=True,
            )
        with get_session() as session:
            db_position = session.get(Position, position.id)
            if db_position is not None:
                db_position.oco_order_list_id = None
        position.oco_order_list_id = None

    def record_oco_exit(self, position: Position, summary: dict) -> Trade | None:
        """Registra no banco a saída que a Binance já executou sozinha via OCO
        (uma perna disparou) -- NÃO manda nenhuma ordem nova (a Binance já
        vendeu), só espelha o resultado no Postgres, análogo a sell_position mas
        sem chamar _execute. `fee`/`fee_asset` ficam 0.0/"" aqui -- limitação
        conhecida: get_order (usado pra ler o resultado da perna executada) não
        devolve a taxa cobrada, só myTrades teria isso, e casar por orderId não
        foi implementado nesta rodada (ver arquitetura-tecnica.md 9.22)."""
        try:
            with get_session() as session:
                db_position = session.get(Position, position.id)
                db_position.status = "closed"
                db_position.closed_at = dt.datetime.now(dt.timezone.utc)
                db_position.oco_order_list_id = None

                trade = Trade(
                    position_id=position.id,
                    pair=position.pair,
                    side="sell",
                    order_type=ORDER_TYPE_OCO,
                    quantity=summary["quantity"],
                    price=summary["price"],
                    fee=0.0,
                    fee_asset="",
                    reason=summary["reason"],
                    is_paper=False,
                )
                session.add(trade)
        except Exception:
            logger.critical("Venda via OCO de %s EXECUTADA na Binance mas NÃO registrada no banco: %s", position.pair, summary)
            alert(
                f"URGENTE: venda via OCO executada e não registrada ({position.pair})",
                f"O OCO da posição {position.id} ({position.pair}) foi resolvido na Binance ({summary}) mas a "
                "gravação no Postgres falhou. Confira a carteira e o banco manualmente.",
            )
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

    def _alert_possible_unconfirmed_buy(self, pair: str, quantity: float) -> None:
        """A CHAMADA de compra falhou (exceção antes de qualquer fill confirmado) --
        mas se a falha veio de timeout/erro de rede DEPOIS que a ordem já tinha sido
        enviada pra Binance, ela pode ter executado mesmo assim, sem Position/Trade
        nenhuma registrada. Diferente de `_alert_unrecorded` (que cobre falha de
        GRAVAÇÃO pós-fill confirmado) -- este cobre a incerteza sobre o fill em si."""
        logger.critical(
            "Falha na compra real de %s (quantidade solicitada=%s) -- a ordem PODE ter "
            "executado na Binance mesmo com esta exceção. Confira o saldo manualmente.",
            pair, quantity,
        )
        alert(
            f"URGENTE: falha na compra de {pair} -- confira a carteira manualmente",
            f"Erro ao comprar {pair} (quantidade solicitada {quantity}). Se o erro veio de "
            "timeout/rede DEPOIS que a ordem foi enviada à Binance, ela pode ter executado "
            "mesmo assim, sem nenhuma Position/Trade registrada. Confira o saldo real na "
            "Binance antes de assumir que nada foi comprado.",
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
        pair = f"{asset}{self._safety_stablecoin}"
        is_paper = self._dry_run

        if is_paper:
            sell_quantity = quantity
        else:
            try:
                sell_quantity, orphaned = self._prepare_real_sell(pair, quantity)
            except RuntimeError as exc:
                # Achado 24/09/2026 (arquitetura-tecnica.md 9.20 item 23): antes
                # este caso (saldo existe mas preso em outra ordem aberta) era
                # engolido em silêncio -- devolvia None sem log nenhum, e
                # PositionReviewAgent.run() então mostrava uma mensagem genérica
                # de "abaixo do mínimo" que não era o motivo real. Agora loga o
                # motivo verdadeiro aqui, na origem, pra qualquer chamador atual
                # ou futuro ter o dado certo no log mesmo que o texto exibido ao
                # usuário continue genérico.
                logger.warning("sell_wallet_asset(%s): venda não realizada -- %s", asset, exc)
                return None
            if orphaned:
                logger.info(
                    "sell_wallet_asset(%s): nenhum saldo negociável encontrado na Binance "
                    "(posição pode já não existir de verdade, ou é poeira abaixo do mínimo).", asset,
                )
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
                    # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): antes
                    # "or \"BNB\"" assumia BNB sempre que fill.fee_asset vinha vazio --
                    # inclusive em fills SIMULADOS (paper trade, fee=0.0, nenhuma taxa
                    # de verdade paga em nenhum ativo), carimbando "BNB" numa taxa que
                    # nunca existiu. Só usa o palpite de BNB quando há uma taxa real
                    # (fill.fee > 0) sem o ativo identificado; com fee=0 (comum em
                    # paper/dry-run), fica em branco em vez de inventar um ativo.
                    fee_asset=fill.fee_asset or ("BNB" if fill.fee else ""),
                    reason=reason,
                    is_paper=is_paper,
                    account_id=self._account_id,
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
                # Achado 24/09/2026 (arquitetura-tecnica.md 9.20 item 22):
                # antes só db_position (objeto novo desta sessão) era
                # atualizado -- o objeto `position` passado pelo chamador
                # (vindo de uma query anterior, ainda em memória durante o
                # resto do ciclo) ficava com stop_price/trailing_reference_price
                # desatualizados. Espelha os mesmos valores no objeto do
                # chamador para qualquer uso posterior no mesmo ciclo (ex:
                # eventos publicados no Redis, log) refletir o stop real.
                position.trailing_reference_price = db_position.trailing_reference_price
                position.stop_price = db_position.stop_price

import math
import sys
from typing import Any, Dict, Optional
import ccxt
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings
from src.logger import setup_structured_logger, log_event

logger = setup_structured_logger(log_dir=settings.LOG_DIR)


class DCABot:
    """
    Motor de Execução do IvanvestAI - Fase 1: DCA Puro.
    Executa compras programadas de forma disciplinada, com logging estruturado
    e suporte a simulação segura (DRY_RUN).
    """

    def __init__(self) -> None:
        self.exchange_id = settings.EXCHANGE_ID
        self.symbol = settings.DCA_SYMBOL
        self.amount_fiat = settings.DCA_AMOUNT_FIAT
        self.currency = settings.DCA_CURRENCY
        self.dry_run = settings.DRY_RUN

        # Inicializa a exchange via CCXT
        exchange_class = getattr(ccxt, self.exchange_id)
        self.exchange: ccxt.Exchange = exchange_class({
            "apiKey": settings.API_KEY,
            "secret": settings.SECRET_KEY,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",  # Garante negociação estrita no mercado à vista
            },
        })

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeNotAvailable)),
        reraise=True
    )
    def fetch_market_price(self) -> float:
        """Obtém o preço atual de mercado do ativo com retry automático para resiliência."""
        ticker = self.exchange.fetch_ticker(self.symbol)
        price = ticker.get("ask") or ticker.get("last")
        if not price or price <= 0:
            raise ValueError(f"Preço inválido retornado para {self.symbol}: {price}")
        return float(price)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeNotAvailable)),
        reraise=True
    )
    def check_fiat_balance(self) -> float:
        """Verifica o saldo disponível da moeda fiduciária na carteira Spot."""
        if not settings.API_KEY or not settings.SECRET_KEY:
            # Em modo DRY_RUN sem chaves preenchidas, retorna saldo fictício para permitir teste
            return 9999.0

        balance = self.exchange.fetch_balance()
        free_balance = balance.get(self.currency, {}).get("free", 0.0)
        return float(free_balance or 0.0)

    def execute(self) -> Dict[str, Any]:
        """
        Executa um ciclo de DCA:
        1. Validação de credenciais e mercado.
        2. Checagem de saldo e preço.
        3. Cálculo de quantidade com respeito às regras de lote/precisão da Binance.
        4. Execução da ordem (ou simulação se DRY_RUN).
        5. Emissão de telemetria em logging estruturado JSON.
        """
        log_event(
            logger, "INFO", "dca_cycle_started",
            f"Iniciando ciclo de DCA para {self.symbol}",
            symbol=self.symbol,
            amount_fiat=self.amount_fiat,
            currency=self.currency,
            dry_run=self.dry_run
        )

        try:
            settings.validate()
            self.exchange.load_markets()

            if self.symbol not in self.exchange.markets:
                raise ValueError(f"Símbolo {self.symbol} não encontrado na exchange {self.exchange_id}.")

            market = self.exchange.markets[self.symbol]

            # 1. Checagem de Saldo
            available_balance = self.check_fiat_balance()
            log_event(
                logger, "INFO", "balance_checked",
                f"Saldo disponível em {self.currency}: {available_balance:.2f}",
                currency=self.currency,
                available_balance=available_balance,
                required_amount=self.amount_fiat
            )

            if available_balance < self.amount_fiat and not self.dry_run:
                error_msg = (
                    f"Saldo insuficiente em {self.currency}. "
                    f"Disponível: {available_balance:.2f}, Necessário: {self.amount_fiat:.2f}"
                )
                log_event(
                    logger, "ERROR", "insufficient_balance",
                    error_msg,
                    available_balance=available_balance,
                    required_amount=self.amount_fiat
                )
                return {"status": "failed", "reason": "insufficient_balance", "error": error_msg}

            # 2. Obter cotação atual
            current_price = self.fetch_market_price()
            log_event(
                logger, "INFO", "price_fetched",
                f"Cotação atual de {self.symbol}: {current_price}",
                symbol=self.symbol,
                price=current_price
            )

            # 3. Calcular quantidade a comprar respeitando precisão da exchange
            raw_quantity = self.amount_fiat / current_price
            formatted_quantity = float(self.exchange.amount_to_precision(self.symbol, raw_quantity))

            # Validação de valor mínimo de ordem
            min_cost = market.get("limits", {}).get("cost", {}).get("min", 0.0) or 0.0
            if self.amount_fiat < min_cost:
                raise ValueError(
                    f"O valor de aporte R${self.amount_fiat:.2f} é menor que o mínimo exigido pela exchange (R${min_cost:.2f})."
                )

            # 4. Modo Simulado (DRY_RUN)
            if self.dry_run:
                simulated_result = {
                    "status": "success_simulated",
                    "mode": "DRY_RUN",
                    "symbol": self.symbol,
                    "price": current_price,
                    "fiat_spent": self.amount_fiat,
                    "crypto_acquired": formatted_quantity,
                    "simulated_order_id": "DRY_RUN_SIMULATION_OK"
                }
                log_event(
                    logger, "INFO", "order_simulated",
                    f"[SIMULAÇÃO] Compra de {formatted_quantity} {self.symbol.split('/')[0]} por {self.amount_fiat} {self.currency} concluída com sucesso.",
                    **simulated_result
                )
                return simulated_result

            # 5. Execução Real no Mercado Spot
            log_event(
                logger, "INFO", "order_submitting",
                f"Enviando ordem a mercado para compra de {formatted_quantity} {self.symbol.split('/')[0]}...",
                symbol=self.symbol,
                quantity=formatted_quantity
            )

            # Na Binance Spot, para compras a mercado via valor fiduciário, cost_quote pode ser usado se suportado,
            # ou quantidade exata de base asset calculada.
            order = self.exchange.create_market_buy_order(self.symbol, formatted_quantity)

            order_result = {
                "status": "success",
                "order_id": order.get("id"),
                "symbol": order.get("symbol"),
                "price": order.get("average") or current_price,
                "amount": order.get("amount", formatted_quantity),
                "cost": order.get("cost", self.amount_fiat),
                "timestamp": order.get("timestamp"),
            }

            log_event(
                logger, "INFO", "order_executed",
                f"Ordem {order.get('id')} executada com sucesso!",
                **order_result
            )
            return order_result

        except Exception as e:
            log_event(
                logger, "ERROR", "dca_cycle_failed",
                f"Falha na execução do ciclo de DCA: {str(e)}",
                error=str(e)
            )
            return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    bot = DCABot()
    result = bot.execute()
    print("\n--- Resultado da Execução ---")
    print(result)

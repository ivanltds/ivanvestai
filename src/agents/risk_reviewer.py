import json
import os
from src.db.vercel_kv import kv_db

class RiskReviewerAgent:
    """
    Agente 5: Revisor de Risco (Risk Reviewer)
    Auditor final. Valida matematicamente as ordens propostas para 
    evitar bugs, alucinações da IA ou orçamentos estourados.
    Execução puramente determinística para garantir 100% de confiabilidade matemática.
    """
    def __init__(self):
        config = kv_db.get_bot_config()
        self.total_budget = float(config.get("dca_amount_brl", os.getenv("DCA_AMOUNT_FIAT", "50.00")))
        self.max_order = float(config.get("max_order_brl", 200.0))

    def review_orders(self, orders: list, max_budget: float = None) -> list:
        """
        Bloqueia ordens que fujam da lógica matemática de forma determinística.
        """
        if not orders:
            return []

        allowed_budget = max_budget if (max_budget and max_budget > 0) else self.total_budget
        print(f"[Agente 5] Auditoria determinística de risco entrando em ação (Teto permitido: {allowed_budget:.2f})...")
        
        approved_orders = []
        total_buy_amount = 0.0

        for order in orders:
            symbol = order.get("symbol", "")
            action = order.get("action", "").upper()
            fiat_amount = float(order.get("fiat_amount", 0.0))

            # Validação 1: Ação válida
            if action not in ["BUY", "SELL"]:
                print(f"[Agente 5] [REJEITADO] Ação inválida '{action}' para {symbol}.")
                continue

            # Validação 2: fiat_amount não negativo
            if fiat_amount < 0:
                print(f"[Agente 5] [REJEITADO] Valor negativo ({fiat_amount}) para {symbol}.")
                continue

            if action == "SELL":
                # Vendas não consomem orçamento de compras
                approved_orders.append(order)
            elif action == "BUY":
                # Validação 3: Teto de orçamento
                # Tolerância de 0.05 para compensar arredondamento de float
                if (total_buy_amount + fiat_amount) > (allowed_budget + 0.05):
                    print(f"[Agente 5] [ALERTA] Ordem {symbol} ({fiat_amount:.2f}) excede teto permitido ({allowed_budget:.2f}). Descartada.")
                    continue
                total_buy_amount += fiat_amount
                approved_orders.append(order)

        print(f"[Agente 5] Auditoria concluída: {len(approved_orders)}/{len(orders)} ordens aprovadas. Total alocado: {total_buy_amount:.2f}.")
        return approved_orders


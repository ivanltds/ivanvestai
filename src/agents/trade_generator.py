import os
from src.db.vercel_kv import kv_db

BINANCE_FEE_RATE = 0.002  # 0.2% por ordem (compra + venda futura = 0.4% round-trip)

class TradeGeneratorAgent:
    """
    Agente 4: Operador Quantitativo (Trade Generator)
    
    Aloca o orçamento do ciclo de forma INTELIGENTE:
    - O dca_amount_brl é o TETO máximo disponível, não um valor fixo por ativo.
    - Cada ativo recebe uma fatia proporcional à sua confiança (0-100).
    - Ativos com baixa confiança (<55%) são descartados para proteger capital.
    - A alocação é ajustada pela volatilidade: ativos mais voláteis recebem menos.
    - Qualquer parcela abaixo do mínimo configurado é eliminada (evita perda em taxas).
    """
    def __init__(self):
        config = kv_db.get_bot_config()
        self.total_budget = float(config.get("dca_amount_brl", os.getenv("DCA_AMOUNT_FIAT", "50.00")))
        self.min_order = float(config.get("min_order_brl", 8.0))
        self.max_order = float(config.get("max_order_brl", 200.0))
        self.auto_deploy_deposits = config.get("auto_deploy_deposits", True)

    def generate_orders(self, final_trades: list, current_balances: dict = None) -> list:
        if not final_trades:
            print("[Agente 4] Nenhuma moeda aprovada para operação. Operador ocioso.")
            return []

        buy_candidates = [t for t in final_trades if t.get("action", "BUY").upper() == "BUY"]
        sell_orders = [t for t in final_trades if t.get("action", "BUY").upper() == "SELL"]
        orders = []

        # Determina o orçamento efetivo do ciclo considerando saldo real de depósitos em BRL
        budget = self.total_budget
        if current_balances and isinstance(current_balances, dict):
            available_brl = float(current_balances.get("BRL", 0.0))
            if available_brl < self.min_order and available_brl >= 0:
                print(f"[Agente 4] Saldo em BRL (R${available_brl:.2f}) abaixo do mínimo da corretora (R${self.min_order:.2f}). Saldo fiduciário já está 100% direcionado para cripto/dólar.")
                # Retorna apenas ordens de venda se houver
                for trade in sell_orders:
                    orders.append({"symbol": trade["symbol"], "action": "SELL", "fiat_amount": 0})
                return orders
            elif available_brl >= self.min_order:
                if self.auto_deploy_deposits and buy_candidates:
                    max_possible = self.max_order * min(len(buy_candidates), 5)
                    budget = round(min(available_brl, max(self.total_budget, max_possible)), 2)
                else:
                    budget = round(min(self.total_budget, available_brl), 2)
                print(f"[Agente 4] Aporte em BRL detectado na Binance (R${available_brl:.2f}). Orçamento de alocação: R${budget:.2f}")

        print(f"[Agente 4] Operador calculando alocação inteligente (Orçamento disponível: R${budget:.2f})...")

        if buy_candidates:
            # 1. Filtra ativos com confiança muito baixa — não vale arriscar capital
            MIN_CONFIDENCE = 55
            viable = [t for t in buy_candidates if t.get("confidence", 0) >= MIN_CONFIDENCE]
            if not viable:
                print(f"[Agente 4] Todos os candidatos estão abaixo da confiança mínima ({MIN_CONFIDENCE}%). Ciclo conservador.")
                viable = sorted(buy_candidates, key=lambda x: x.get("confidence", 0), reverse=True)[:1]

            # 2. Limita a 5 ativos por ciclo (evita pulverização excessiva)
            viable = viable[:5]

            # 3. Calcula pesos proporcionais à confiança
            total_confidence = sum(t.get("confidence", 50) for t in viable)
            raw_allocations = {}
            for t in viable:
                weight = t.get("confidence", 50) / total_confidence
                raw_allocations[t["symbol"]] = round(budget * weight, 2)

            # 4. Aplica limites: elimina abaixo do mínimo, capa no máximo
            # e redistribui o capital liberado
            final_allocations = {}
            budget_returned = 0.0
            for symbol, amount in raw_allocations.items():
                if amount < self.min_order:
                    print(f"[Agente 4] {symbol}: alocação R${amount:.2f} abaixo do mínimo R${self.min_order:.2f} — descartado.")
                    budget_returned += amount
                elif amount > self.max_order:
                    budget_returned += (amount - self.max_order)
                    final_allocations[symbol] = self.max_order
                else:
                    final_allocations[symbol] = amount

            # 5. Redistribui capital retornado pro maior símbolo aprovado (se tiver)
            if budget_returned > 0 and final_allocations:
                top_symbol = max(final_allocations, key=final_allocations.get)
                extra = min(budget_returned, self.max_order - final_allocations[top_symbol])
                final_allocations[top_symbol] = round(final_allocations[top_symbol] + extra, 2)

            total_allocated = sum(final_allocations.values())
            print(f"[Agente 4] Alocação final: {final_allocations} | Total: R${total_allocated:.2f}")

            for symbol, amount in final_allocations.items():
                # Verificação de custo-benefício: taxa de round-trip (compra+venda) ~0.4%
                # Só vale se esperarmos lucro acima do custo de trading
                taxa_estimada = amount * BINANCE_FEE_RATE * 2
                if taxa_estimada > amount * 0.005:  # corta se taxa > 0.5% do valor
                    print(f"[Agente 4] {symbol}: taxa estimada R${taxa_estimada:.4f} inviabiliza a operação.")
                    continue
                orders.append({"symbol": symbol, "action": "BUY", "fiat_amount": amount})

        # Repassa vendas intactas
        for trade in sell_orders:
            orders.append({"symbol": trade["symbol"], "action": "SELL", "fiat_amount": 0})

        return orders

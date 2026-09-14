import os
from src.db.vercel_kv import kv_db

class TradeGeneratorAgent:
    """
    Agente 4: Operador (Trade Generator)
    Pega os trades aprovados pelo Gestor de Portfólio e desenha a matemática 
    exata das ordens (divide o orçamento). Usa matemática pura — sem IA — para
    garantir que os valores sejam sempre corretos.
    """
    def __init__(self):
        # Lê o orçamento da config do Redis (com fallback para variável de ambiente)
        config = kv_db.get_bot_config()
        self.total_budget = float(config.get("dca_amount_brl", os.getenv("DCA_AMOUNT_FIAT", "50.00")))
        self.min_order = float(config.get("min_order_brl", 8.0))

    def generate_orders(self, final_trades: list) -> list:
        """
        Divide o orçamento total igualmente entre as moedas aprovadas para BUY.
        Repassa ordens de SELL intactas.
        Usa matemática pura — sem IA — para garantia de correção.
        """
        if not final_trades:
            print("[Agente 4] Nenhuma moeda aprovada para operação. Operador ocioso.")
            return []

        print(f"[Agente 4] Operador estruturando a matemática das ordens (Orçamento Base de DCA: {self.total_budget})...")

        buy_orders = [t for t in final_trades if t.get("action", "BUY").upper() == "BUY"]
        sell_orders = [t for t in final_trades if t.get("action", "BUY").upper() == "SELL"]

        orders = []

        # Divide o orçamento igualmente entre as compras (máximo 5 moedas por ciclo)
        buy_orders = buy_orders[:5]
        if buy_orders:
            amount_per_coin = round(self.total_budget / len(buy_orders), 2)
            # Garante que cada parcela seja maior que o mínimo da Binance
            if amount_per_coin < self.min_order:
                # Se a divisão resultar em valor abaixo do mínimo, compra só a primeira da lista
                buy_orders = buy_orders[:1]
                amount_per_coin = self.total_budget

            for trade in buy_orders:
                orders.append({
                    "symbol": trade["symbol"],
                    "action": "BUY",
                    "fiat_amount": amount_per_coin
                })

        # Repassa vendas intactas
        for trade in sell_orders:
            orders.append({
                "symbol": trade["symbol"],
                "action": "SELL",
                "fiat_amount": 0
            })

        return orders

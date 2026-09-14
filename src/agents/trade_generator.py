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

        # Determina a moeda de cotação e o orçamento disponível
        quote_currency = "BRL"
        min_order_cost = self.min_order
        max_order_cost = self.max_order
        budget = self.total_budget

        if current_balances and isinstance(current_balances, dict):
            available_brl = float(current_balances.get("BRL", 0.0))
            available_usdt = float(current_balances.get("USDT", 0.0))

            if available_brl >= self.min_order:
                # Prioridade 1: gasta o saldo em Reais (BRL)
                quote_currency = "BRL"
                min_order_cost = self.min_order
                max_order_cost = self.max_order
                if self.auto_deploy_deposits and buy_candidates:
                    max_possible = self.max_order * min(len(buy_candidates), 5)
                    budget = round(min(available_brl, max(self.total_budget, max_possible)), 2)
                else:
                    budget = round(min(self.total_budget, available_brl), 2)
                print(f"[Agente 4] Aporte em BRL detectado (R${available_brl:.2f}). Comprando em BRL (Orçamento: R${budget:.2f})")

            elif available_usdt >= 5.0:
                # Prioridade 2: BRL acabou, mas há reserva em Dólar (USDT) na conta!
                # Especialmente crucial se houver diretriz humana (ex: 'Compre bitcoin')
                quote_currency = "USDT"
                min_order_cost = 5.0  # Mínimo de ordem em USDT na Binance
                max_order_cost = round(self.max_order / 5.2, 2)
                
                # Converte orçamento configurado para USDT (ex: R$50 -> ~9.62 USDT)
                budget_in_usdt = round(self.total_budget / 5.2, 2)
                if self.auto_deploy_deposits:
                    # Se auto_deploy_deposits estiver ativo, aloca a reserva disponível até o teto máximo permitido
                    budget = round(min(available_usdt, max_order_cost), 2)
                else:
                    budget = round(min(available_usdt, max(budget_in_usdt, 5.0)), 2)
                print(f"[Agente 4] BRL esgotado (R${available_brl:.2f}), mas detectada Reserva em Dólar (${available_usdt:.2f} USDT). Ativando compras via par /USDT (Orçamento: ${budget:.2f} USDT)")

            else:
                # Nem BRL nem USDT têm saldo suficiente para uma ordem mínima
                print(f"[Agente 4] Saldos disponíveis em BRL (R${available_brl:.2f}) e USDT (${available_usdt:.2f}) abaixo do mínimo da corretora. Saldo já está 100% alocado em criptoativos.")
                for trade in sell_orders:
                    orders.append({"symbol": trade["symbol"], "action": "SELL", "fiat_amount": 0})
                return orders

        print(f"[Agente 4] Operador calculando alocação inteligente (Orçamento: {budget:.2f} {quote_currency})...")

        if buy_candidates:
            # 1. Ajusta os pares dos candidatos para a moeda de cotação ativa (BRL ou USDT)
            for t in buy_candidates:
                base = t["symbol"].split('/')[0] if '/' in t["symbol"] else t["symbol"]
                # Se estiver usando USDT e o candidato for USDT, ignora
                if quote_currency == "USDT" and base == "USDT":
                    continue
                t["active_symbol"] = f"{base}/{quote_currency}"

            viable_candidates = [t for t in buy_candidates if t.get("active_symbol")]

            # Filtra por confiança mínima (55%), mas se houver diretriz humana (ou se for o top), mantém
            MIN_CONFIDENCE = 55
            viable = [t for t in viable_candidates if t.get("confidence", 0) >= MIN_CONFIDENCE]
            if not viable and viable_candidates:
                viable = sorted(viable_candidates, key=lambda x: x.get("confidence", 0), reverse=True)[:1]

            viable = viable[:5]

            if viable:
                total_confidence = sum(t.get("confidence", 50) for t in viable)
                raw_allocations = {}
                for t in viable:
                    weight = t.get("confidence", 50) / total_confidence
                    raw_allocations[t["active_symbol"]] = round(budget * weight, 2)

                final_allocations = {}
                budget_returned = 0.0
                for symbol, amount in raw_allocations.items():
                    if amount < min_order_cost:
                        print(f"[Agente 4] {symbol}: alocação {amount:.2f} {quote_currency} abaixo do mínimo {min_order_cost:.2f} — descartado.")
                        budget_returned += amount
                    elif amount > max_order_cost:
                        budget_returned += (amount - max_order_cost)
                        final_allocations[symbol] = max_order_cost
                    else:
                        final_allocations[symbol] = amount

                # Redistribui capital retornado pro maior símbolo aprovado
                if budget_returned > 0 and final_allocations:
                    top_symbol = max(final_allocations, key=final_allocations.get)
                    extra = min(budget_returned, max_order_cost - final_allocations[top_symbol])
                    final_allocations[top_symbol] = round(final_allocations[top_symbol] + extra, 2)

                total_allocated = sum(final_allocations.values())
                print(f"[Agente 4] Alocação final: {final_allocations} | Total: {total_allocated:.2f} {quote_currency}")

                for symbol, amount in final_allocations.items():
                    orders.append({"symbol": symbol, "action": "BUY", "fiat_amount": amount})

        # Repassa vendas intactas
        for trade in sell_orders:
            orders.append({"symbol": trade["symbol"], "action": "SELL", "fiat_amount": 0})

        return orders

import json
import os
from src.llm.provider import get_llm_provider

class TradeGeneratorAgent:
    """
    Agente 4: Operador (Trade Generator)
    Pega os trades aprovados pelo Gestor de Portfólio e desenha a matemática 
    exata das ordens (divide o orçamento).
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.total_budget = float(os.getenv("DCA_AMOUNT_FIAT", "50.00"))

    def generate_orders(self, final_trades: list) -> list:
        """
        Divide o orçamento total igualmente entre as moedas aprovadas para BUY.
        Repassa ordens de SELL intactas (pois a execução fará o sell total).
        """
        if not final_trades:
            print("[Agente 4] Nenhuma moeda aprovada para operação. Operador ocioso.")
            return []

        print(f"[Agente 4] Operador estruturando a matemática das ordens (Orçamento Base de DCA: {self.total_budget})...")
        
        system_prompt = f"""
        Você é um algoritmo Operador (Execution Trader).
        O Gestor aprovou as seguintes operações: {json.dumps(final_trades)}
        
        Orçamento total para compras (BUY): {self.total_budget}.
        
        Sua tarefa:
        1. Para todas as ordens de 'BUY', divida o orçamento de {self.total_budget} IGUALMENTE entre elas e defina o 'fiat_amount'.
        2. Para todas as ordens de 'SELL', apenas repasse a ordem definindo o 'fiat_amount' como 0 (a exchange liquidará tudo).
        
        Retorne estritamente um JSON neste formato:
        {{
            "orders": [
                {{
                    "symbol": "TICKER/BASE",
                    "fiat_amount": valor numérico exato a ser gasto (0 se for SELL),
                    "action": "BUY" ou "SELL"
                }}
            ]
        }}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Gere as ordens matemáticas prontas para envio à exchange.",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            return data.get("orders", [])
        except json.JSONDecodeError:
            print("[Agente 4] Erro: Falha na matemática do Operador.")
            return []

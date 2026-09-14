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
        Divide o orçamento total igualmente entre as moedas aprovadas.
        """
        if not final_trades:
            print("[Agente 4] Nenhuma moeda aprovada para compra hoje. Operador ocioso.")
            return []

        print(f"[Agente 4] Operador estruturando a matemática para {len(final_trades)} moedas com orçamento de {self.total_budget}...")
        
        system_prompt = f"""
        Você é um algoritmo Operador (Execution Trader).
        O Gestor aprovou as seguintes compras: {json.dumps(final_trades)}
        
        Você tem um orçamento total de {self.total_budget} na moeda base.
        Sua tarefa é dividir esse orçamento IGUALMENTE entre todas as moedas aprovadas.
        
        Retorne estritamente um JSON neste formato:
        {{
            "orders": [
                {{
                    "symbol": "TICKER/BASE",
                    "fiat_amount": valor numérico exato a ser gasto (duas casas decimais),
                    "action": "BUY"
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

import json
import os
from src.llm.provider import get_llm_provider

class PortfolioManagerAgent:
    """
    Agente 3: Gestor de Portfólio
    Analisa o estado atual da carteira da Binance e aplica regras rígidas de gestão de risco.
    Regra Principal: Memecoins (Alto Risco) não podem ultrapassar 5% da carteira.
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.max_memecoin_pct = float(os.getenv("MAX_MEMECOIN_ALLOCATION_PCT", "5"))

    def enforce_risk_limits(self, approved_trades: list, current_balances: dict) -> list:
        """
        Usa a IA para avaliar a carteira atual e cortar trades se quebrarem as regras de risco.
        """
        print(f"[Agente 3] Gestor avaliando limite de risco (Máximo Memecoin: {self.max_memecoin_pct}%)...")
        
        system_prompt = f"""
        Você é um Gestor de Risco (Risk Manager) rigoroso de um Hedge Fund.
        
        REGRA DE OURO: A alocação total em moedas marcadas como 'memecoin' NUNCA pode 
        ultrapassar {self.max_memecoin_pct}% do valor total da carteira.
        
        DADOS DE ENTRADA:
        1. Balanços Atuais da Carteira: {json.dumps(current_balances)}
        2. Propostas de Compra: {json.dumps(approved_trades)}
        
        Sua missão:
        Se as propostas de compra de memecoins fizerem a carteira ultrapassar o limite,
        você deve remover as memecoins da lista de compras aprovadas. As compras de moedas sólidas (não memecoins) 
        devem sempre passar, a menos que não haja saldo base suficiente.
        
        Retorne um JSON estrito:
        {{
            "final_trades": [
                {{"symbol": "TICKER/BASE", "is_memecoin": true|false}}
            ],
            "reasoning": "Sua justificativa para aprovar ou cortar trades."
        }}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Avalie a carteira e retorne os trades liberados para o Operador.",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            print(f"[Agente 3] Veredito do Gestor: {data.get('reasoning')}")
            return data.get("final_trades", [])
        except json.JSONDecodeError:
            print("[Agente 3] Erro: IA falhou na gestão de risco. Bloqueando trades por segurança.")
            return []

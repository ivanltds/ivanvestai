import json
import os
from src.llm.provider import get_llm_provider

class RiskReviewerAgent:
    """
    Agente 5: Revisor de Risco (Risk Reviewer)
    Auditor final. Valida matematicamente as ordens propostas para 
    evitar bugs, alucinações da IA ou orçamentos estourados.
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.total_budget = float(os.getenv("DCA_AMOUNT_FIAT", "50.00"))

    def review_orders(self, orders: list) -> list:
        """
        Bloqueia ordens que fujam da lógica matemática.
        """
        if not orders:
            return []

        print("[Agente 5] Auditoria final de risco entrando em ação...")
        
        system_prompt = f"""
        Você é o Auditor de Risco (Compliance) de um Hedge Fund.
        Seu papel é evitar desastres.
        
        Ordem Proposta: {json.dumps(orders)}
        Orçamento Máximo Total Permitido: {self.total_budget}
        
        Checagens Obrigatórias:
        1. Se houver ordens de 'BUY', a soma de seus 'fiat_amount' não pode ultrapassar o Orçamento Máximo ({self.total_budget}).
        2. O 'fiat_amount' nunca pode ser negativo. Se for 'SELL', deve ser exatamente 0.
        3. A ação DEVE ser 'BUY' ou 'SELL'.
        4. É perfeitamente VÁLIDO receber uma lista que contenha apenas ordens de 'SELL' (onde a soma de compras será 0).
        
        Se alguma dessas 4 regras for quebrada, você DEVE retornar uma lista de ordens vazia [].
        Se tudo estiver matematicamente perfeito, retorne as ordens exatamente como recebeu.
        
        Retorne estritamente um JSON neste formato:
        {{
            "approved_orders": [ ... ],
            "audit_log": "Texto explicando que a auditoria passou com sucesso."
        }}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Audite as ordens e aplique o carimbo final.",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            print(f"[Agente 5] Log da Auditoria: {data.get('audit_log')}")
            return data.get("approved_orders", [])
        except json.JSONDecodeError:
            print("[Agente 5] Erro na Auditoria. Bloqueando execução total por segurança.")
            return []

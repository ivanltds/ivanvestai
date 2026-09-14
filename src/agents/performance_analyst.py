import json
from src.llm.provider import get_llm_provider

class PerformanceAnalystAgent:
    """
    Agente 1.5: Analista de Performance (Aprendizado Contínuo)
    Lê o histórico recente de operações e o PnL atual para extrair 
    uma "Lição Aprendida" a ser usada pelo Gestor de Risco no ciclo atual.
    """
    def __init__(self):
        self.llm = get_llm_provider()

    def generate_lessons(self, audit_logs: list, open_positions: dict) -> str:
        """
        Gera conselhos baseados nos acertos/erros recentes.
        """
        if not audit_logs or not open_positions:
            return "Ainda não há histórico suficiente para extrair lições. Siga as regras padrão."

        print("[Agente 1.5] Analisando histórico para gerar lições aprendidas...")
        
        system_prompt = """
        Você é o Chefe de Otimização e Aprendizado de um Fundo Quantitativo.
        Seu objetivo é analisar as últimas operações e o status atual da carteira
        para deduzir o que deu certo e o que deu errado.
        
        Você deve extrair no MÁXIMO 2 lições práticas e diretas de uma linha.
        Exemplos de Saída Esperada:
        - "Na última hora compramos XRP após um falso rompimento; seja mais rigoroso com altcoins hoje."
        - "O ativo XYZ está dando prejuízo persistente; considere realizar o prejuízo se houver nova queda."
        
        Retorne APENAS o texto das lições em plain text (sem JSON). Se estiver tudo tranquilo, 
        diga: "Mantenha a estratégia atual, sem erros críticos recentes."
        """
        
        user_prompt = f"""
        Histórico Recente (Logs):
        {json.dumps(audit_logs[:10])}
        
        Posições Abertas Atuais (PnL):
        {json.dumps(open_positions)}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_json=False
        )
        
        print(f"[Agente 1.5] Lição Aprendida: {response_text.strip()}")
        return response_text.strip()

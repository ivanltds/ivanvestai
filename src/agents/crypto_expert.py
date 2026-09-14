import json
import os
from src.llm.provider import get_llm_provider

class CryptoExpertAgent:
    """
    Agente 2: Especialista Cripto
    Recebe os insights das notícias e define exatamente quais moedas (tickers)
    são válidas para compra na Binance.
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.currency = os.getenv("DCA_CURRENCY", "BRL")

    def filter_and_map_coins(self, news_insights: dict, user_directives: str = "") -> list:
        """
        Recebe o resumo de notícias e, opcionalmente, uma diretriz humana.
        Filtra QUAIS moedas devem ser operadas no momento.
        """
        print("[Agente 2] Especialista avaliando as recomendações das notícias e diretrizes humanas...")
        
        system_prompt = f"""
        Você é um Especialista de Trading de Criptomoedas operando na Binance.
        O pesquisador de notícias te enviou o seguinte insight de mercado em JSON:
        {json.dumps(news_insights)}
        
        DIRETRIZ HUMANA (OVERRIDE DE PRIORIDADE MÁXIMA):
        "{user_directives}"
        Se houver uma diretriz humana acima, ELA SOBRESCREVE TODAS AS REGRAS DAS NOTÍCIAS. Você DEVE obedecer ao que o humano pediu estritamente (por exemplo: não comprar memecoins, focar em apenas uma moeda, etc). Se estiver vazia, ignore.
        
        Sua missão:
        1. Avaliar se vale a pena comprar essas moedas listadas.
        2. Retornar um JSON apenas com as moedas aprovadas para compra no formato oficial da Binance.
        3. A moeda base para compra deve ser '{self.currency}'. Portanto, se recomendar Bitcoin, retorne 'BTC/{self.currency}'.
        
        Se o sentimento do mercado estiver 'bearish' (em queda forte) e nenhuma moeda estiver boa,
        retorne uma lista vazia. Se a notícia mencionar memecoins de alto risco, adicione uma flag 'is_memecoin': true.
        
        O JSON deve ser estritamente no formato:
        {{
            "approved_trades": [
                {{"symbol": "TICKER/BASE", "is_memecoin": true|false, "confidence": 0 a 100}}
            ]
        }}
        """
        
        print("[Agente 2] Consultando o modelo de linguagem...")
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Quais são os tickers oficiais aprovados para compra?",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            return data.get("approved_trades", [])
        except json.JSONDecodeError:
            print("[Agente 2] Erro: IA falhou ao gerar os tickers.")
            return []

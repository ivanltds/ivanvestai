import json
import os
import ccxt
from src.llm.provider import get_llm_provider
from src.config import settings

class PortfolioManagerAgent:
    """
    Agente 3: Gestor de Portfólio Avançado
    Verifica a Memória (Redis) e as cotações atuais.
    Se estiver caindo > 10%: Cruza com a Notícia para decidir Vender (Stop Loss) ou Comprar (DCA).
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.max_memecoin_pct = float(os.getenv("MAX_MEMECOIN_ALLOCATION_PCT", "5"))
        self.exchange = ccxt.binance({'enableRateLimit': True})

    def get_current_prices(self, symbols: list) -> dict:
        prices = {}
        for symbol in symbols:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                prices[symbol] = ticker['last']
            except:
                pass
        return prices

    def enforce_risk_limits(self, approved_trades: list, current_balances: dict, open_positions_memory: dict, news_insights: dict, user_directives: str = "") -> list:
        """
        Usa a IA para avaliar a carteira atual e o histórico de compras do Redis.
        """
        print(f"[Agente 3] Gestor avaliando limite de risco e checando Memória do Vercel KV...")
        
        # Buscar os preços atuais das moedas que temos em memória para calcular o PNL (Lucro/Prejuízo)
        memory_symbols = list(open_positions_memory.keys())
        current_prices = self.get_current_prices(memory_symbols)
        
        system_prompt = f"""
        Você é um Gestor de Risco (Risk Manager) rigoroso de um Hedge Fund Quantitativo.
        
        DIRETRIZ HUMANA (OVERRIDE DE PRIORIDADE MÁXIMA):
        "{user_directives}"
        Se a diretriz humana disser para vender tudo, comprar algo específico ou ignorar risco, VOCÊ DEVE OBEDECER CEGAMENTE. Ela tem precedência sobre todas as regras abaixo.
        
        REGRA 1: Memecoins nunca podem passar de {self.max_memecoin_pct}% da carteira.
        REGRA 2 (O QUEDA DE 10%): 
           Se possuímos uma moeda e o preço ATUAL for 10% menor (ou pior) que o preço MÉDIO pago, 
           você deve avaliar as Notícias Atuais.
           - Se a notícia for catastrófica (fim do projeto), envie ação de SELL para cortar perdas (Stop Loss).
           - Se a notícia for apenas variação normal ou boa, envie ação de BUY para melhorar o preço médio (DCA).
        
        DADOS DE ENTRADA:
        1. Balanços Atuais na Binance: {json.dumps(current_balances)}
        2. Propostas de Compra Novas: {json.dumps(approved_trades)}
        3. Histórico de Compras (Memória Vercel): {json.dumps(open_positions_memory)}
        4. Preços Atuais de Mercado (Binance): {json.dumps(current_prices)}
        5. Sentimento das Notícias: {json.dumps(news_insights)}
        
        Retorne um JSON estrito com os trades finais aprovados (podem ser de BUY ou SELL):
        {{
            "final_trades": [
                {{"symbol": "TICKER/BASE", "action": "BUY" ou "SELL", "is_memecoin": true|false}}
            ],
            "reasoning": "Sua justificativa para as ações."
        }}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Avalie a memória, as quedas e retorne os trades liberados para o Operador.",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            print(f"[Agente 3] Veredito do Gestor: {data.get('reasoning')}")
            return data.get("final_trades", [])
        except json.JSONDecodeError:
            print("[Agente 3] Erro: IA falhou na gestão de risco. Bloqueando operações.")
            return []

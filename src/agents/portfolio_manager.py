import json
import os
import ccxt
import pandas as pd
import pandas_ta as ta
from src.llm.provider import get_llm_provider
from src.config import settings

class PortfolioManagerAgent:
    """
    Agente 3: Gestor de Portfólio Avançado
    Verifica a Memória (Redis) e as cotações atuais.
    Calcula a Volatilidade (ATR) de cada moeda para definir um Stop Dinâmico.
    Cruza com a Notícia para decidir Vender (Stop Loss) ou Comprar (DCA).
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.max_memecoin_pct = float(os.getenv("MAX_MEMECOIN_ALLOCATION_PCT", "5"))
        self.exchange = ccxt.binance({'enableRateLimit': True})

    def get_current_prices_and_atr(self, symbols: list) -> dict:
        """Busca o preço atual e calcula a Volatilidade (ATR) para definir Stop dinâmico."""
        data = {}
        for symbol in symbols:
            try:
                # Pegar o preço atual
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Pegar velas para o ATR
                bars = self.exchange.fetch_ohlcv(symbol, timeframe='1d', limit=14)
                if len(bars) > 10:
                    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
                    latest_atr = df.iloc[-1]['atr']
                    # O "safe_stop_pct" é calculado como: (ATR / Preço Atual) * 2 (duas vezes a volatilidade diária)
                    # Limitamos entre 5% e 25% para não ficar irreal.
                    atr_pct = (latest_atr / current_price) * 100 * 2
                    safe_stop_pct = max(5.0, min(25.0, atr_pct))
                else:
                    safe_stop_pct = 10.0 # Fallback default
                
                data[symbol] = {
                    "current_price": current_price,
                    "dynamic_stop_loss_pct": round(safe_stop_pct, 2)
                }
            except Exception as e:
                print(f"[Agente 3] Erro ao buscar preço/ATR para {symbol}: {e}")
        return data

    def enforce_risk_limits(self, approved_trades: list, current_balances: dict, open_positions_memory: dict, news_insights: dict, user_directives: str = "", learned_lessons: str = "") -> list:
        """
        Usa a IA para avaliar a carteira atual e o histórico de compras do Redis.
        """
        print(f"[Agente 3] Gestor avaliando limite de risco, calculando Volatilidade Diária (ATR) e checando Memória do Vercel KV...")
        
        # Buscar preços e ATR
        memory_symbols = list(open_positions_memory.keys())
        market_data = self.get_current_prices_and_atr(memory_symbols)
        
        system_prompt = f"""
        Você é um Gestor de Risco (Risk Manager) rigoroso de um Hedge Fund Quantitativo.
        
        DIRETRIZ HUMANA (OVERRIDE DE PRIORIDADE MÁXIMA):
        "{user_directives}"
        Se a diretriz humana disser para vender tudo, comprar algo específico ou ignorar risco, VOCÊ DEVE OBEDECER CEGAMENTE. Ela tem precedência sobre todas as regras abaixo.

        LIÇÕES APRENDIDAS (EXPERIÊNCIA PASSADA):
        "{learned_lessons}"
        Siga estas lições para não repetir os mesmos erros de negociações anteriores.
        
        REGRA 1: Memecoins nunca podem passar de {self.max_memecoin_pct}% da carteira.
        REGRA 2 (STOP LOSS DINÂMICO): 
           Para cada moeda que possuímos, foi calculado matematicamente um 'dynamic_stop_loss_pct' baseado na volatilidade (ATR) da moeda.
           Se o preço ATUAL for MENOR que o preço MÉDIO pago caindo MAIS do que esse limite dinâmico de ATR, a moeda rompeu sua volatilidade normal.
           Neste caso de queda além da normalidade, avalie as Notícias Atuais:
           - Se a notícia for catastrófica (fim do projeto, hack), envie ação de SELL para cortar perdas.
           - Se a notícia for apenas variação normal e os fundamentos intactos, envie ação de BUY para melhorar o preço médio (DCA).
        REGRA 3 (DIVERSIFICAÇÃO OBRIGATÓRIA):
           O fundo deve manter no MÍNIMO 5 ativos diferentes em carteira.
           Se no "Histórico de Compras" houver menos de 5 ativos, PRIORIZE a aprovação de ordens de ativos NOVOS (que ainda não estão na carteira) a partir das "Propostas de Compra Novas".
        REGRA 4 (RISCO X RETORNO DE TAXAS):
           Lembre-se que a corretora cobra cerca de 0.2% de taxas totais na operação.
           Se a expectativa de lucro imediato (pela análise gráfica + notícias) não for claramente e folgadamente superior a essa margem de custo, REJEITE a operação.
           É preferível não operar do que perder dinheiro com taxas em mercados laterais.
        
        DADOS DE ENTRADA:
        1. Balanços Atuais na Binance: {json.dumps(current_balances)}
        2. Propostas de Compra Novas (do Agente 2): {json.dumps(approved_trades)}
        3. Histórico de Compras e Preços Médios (Memória Vercel): {json.dumps(open_positions_memory)}
        4. Mercado (Preços Atuais e Limite ATR Dinâmico): {json.dumps(market_data)}
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

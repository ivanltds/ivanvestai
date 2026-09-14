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
                
                # Pegar velas para o ATR (precisa de >14 candles para ATR-14)
                bars = self.exchange.fetch_ohlcv(symbol, timeframe='1d', limit=30)
                if len(bars) > 15:
                    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
                    latest_atr = df.iloc[-1]['atr']
                    if latest_atr is not None and not pd.isna(latest_atr) and current_price > 0:
                        # O "safe_stop_pct" é calculado como: (ATR / Preço Atual) * 2 (duas vezes a volatilidade diária)
                        # Limitamos entre 5% e 25% para não ficar irreal.
                        atr_pct = (float(latest_atr) / current_price) * 100 * 2
                        safe_stop_pct = max(5.0, min(25.0, atr_pct))
                    else:
                        safe_stop_pct = 10.0
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
        REGRA 5 (PROTEÇÃO CAMBIAL & ALOCAÇÃO ATIVA DE APORTES - BRL É APENAS TRANPORTE):
           O Real Brasileiro (BRL) é estritamente uma moeda de entrada (on-ramp) para aportes mensais do investidor. 
           Manter capital parado em moeda que desvaloriza (BRL) é inaceitável. O patrimônio deve ser mantido em CRIPTOATIVOS FORTES (BTC, ETH, etc.) ou em DÓLAR (USDT).
           - Quando houver saldo em BRL disponível vindo de depósitos/aportes (ver Balanços Atuais):
             DIRECIONE-O ATIVAMENTE para a acumulação dos ativos cripto recomendados pelo comitê.
           - Se o mercado estiver em momento de cautela ou indefinição e exigir reserva de valor, prefira converter o BRL para DÓLAR (USDT/BRL) para preservar o poder de compra.
           - Em operações de venda (Stop Loss ou Take Profit), a reserva líquida deve ser mantida preferencialmente em DÓLAR (USDT) ou reinvestida em outros ativos, NUNCA mantida como BRL ocioso.
        REGRA 6 (REBALANCEAMENTO ATIVO & ROTATIVIDADE DE MEMECOINS):
            Memecoins (PEPE, FLOKI, DOGE, SHIB, etc.) são ativos táticos de altíssimo risco e NÃO reservas de valor.
            - Se as LIÇÕES APRENDIDAS recomendarem redução ou apontarem fraqueza/estagnação (ex: "ativo PEPE não está apresentando valorização", "FLOKI estagnado"), ou se o comitê estiver buscando acumular ativos fortes (BTC/ETH):
              -> EMITA AÇÃO DE 'SELL' PARA ESSAS MEMECOINS (preferencialmente par /USDT).
              -> A rotação de capital saindo de memecoins estagnadas para Bitcoin (BTC) ou Dólar (USDT) é a prioridade do fundo.
            - Se o total de memecoins ultrapassar {self.max_memecoin_pct}% da carteira, liquide o excesso imediatamente emitindo 'SELL'.
        
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
            final_trades = data.get("final_trades", [])
        except json.JSONDecodeError:
            print("[Agente 3] Erro: IA falhou na gestão de risco. Bloqueando operações.")
            final_trades = []

        # REBALANCEAMENTO ATIVO DE MEMECOINS:
        # Se as lições aprendidas apontarem estagnação/redução de memecoins que temos em carteira,
        # ou se o usuário ativou rebalanceamento de memecoins, força ordem de SELL para rotacionar capital.
        memecoin_tickers = ["PEPE", "FLOKI", "DOGE", "SHIB", "BONK", "WIF"]
        learned_lower = learned_lessons.lower() if learned_lessons else ""
        for pos_symbol in open_positions_memory.keys():
            base = pos_symbol.split('/')[0].upper()
            if base in memecoin_tickers:
                is_flagged = any(w in learned_lower for w in ["reduzir", "vender", "estagnad", "desvaloriza", "cortar", "sair"]) and base.lower() in learned_lower
                dir_rebalance = user_directives and any(w in user_directives.lower() for w in ["rebalance", "memecoin", "venda pepe", "venda floki", "vender memecoin"])
                if is_flagged or dir_rebalance:
                    if not any(t.get("symbol", "").split("/")[0] == base and t.get("action") == "SELL" for t in final_trades):
                        print(f"[Agente 3] [REBALANCEAMENTO DE MEMECOINS] Emitindo ordem de VENDA para {base} (rotacionando para BTC/USDT).")
                        final_trades.append({
                            "symbol": f"{base}/USDT",
                            "action": "SELL",
                            "is_memecoin": True
                        })

        # Garantia absoluta: Se o humano definiu uma diretriz explícita de compra,
        # o Gestor de Portfólio não pode descartar o ativo solicitado!
        if user_directives:
            user_dir_lower = user_directives.lower()
            if any(w in user_dir_lower for w in ["compre", "comprar", "buy", "acumular", "aporte"]):
                COIN_MAP = {
                    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
                    "solana": "SOL", "sol": "SOL", "usdt": "USDT", "dolar": "USDT", "dólar": "USDT"
                }
                for name, ticker in COIN_MAP.items():
                    if name in user_dir_lower:
                        if not any(t.get("symbol", "").split("/")[0] == ticker and t.get("action") == "BUY" for t in final_trades):
                            print(f"[Agente 3] [OVERRIDE HUMANO] Forçando aprovação de compra para {ticker} por diretriz direta: '{user_directives}'")
                            final_trades.append({
                                "symbol": f"{ticker}/BRL",
                                "action": "BUY",
                                "is_memecoin": False
                            })
                        break

        return final_trades


import json
import os
import ccxt
import pandas as pd
import pandas_ta as ta
from src.llm.provider import get_llm_provider

class CryptoExpertAgent:
    """
    Agente 2: Especialista Cripto Quantitativo
    Recebe os insights das notícias, cruza com Indicadores Técnicos (TA) da Binance
    e define exatamente quais moedas são matematicamente e fundamentalmente válidas para compra.
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.currency = os.getenv("DCA_CURRENCY", "BRL")
        self.exchange = ccxt.binance({'enableRateLimit': True})

    def get_technical_indicators(self, symbol: str) -> dict:
        """Calcula RSI e Médias Móveis usando dados recentes da Binance."""
        try:
            # Puxar as últimas 100 velas de 1 hora
            bars = self.exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Calcular indicadores
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['ema20'] = ta.ema(df['close'], length=20)
            df['ema50'] = ta.ema(df['close'], length=50)
            
            latest = df.iloc[-1]
            return {
                "symbol": symbol,
                "current_price": latest['close'],
                "rsi_14": round(latest['rsi'], 2) if not pd.isna(latest['rsi']) else 50,
                "ema_20": round(latest['ema20'], 2) if not pd.isna(latest['ema20']) else latest['close'],
                "ema_50": round(latest['ema50'], 2) if not pd.isna(latest['ema50']) else latest['close'],
                "is_uptrend": bool(latest['close'] > latest['ema50']),
                "is_overbought": bool(latest['rsi'] > 70) if not pd.isna(latest['rsi']) else False,
                "is_oversold": bool(latest['rsi'] < 30) if not pd.isna(latest['rsi']) else False
            }
        except Exception as e:
            print(f"[Agente 2] ALERTA: Falha ao buscar TA para {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}

    def filter_and_map_coins(self, news_insights: dict, user_directives: str = "") -> list:
        """
        Cruza o resumo de notícias com Análise Técnica.
        Filtra QUAIS moedas devem ser operadas no momento.
        """
        print("[Agente 2] Especialista avaliando as recomendações das notícias e cruzando com Análise Técnica...")
        
        # 1. Extrair os candidatos mencionados pelas notícias
        candidates = news_insights.get("top_coins", [])
        technical_data = []
        
        # Mapa de nomes completos para tickers oficiais da Binance
        NAME_TO_TICKER = {
            "BITCOIN": "BTC", "BTC": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH", "ETH": "ETH",
            "SOLANA": "SOL", "SOL": "SOL", "CARDANO": "ADA", "ADA": "ADA", "RIPPLE": "XRP", "XRP": "XRP",
            "DOGECOIN": "DOGE", "DOGE": "DOGE", "SHIBA INU": "SHIB", "SHIBA": "SHIB", "SHIB": "SHIB",
            "POLKADOT": "DOT", "DOT": "DOT", "AVALANCHE": "AVAX", "AVAX": "AVAX", "CHAINLINK": "LINK", "LINK": "LINK",
            "LITECOIN": "LTC", "LTC": "LTC", "BINANCE COIN": "BNB", "BNBCOIN": "BNB", "BNB": "BNB",
            "UNISWAP": "UNI", "UNI": "UNI", "POLYGON": "MATIC", "MATIC": "MATIC", "NEAR PROTOCOL": "NEAR",
            "NEAR": "NEAR", "APTOS": "APT", "APT": "APT", "ARBITRUM": "ARB", "ARB": "ARB",
            "OPTIMISM": "OP", "OP": "OP", "CELESTIA": "TIA", "TIA": "TIA", "INJECTIVE": "INJ", "INJ": "INJ",
            "SUI": "SUI", "PEPE": "PEPE", "FLOKI": "FLOKI",
            "USD COIN": "USDC", "USDC": "USDC", "TETHER": "USDT", "USDT": "USDT", "TONCOIN": "TON", "TON": "TON",
            "DOLAR": "USDT", "DÓLAR": "USDT", "DOLLAR": "USDT",
        }

        # Se houver diretriz humana citando moedas, garanta que elas estejam na lista de avaliação técnica
        if user_directives:
            user_dir_upper = user_directives.upper()
            for name, ticker in NAME_TO_TICKER.items():
                if name in user_dir_upper:
                    if not any(c.get("coin", "").upper() == ticker for c in candidates):
                        candidates.append({"coin": ticker, "sentiment": "bullish (Diretriz Humana Obrigatória)"})

        for coin_info in candidates:
            raw_name = coin_info.get("coin", "").upper().strip()
            # Normaliza: tenta o mapa primeiro, senão usa o nome como ticker
            coin_ticker = NAME_TO_TICKER.get(raw_name, raw_name)
            symbol_to_check = f"{coin_ticker}/{self.currency}"
            
            # Buscar TA
            ta_stats = self.get_technical_indicators(symbol_to_check)
            if "error" not in ta_stats:
                technical_data.append(ta_stats)
            else:
                # Fallback para USDT se BRL não existir
                fallback_symbol = f"{coin_ticker}/USDT"
                ta_stats_usdt = self.get_technical_indicators(fallback_symbol)
                technical_data.append(ta_stats_usdt)

        system_prompt = f"""
        Você é um Especialista Quantitativo de Criptomoedas operando na Binance.
        Você baseia suas decisões em DUAS fontes de dados que devem entrar em concordância:
        
        1. INSIGHT FUNDAMENTALISTA (NOTÍCIAS):
        {json.dumps(news_insights)}
        
        2. ANÁLISE TÉCNICA MATEMÁTICA ATUAL (GRÁFICO DE 1H):
        {json.dumps(technical_data)}
        
        DIRETRIZ HUMANA (OVERRIDE DE PRIORIDADE MÁXIMA):
        "{user_directives}"
        Se houver uma diretriz humana acima, ELA SOBRESCREVE TODAS AS REGRAS.
        
        Sua missão:
        1. Avaliar se vale a pena comprar as moedas listadas cruzando as notícias com a matemática.
        2. REGRA DE OURO QUANTITATIVA: Se uma moeda está com RSI Overbought (Sobrecomprado > 70), NÃO APROVE A COMPRA, não importa o quão boa seja a notícia. Você virará liquidez se comprar no topo. Dê preferência para moedas em tendência de alta (is_uptrend = true) ou sub-avaliadas (is_oversold = true).
        3. Retorne um JSON apenas com as moedas aprovadas para compra no formato oficial da Binance. A moeda base para compra deve ser obrigatoriamente '{self.currency}'. Se a análise técnica foi feita em USDT, converta a recomendação final para '{self.currency}' (ex: 'BTC/{self.currency}').
        4. TESE DE PRESERVAÇÃO CAMBIAL (BRL É APENAS TRANPORTE): O investidor não quer manter patrimônio em Real (BRL), que se desvaloriza. O foco é acumular criptos sólidas e manter reservas em DÓLAR (USDT). Se o cenário de mercado estiver defensivo ou sem altcoins viáveis, indicar compra de 'USDT/{self.currency}' para dolarizar o capital é encorajado.
        
        O JSON deve ser estritamente no formato:
        {{
            "approved_trades": [
                {{"symbol": "TICKER/BASE", "is_memecoin": true|false, "confidence": 0 a 100, "reason": "Explique brevemente por que a união de TA e Notícia validou a compra."}}
            ]
        }}
        """
        
        print("[Agente 2] Consultando o modelo de linguagem quantitativo...")
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt="Quais são os tickers oficiais que passaram no filtro quantitativo para compra?",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            approved = data.get("approved_trades", [])
        except json.JSONDecodeError:
            print("[Agente 2] Erro: IA falhou ao gerar os tickers.")
            approved = []

        # Garantia absoluta: Se o humano definiu uma diretriz explícita de compra no painel de comando,
        # o comitê DEVE obedecer e nunca descartar o ativo solicitado!
        if user_directives:
            user_dir_lower = user_directives.lower()
            if any(w in user_dir_lower for w in ["compre", "comprar", "buy", "acumular", "aporte"]):
                for name, ticker in NAME_TO_TICKER.items():
                    if name.lower() in user_dir_lower:
                        if not any(t.get("symbol", "").split("/")[0] == ticker for t in approved):
                            print(f"[Agente 2] [OVERRIDE HUMANO] Inserindo {ticker} obrigatoriamente por diretriz direta: '{user_directives}'")
                            approved.append({
                                "symbol": f"{ticker}/{self.currency}",
                                "is_memecoin": False,
                                "confidence": 99,
                                "reason": f"Diretriz Humana de Prioridade Máxima: {user_directives}"
                            })
                        break

        return approved


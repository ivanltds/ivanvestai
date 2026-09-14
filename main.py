"""
Orquestrador Multi-Agente do IvanvestAI Hedge Fund.
"""
import sys
import os
import json
import ccxt
from src.config import settings
from src.agents.memory_agent import MemoryAgent
from src.agents.news_researcher import NewsResearcherAgent
from src.agents.crypto_expert import CryptoExpertAgent
from src.agents.portfolio_manager import PortfolioManagerAgent
from src.agents.trade_generator import TradeGeneratorAgent
from src.agents.risk_reviewer import RiskReviewerAgent
from src.db.vercel_kv import kv_db

def get_binance_balances():
    """Conecta na Binance e traz os saldos atuais"""
    exchange = ccxt.binance({
        'apiKey': settings.API_KEY,
        'secret': settings.SECRET_KEY,
        'enableRateLimit': True,
    })
    try:
        balance = exchange.fetch_balance()
        # Filtra apenas moedas que tem saldo positivo
        return {k: v for k, v in balance['total'].items() if v > 0}
    except Exception as e:
        print(f"Erro ao buscar saldo: {e}")
        return {}

def execute_order(order, exchange):
    """Executa a ordem a mercado com tratamento de erro de Símbolo"""
    symbol = order['symbol']
    action = order.get('action', 'BUY').upper()
    fiat_amount = order.get('fiat_amount', 0)
    
    try:
        # Busca o preço atual para calcular quantidade de crypto
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        
        if action == 'BUY':
            crypto_qty = fiat_amount / price
            if settings.DRY_RUN:
                print(f"[DRY_RUN] SIMULADO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL")
            else:
                print(f"[LIVE] EXECUTANDO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL")
                exchange.create_market_buy_order(symbol, crypto_qty)
            kv_db.register_buy(symbol, crypto_qty, fiat_amount)
            
            return {
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "crypto_qty": crypto_qty,
                "fiat_amount": fiat_amount
            }
            
        elif action == 'SELL':
            # Se for SELL, vamos vender tudo o que temos na memória ou a quantidade total real
            # Para simplificar na Fase 4: Venda de TODO o saldo (Stop Loss/Take profit daquela moeda)
            positions = kv_db.get_open_positions()
            if symbol in positions:
                crypto_qty = positions[symbol]['total_coins']
                if settings.DRY_RUN:
                    print(f"[DRY_RUN] SIMULADO: VENDA de {crypto_qty:.6f} {symbol} (Stop Loss/DCA)")
                else:
                    print(f"[LIVE] EXECUTANDO: VENDA de {crypto_qty:.6f} {symbol}")
                    exchange.create_market_sell_order(symbol, crypto_qty)
                kv_db.register_sell(symbol)
                
                return {
                    "symbol": symbol,
                    "action": "SELL",
                    "price": price,
                    "crypto_qty": crypto_qty,
                    "fiat_amount": crypto_qty * price
                }
            else:
                print(f"[ERRO DE LÓGICA] IA tentou vender {symbol} mas não temos histórico na memória.")
                return None
            
    except ccxt.BadSymbol:
        print(f"[ERRO DE MERCADO] A moeda {symbol} não existe ou não tem par com BRL na Binance. Ordem descartada.")
        return None
    except Exception as e:
        print(f"[ERRO DE EXECUÇÃO] Falha ao executar {symbol}: {e}")
        return None

def main():
    print("=== INICIANDO COMITÊ DO FUNDO HEDGE IVANVEST AI ===")
    
    # 0. Guardião da Memória (Puxa diretrizes do humano)
    ag0 = MemoryAgent()
    user_directives = ag0.fetch_context()
    
    # 1. Pesquisador de Notícias
    ag1 = NewsResearcherAgent()
    news_insights = ag1.analyze_news()
    print(f"Resumo do Mercado: {news_insights.get('summary')}\n")
    
    # 2. Especialista Cripto (Recebe a ordem do Humano)
    ag2 = CryptoExpertAgent()
    approved_trades = ag2.filter_and_map_coins(news_insights, user_directives)
    
    # 3. Gestor de Portfólio (Lê da Memória)
    ag3 = PortfolioManagerAgent()
    current_balances = get_binance_balances()
    open_positions_memory = kv_db.get_open_positions()
    final_trades = ag3.enforce_risk_limits(approved_trades, current_balances, open_positions_memory, news_insights, user_directives)
    
    # 4. Operador (Matemático)
    ag4 = TradeGeneratorAgent()
    final_orders = ag4.generate_orders(final_trades)
    
    # 5. Auditor (Revisor de Risco Final)
    ag5 = RiskReviewerAgent()
    final_orders = ag5.review_orders(final_orders)
    
    if final_orders:
        print("=== INICIANDO EXECUÇÃO (SIMULAÇÃO) ===" if settings.DRY_RUN else "=== INICIANDO EXECUÇÃO (MERCADO REAL) ===")
        exchange = ccxt.binance({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        
        executed_trades = []
        for order in final_orders:
            res = execute_order(order, exchange)
            if res: executed_trades.append(res)
            
        # Calcula e salva PNL (Simplificado: só soma os totais investidos para a curva de equity, num caso real somaria os balanços reais convertidos em fiat)
        total_pnl = sum([v['total_invested'] for v in kv_db.get_open_positions().values()])
        kv_db.save_portfolio_value(total_pnl)
    else:
        executed_trades = []
        print("Comitê decidiu NÃO operar nesta hora.")
        
    # 0. Finaliza o ciclo salvando o log no Dashboard
    ag0.commit_cycle(news_insights, executed_trades, current_balances)
    
    print("=== CICLO CONCLUÍDO COM SUCESSO ===")

if __name__ == "__main__":
    main()

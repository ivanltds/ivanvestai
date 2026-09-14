"""
Orquestrador Multi-Agente do IvanvestAI Hedge Fund.
"""
import sys
import os
import json
import ccxt
from src.config import settings
from src.agents.news_researcher import NewsResearcherAgent
from src.agents.crypto_expert import CryptoExpertAgent
from src.agents.portfolio_manager import PortfolioManagerAgent
from src.agents.trade_generator import TradeGeneratorAgent
from src.agents.risk_reviewer import RiskReviewerAgent

def get_binance_balances():
    """Conecta na Binance e traz os saldos atuais"""
    exchange = ccxt.binance({
        'apiKey': settings.BINANCE_API_KEY,
        'secret': settings.BINANCE_SECRET_KEY,
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
    """Executa a ordem a mercado"""
    symbol = order['symbol']
    fiat_amount = order['fiat_amount']
    # Busca o preço atual para calcular quantidade de crypto
    ticker = exchange.fetch_ticker(symbol)
    price = ticker['last']
    crypto_qty = fiat_amount / price
    
    if settings.DRY_RUN:
        print(f"[DRY_RUN] SIMULADO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL/USDT")
    else:
        print(f"[LIVE] EXECUTANDO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL/USDT")
        exchange.create_market_buy_order(symbol, crypto_qty)

def main():
    print("=== INICIANDO COMITÊ DO FUNDO HEDGE IVANVEST AI ===")
    
    # 1. Pesquisador de Notícias
    ag1 = NewsResearcherAgent()
    news_insights = ag1.analyze_news()
    print(f"Resumo do Mercado: {news_insights.get('summary')}\n")
    
    # 2. Especialista Cripto
    ag2 = CryptoExpertAgent()
    approved_trades = ag2.filter_and_map_coins(news_insights)
    
    # 3. Gestor de Portfólio
    ag3 = PortfolioManagerAgent()
    current_balances = get_binance_balances()
    final_trades = ag3.enforce_risk_limits(approved_trades, current_balances)
    
    # 4. Operador (Matemático)
    ag4 = TradeGeneratorAgent()
    orders = ag4.generate_orders(final_trades)
    
    # 5. Revisor de Risco (Compliance)
    ag5 = RiskReviewerAgent()
    safe_orders = ag5.review_orders(orders)
    
    if not safe_orders:
        print("Comitê decidiu NÃO operar nesta hora.")
        sys.exit(0)
        
    print(f"=== INICIANDO EXECUÇÃO ({'SIMULAÇÃO' if settings.DRY_RUN else 'REAL'}) ===")
    exchange = ccxt.binance({
        'apiKey': settings.BINANCE_API_KEY,
        'secret': settings.BINANCE_SECRET_KEY,
        'enableRateLimit': True,
    })
    
    for order in safe_orders:
        execute_order(order, exchange)
        
    print("=== CICLO CONCLUÍDO COM SUCESSO ===")
    sys.exit(0)

if __name__ == "__main__":
    main()

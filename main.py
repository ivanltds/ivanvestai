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
from src.agents.performance_analyst import PerformanceAnalystAgent
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
            is_dry_run = order.get('dry_run', True)
            if is_dry_run:
                print(f"[DRY_RUN] SIMULADO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL")
            else:
                print(f"[LIVE] EXECUTANDO: Compra de {crypto_qty:.6f} {symbol} por {fiat_amount} BRL")
                exchange.create_market_buy_order(symbol, crypto_qty)
            kv_db.register_buy(symbol, crypto_qty, fiat_amount)
            
            base_asset = symbol.split('/')[0] if '/' in symbol else symbol
            quote_asset = symbol.split('/')[1] if '/' in symbol else "BRL"
            
            return {
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "crypto_qty": crypto_qty,
                "fiat_amount": fiat_amount,
                "from_asset": quote_asset,
                "to_asset": base_asset,
                "pair_flow": f"{quote_asset} -> {base_asset}",
                "dry_run": is_dry_run
            }
            
        elif action == 'SELL':
            positions = kv_db.get_open_positions()
            if symbol in positions:
                crypto_qty = positions[symbol]['total_coins']
                is_dry_run = order.get('dry_run', True)
                if is_dry_run:
                    print(f"[DRY_RUN] SIMULADO: VENDA de {crypto_qty:.6f} {symbol} (Stop Loss/DCA)")
                else:
                    print(f"[LIVE] EXECUTANDO: VENDA de {crypto_qty:.6f} {symbol}")
                    exchange.create_market_sell_order(symbol, crypto_qty)
                kv_db.register_sell(symbol)
                
                base_asset = symbol.split('/')[0] if '/' in symbol else symbol
                quote_asset = symbol.split('/')[1] if '/' in symbol else "BRL"

                return {
                    "symbol": symbol,
                    "action": "SELL",
                    "price": price,
                    "crypto_qty": crypto_qty,
                    "fiat_amount": crypto_qty * price,
                    "from_asset": base_asset,
                    "to_asset": quote_asset,
                    "pair_flow": f"{base_asset} -> {quote_asset}",
                    "dry_run": is_dry_run
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
    
    # Carrega configurações do Redis (com fallback para variáveis de ambiente)
    bot_config = kv_db.get_bot_config()
    dry_run = bot_config.get("dry_run", True)
    dca_amount = bot_config.get("dca_amount_brl", 50.0)
    min_order = bot_config.get("min_order_brl", 8.0)
    max_order = bot_config.get("max_order_brl", 200.0)
    print(f"[Config] Dry Run={dry_run} | DCA={dca_amount}BRL | Min={min_order}BRL | Max={max_order}BRL")

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
    
    # 1.5 Analista de Performance (Aprendizado)
    ag1_5 = PerformanceAnalystAgent()
    recent_logs = kv_db.get_audit_logs(limit=10)
    open_positions_memory = kv_db.get_open_positions()
    learned_lessons = ag1_5.generate_lessons(recent_logs, open_positions_memory)
    
    # 3. Gestor de Portfólio (Lê da Memória)
    ag3 = PortfolioManagerAgent()
    current_balances = get_binance_balances()
    final_trades = ag3.enforce_risk_limits(approved_trades, current_balances, open_positions_memory, news_insights, user_directives, learned_lessons)
    
    # 4. Operador (Matemático)
    ag4 = TradeGeneratorAgent()
    final_orders = ag4.generate_orders(final_trades)
    
    # 5. Auditor (Revisor de Risco Final)
    ag5 = RiskReviewerAgent()
    final_orders = ag5.review_orders(final_orders)
    
    if final_orders:
        print("=== INICIANDO EXECUÇÃO (SIMULAÇÃO) ===" if dry_run else "=== INICIANDO EXECUÇÃO (MERCADO REAL) ===")
        exchange = ccxt.binance({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        
        executed_trades = []
        for order in final_orders:
            # Injeta o modo dry_run em cada ordem
            order['dry_run'] = dry_run
            res = execute_order(order, exchange)
            if res: executed_trades.append(res)
            
        # Calcula e salva PNL (Simplificado: só soma os totais investidos para a curva de equity, num caso real somaria os balanços reais convertidos em fiat)
        total_pnl = sum([v['total_invested'] for v in kv_db.get_open_positions().values()])
        kv_db.save_portfolio_value(total_pnl)
    else:
        executed_trades = []
        print("Comitê decidiu NÃO operar nesta hora.")
        
    # 0. Finaliza o ciclo salvando o log no Dashboard
    ag0.commit_cycle(news_insights, executed_trades, current_balances, learned_lessons, dry_run)
    
    print("=== CICLO CONCLUÍDO COM SUCESSO ===")

if __name__ == "__main__":
    main()

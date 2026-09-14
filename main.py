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
    """Executa a ordem a mercado com tratamento de erro de Símbolo e roteamento inteligente de par"""
    symbol = order['symbol']
    action = order.get('action', 'BUY').upper()
    fiat_amount = order.get('fiat_amount', 0)
    
    try:
        base_asset = symbol.split('/')[0] if '/' in symbol else symbol
        quote_asset = symbol.split('/')[1] if '/' in symbol else "BRL"

        # Garante que o par correto existe na Binance (ex: FLOKI/BRL não existe, mas FLOKI/USDT existe)
        markets = exchange.load_markets()
        if symbol not in markets:
            if f"{base_asset}/USDT" in markets:
                symbol = f"{base_asset}/USDT"
                quote_asset = "USDT"
            elif f"{base_asset}/BRL" in markets:
                symbol = f"{base_asset}/BRL"
                quote_asset = "BRL"

        # Busca o preço atual para calcular quantidade de crypto
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        
        if action == 'BUY':
            raw_qty = fiat_amount / price
            try:
                crypto_qty = float(exchange.amount_to_precision(symbol, raw_qty))
            except:
                crypto_qty = round(raw_qty, 6)
            is_dry_run = order.get('dry_run', True)
            if is_dry_run:
                print(f"[DRY_RUN] SIMULADO: Compra de {crypto_qty} {symbol} por {fiat_amount} {quote_asset}")
            else:
                print(f"[LIVE] EXECUTANDO: Compra de {crypto_qty} {symbol} por {fiat_amount} {quote_asset}")
                try:
                    exchange.create_market_buy_order(symbol, crypto_qty)
                except Exception as buy_err:
                    print(f"[LIVE] Tentando compra com quoteOrderQty ({fiat_amount} {quote_asset}): {buy_err}")
                    exchange.create_market_buy_order(symbol, None, params={'quoteOrderQty': fiat_amount})
            kv_db.register_buy(symbol, crypto_qty, fiat_amount)
            
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
            # Encontra a posição por correspondência exata ou por base_asset
            matching_key = symbol if symbol in positions else next((k for k in positions if k.split('/')[0] == base_asset), None)
            
            # Busca saldo livre real na Binance
            try:
                bal = exchange.fetch_balance()
                free_asset_qty = float(bal.get('free', {}).get(base_asset, 0.0))
            except Exception as e:
                print(f"[AVISO] Falha ao consultar saldo da Binance para {base_asset}: {e}")
                free_asset_qty = 0.0

            if matching_key and matching_key in positions:
                recorded_qty = positions[matching_key].get('total_coins', 0.0)
                target_qty = min(recorded_qty, free_asset_qty) if free_asset_qty > 0 else recorded_qty
            else:
                # Se não estiver no Redis, mas houver saldo livre real na Binance, PERMITE A VENDA!
                target_qty = free_asset_qty
                matching_key = symbol

            # Checagem de limites de lote e valor mínimo da Binance
            market = markets.get(symbol, {})
            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.0) or 0.0
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 0.0) or 1.0

            try:
                crypto_qty = float(exchange.amount_to_precision(symbol, target_qty))
            except:
                crypto_qty = target_qty

            order_value = crypto_qty * price

            if crypto_qty <= 0 or (min_amount > 0 and crypto_qty < min_amount) or order_value < min_cost:
                print(f"[VENDA IGNORADA] Saldo de {base_asset} ({free_asset_qty}) é poeira residual abaixo do mínimo da Binance ({min_amount} moedas / {min_cost:.2f} {quote_asset}). Ordem ignorada.")
                if matching_key and matching_key in positions:
                    kv_db.register_sell(matching_key)
                return None


            is_dry_run = order.get('dry_run', True)
            if is_dry_run:
                print(f"[DRY_RUN] SIMULADO: VENDA de {crypto_qty} {symbol} (Rebalanceamento/Stop)")
            else:
                print(f"[LIVE] EXECUTANDO: VENDA de {crypto_qty} {symbol} (Rebalanceamento/Stop)")
                exchange.create_market_sell_order(symbol, crypto_qty)
                
            if matching_key and matching_key in positions:
                kv_db.register_sell(matching_key)

            return {
                "symbol": symbol,
                "action": "SELL",
                "price": price,
                "crypto_qty": crypto_qty,
                "fiat_amount": round(crypto_qty * price, 2),
                "from_asset": base_asset,
                "to_asset": quote_asset,
                "pair_flow": f"{base_asset} -> {quote_asset}",
                "dry_run": is_dry_run
            }

            
    except ccxt.BadSymbol:
        print(f"[ERRO DE MERCADO] A moeda {symbol} não existe ou não tem par negociável na Binance. Ordem descartada.")
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
    kv_db.sync_with_binance()
    open_positions_memory = kv_db.get_open_positions()

    learned_lessons = ag1_5.generate_lessons(recent_logs, open_positions_memory)
    
    # 3. Gestor de Portfólio (Lê da Memória)
    ag3 = PortfolioManagerAgent()
    current_balances = get_binance_balances()
    final_trades = ag3.enforce_risk_limits(approved_trades, current_balances, open_positions_memory, news_insights, user_directives, learned_lessons)
    
    # 4. Operador (Matemático)
    ag4 = TradeGeneratorAgent()
    final_orders = ag4.generate_orders(final_trades, current_balances=current_balances)
    
    # 5. Auditor (Revisor de Risco Final)
    ag5 = RiskReviewerAgent()
    orders_buy_sum = round(sum(o.get('fiat_amount', 0) for o in final_orders if o.get('action') == 'BUY'), 2)
    final_orders = ag5.review_orders(final_orders, max_budget=max(orders_buy_sum, dca_amount))
    
    if final_orders:
        print("=== INICIANDO EXECUÇÃO (SIMULAÇÃO) ===" if dry_run else "=== INICIANDO EXECUÇÃO (MERCADO REAL) ===")
        exchange = ccxt.binance({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        
        executed_trades = []
        # Executa ordens de VENDA primeiro para liberar caixa/liquidez antes de executar COMPRAS
        sorted_orders = sorted(final_orders, key=lambda x: 0 if x.get('action') == 'SELL' else 1)
        for order in sorted_orders:
            # Injeta o modo dry_run em cada ordem
            order['dry_run'] = dry_run
            res = execute_order(order, exchange)
            if res: executed_trades.append(res)

        # Se ordens de venda foram executadas e liberaram caixa, rotaciona imediatamente para compra no mesmo ciclo
        had_sells = any(t.get('action') == 'SELL' for t in executed_trades)
        had_buys = any(t.get('action') == 'BUY' for t in executed_trades)
        if had_sells and not had_buys:
            fresh_balances = get_binance_balances()
            fresh_usdt = float(fresh_balances.get("USDT", 0.0))
            fresh_brl = float(fresh_balances.get("BRL", 0.0))
            if fresh_usdt >= 5.0 or fresh_brl >= min_order:
                print(f"[REBALANCEAMENTO ATIVO] Caixa liberado pelas vendas: ${fresh_usdt:.2f} USDT | R${fresh_brl:.2f} BRL. Gerando compra de rotação imediata...")
                rotation_orders = ag4.generate_orders(final_trades, current_balances=fresh_balances)
                rotation_orders = [o for o in rotation_orders if o.get('action') == 'BUY']
                rotation_orders = ag5.review_orders(rotation_orders, max_budget=max_order)
                for order in rotation_orders:
                    order['dry_run'] = dry_run
                    r = execute_order(order, exchange)
                    if r: executed_trades.append(r)
            
        # Calcula e salva PNL
        total_pnl = sum([v['total_invested'] for v in kv_db.get_open_positions().values()])
        kv_db.save_portfolio_value(total_pnl)

    else:
        executed_trades = []
        print("Comitê decidiu NÃO operar nesta hora.")
        
    # 0. Finaliza o ciclo salvando o log no Dashboard com os saldos atualizados pós-execução
    latest_balances = get_binance_balances()
    ag0.commit_cycle(news_insights, executed_trades, latest_balances, learned_lessons, dry_run)

    
    print("=== CICLO CONCLUÍDO COM SUCESSO ===")

if __name__ == "__main__":
    main()

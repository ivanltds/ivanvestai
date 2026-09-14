import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

# Garante que as variáveis de ambiente sejam carregadas sempre
load_dotenv()

class KVDatabase:
    """
    Cliente para conectar no Vercel KV / Upstash Redis via API REST.
    Permite custo zero, fácil implementação e acesso direto do Frontend.
    """
    def __init__(self):
        # A API Rest da Upstash/Vercel é extremamente simples e não exige driver complexo
        self.url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL")
        self.token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN")
        
        if not self.url or not self.token:
            print("[DB] Aviso: KV_REST_API_URL ou TOKEN não configurados. A memória não será persistida.")
            self.enabled = False
        else:
            self.enabled = True
            # Remove espaços, quebras de linha acidentais do GitHub Secrets e barra final
            self.url = self.url.strip().strip('"').strip("'").rstrip('/')
            self.token = self.token.strip().strip('"').strip("'")

    def _execute_command(self, *args):
        if not self.enabled:
            return None
        
        # Faz o URL encode de todos os argumentos (importante para JSON e caracteres especiais)
        import urllib.parse
        encoded_args = [urllib.parse.quote(str(a), safe='') for a in args]
        endpoint = f"{self.url}/{'/'.join(encoded_args)}"
        
        req = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {self.token}"})
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())
                return result.get('result')
        except urllib.error.URLError as e:
            print(f"[DB] Erro de rede no Vercel KV: {e}")
            return None
        except Exception as e:
            print(f"[DB] Erro genérico no KV: {e}")
            return None

    def get_open_positions(self) -> dict:
        """Retorna dicionário com o histórico de preço médio de compra por Ticker"""
        data = self._execute_command("get", "portfolio:open_positions")
        if data:
            try:
                import urllib.parse
                decoded = urllib.parse.unquote(data) if isinstance(data, str) else data
                return json.loads(decoded) if isinstance(decoded, str) else decoded
            except:
                return {}
        return {}

    def save_open_positions(self, positions: dict):
        """Salva as posições atuais na memória"""
        import urllib.parse
        encoded_val = urllib.parse.quote(json.dumps(positions), safe='')
        self._execute_command("set", "portfolio:open_positions", encoded_val)

    def register_buy(self, symbol: str, crypto_qty: float, fiat_spent: float):
        """
        Registra uma compra, recalculando o preço médio.
        """
        positions = self.get_open_positions()
        
        if symbol not in positions:
            positions[symbol] = {"total_invested": 0.0, "total_coins": 0.0, "avg_price": 0.0}
            
        pos = positions[symbol]
        pos["total_invested"] += fiat_spent
        pos["total_coins"] += crypto_qty
        pos["avg_price"] = pos["total_invested"] / pos["total_coins"]
        
        self.save_open_positions(positions)
        print(f"[DB] Registrada COMPRA: {symbol} - Novo Preço Médio: {pos['avg_price']:.4f}")

    def register_sell(self, symbol: str):
        """
        Registra uma venda, removendo a moeda da carteira local.
        (Num projeto real, poderia também gravar num log de 'trade_history')
        """
        positions = self.get_open_positions()
        
        if symbol in positions:
            del positions[symbol]
            self.save_open_positions(positions)
            print(f"[DB] Registrada VENDA TOTAL: {symbol} removido da carteira (Stop Loss / Take Profit).")

    # ==================================================
    # MÉTODOS DO AGENTE DA MEMÓRIA (FASE 5)
    # ==================================================
    
    def get_user_directives(self) -> str:
        """Lê instruções injetadas pelo usuário (Overrides). Expiram sozinhas no Redis via TTL."""
        data = self._execute_command("get", "ai:user_directives")
        return data if data else ""

    def get_bot_config(self) -> dict:
        """Lê todas as configurações do bot salvas pelo frontend."""
        defaults = {
            "dry_run": True,
            "dca_amount_brl": 50.0,
            "min_order_brl": 8.0,
            "max_order_brl": 200.0,
            "max_memecoin_pct": 20,
            "min_assets": 5,
            "min_stop_pct": 5,
            "max_stop_pct": 25,
            "auto_deploy_deposits": True,
            "preferred_reserve": "USDT",
        }
        try:
            import urllib.parse
            raw = self._execute_command("get", "config:bot_settings")
            if raw:
                decoded = urllib.parse.unquote(raw) if isinstance(raw, str) else raw
                data = json.loads(decoded) if isinstance(decoded, str) else decoded
                defaults.update(data)
        except Exception as e:
            print(f"[DB] Erro ao ler config: {e}")
        return defaults

    def save_bot_config(self, config: dict):
        """Salva as configurações do bot no Redis."""
        import urllib.parse
        encoded = urllib.parse.quote(json.dumps(config), safe='')
        self._execute_command("set", "config:bot_settings", encoded)
        print("[DB] Configurações salvas com sucesso.")
        
    def get_audit_logs(self, limit: int = 10) -> list:
        """Lê os últimos N logs."""
        try:
            data = self._execute_command("lrange", "dashboard:audit_logs", "0", str(limit - 1))
            if data:
                return [json.loads(urllib.parse.unquote(item)) for item in data]
        except Exception as e:
            print(f"[DB] Erro ao ler audit_logs: {e}")
        return []

    def save_audit_log(self, log_entry: dict):
        """Salva a execução no Diário de Bordo do Dashboard (usa lpush para lista)."""
        import urllib.parse
        encoded_val = urllib.parse.quote(json.dumps(log_entry), safe='')
        
        # 1. Pega o item 100 (que vai ser jogado fora pelo ltrim) e salva no arquivo histórico
        old_item = self._execute_command("lindex", "dashboard:audit_logs", "99")
        if old_item:
            self._execute_command("lpush", "dashboard:audit_logs_archive", old_item)
            
        # 2. Insere o novo log
        self._execute_command("lpush", "dashboard:audit_logs", encoded_val)
        # 3. Mantém apenas os últimos 100 na tabela principal
        self._execute_command("ltrim", "dashboard:audit_logs", "0", "99")

    def save_portfolio_value(self, total_pnl: float):
        """Salva a evolução do PNL para o gráfico"""
        import time, urllib.parse
        log = {
            "timestamp": int(time.time()),
            "value": total_pnl
        }
        encoded_log = urllib.parse.quote(json.dumps(log), safe='')
        self._execute_command("lpush", "dashboard:pnl_history", encoded_log)
        self._execute_command("ltrim", "dashboard:pnl_history", "0", "99")
        
    def sync_with_binance(self, real_balances: dict = None):
        """
        Sincroniza a carteira diretamente com a Binance (Fonte da Verdade Oficial).
        Consulta os saldos reais, cotações ao vivo e o histórico oficial de trades
        da Binance para calcular o Preço Médio (PM) e PnL com exatidão máxima.
        """
        import ccxt
        from src.config import settings

        try:
            exchange = ccxt.binance({
                'apiKey': settings.API_KEY,
                'secret': settings.SECRET_KEY,
                'enableRateLimit': True,
            })
            
            # Se não recebeu real_balances, busca diretamente da Binance
            if not real_balances:
                bal_data = exchange.fetch_balance()
                real_balances = {k: float(v) for k, v in bal_data.get('free', {}).items() if float(v) > 0.000001}

            # Cotação do Dólar para normalização
            try:
                usdt_rate = float(exchange.fetch_ticker('USDT/BRL')['last'])
            except:
                usdt_rate = 5.17

            old_positions = self.get_open_positions()
            new_positions = {}

            for coin, qty in real_balances.items():
                if coin in ['BRL', 'USDT'] or qty <= 0.00001:
                    continue

                # Preço atual da moeda em BRL
                cur_price = None
                try:
                    cur_price = float(exchange.fetch_ticker(f"{coin}/BRL")['last'])
                except:
                    try:
                        cur_price = float(exchange.fetch_ticker(f"{coin}/USDT")['last']) * usdt_rate
                    except:
                        cur_price = 0.0

                val_brl = qty * (cur_price or 0.0)
                # Ignora poeiras irrelevantes (< R$ 2,00)
                if val_brl < 2.0:
                    continue

                symbol = f"{coin}/BRL"
                old_entry = old_positions.get(symbol, {})
                old_last_price = old_entry.get('current_price', cur_price)

                # Consulta o histórico real de compras na Binance (fetch_my_trades) para extrair o PM oficial
                total_cost_brl = 0.0
                total_qty_bought = 0.0
                for quote in ['BRL', 'USDT']:
                    trade_sym = f"{coin}/{quote}"
                    try:
                        trades = exchange.fetch_my_trades(trade_sym)
                        for t in trades:
                            if t.get('side') == 'buy':
                                cost = float(t.get('cost', 0.0))
                                amount = float(t.get('amount', 0.0))
                                if quote == 'USDT':
                                    cost = cost * usdt_rate
                                total_cost_brl += cost
                                total_qty_bought += amount
                    except Exception:
                        pass

                if total_qty_bought > 0:
                    avg_price = round(total_cost_brl / total_qty_bought, 2)
                    total_invested = round(qty * avg_price, 2)
                else:
                    avg_price = round(cur_price, 2)
                    total_invested = round(val_brl, 2)

                pnl_pct = round(((cur_price - avg_price) / avg_price) * 100, 2) if avg_price > 0 else 0.0

                new_positions[symbol] = {
                    "total_coins": qty,
                    "total_invested": total_invested,
                    "avg_price": avg_price,
                    "current_price": round(cur_price, 2),
                    "last_price": round(old_last_price, 2),
                    "pnl_pct": pnl_pct
                }

            # Salva posições limpas e unificadas sem duplicatas
            import urllib.parse
            encoded_val = urllib.parse.quote(json.dumps(new_positions), safe='')
            self._execute_command("set", "portfolio:open_positions", encoded_val)

            # Salva o Total Alocado Oficial
            total_invested_all = sum(p['total_invested'] for p in new_positions.values())
            self.save_portfolio_value(total_invested_all)

            # Salva saldos da conta
            if real_balances:
                encoded_balances = urllib.parse.quote(json.dumps(real_balances), safe='')
                self._execute_command("set", "portfolio:account_balances", encoded_balances)

            print(f"[DB] Posições e Preço Médio sincronizados oficialmente com a Binance: {list(new_positions.keys())}")

        except Exception as e:
            print(f"[DB] Erro ao sincronizar oficialmente com a Binance: {e}")


    def get_account_balances(self) -> dict:
        """Retorna os saldos reais de todas as moedas na conta da Binance"""
        data = self._execute_command("get", "portfolio:account_balances")
        if data:
            try:
                import urllib.parse
                decoded = urllib.parse.unquote(data) if isinstance(data, str) else data
                return json.loads(decoded) if isinstance(decoded, str) else decoded
            except:
                return {}
        return {}

    def save_market_sentiment(self, is_bullish: bool, summary: str):
        """Salva o status do 'Fear & Greed' baseado na IA de notícias."""
        import time, urllib.parse
        entry = {"timestamp": int(time.time()), "is_bullish": is_bullish, "summary": summary}
        encoded_val = urllib.parse.quote(json.dumps(entry), safe='')
        self._execute_command("set", "dashboard:current_sentiment", encoded_val)

kv_db = KVDatabase()

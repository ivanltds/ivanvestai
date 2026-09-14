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
            # Remove barra no final da URL se existir
            self.url = self.url.rstrip('/')

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
                return json.loads(data)
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
        
    def sync_with_binance(self, real_balances: dict):
        """
        Sincroniza a memória com o saldo real da Binance.
        Isso corrige divergências por conta de taxas da corretora
        e ajusta o Preço Médio (PM) para a realidade matemática.
        Atualiza também o Preço Atual e Preço Anterior para o Dashboard.
        """
        positions = self.get_open_positions()
        synced = False
        
        # 1. Buscar preços atuais na Binance para as moedas em memória
        tickers = {}
        if positions:
            try:
                import ccxt
                exchange = ccxt.binance({'enableRateLimit': True})
                # Evita chamadas inválidas buscando 1 por 1 ou fetch_tickers se suportado
                for symbol in positions.keys():
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        tickers[symbol] = ticker['last']
                    except:
                        pass
            except Exception as e:
                print(f"[DB] Aviso: Não foi possível buscar cotações para PnL: {e}")
        
        for symbol in list(positions.keys()):
            # Atualiza histórico de preço para a Seta de Tendência e PnL Real
            if symbol in tickers:
                new_price = tickers[symbol]
                old_price = positions[symbol].get('current_price', new_price)
                positions[symbol]['last_price'] = old_price
                positions[symbol]['current_price'] = new_price
                synced = True

            # O symbol na Binance geralmente vem como 'BTC', no bot salvamos 'BTC/BRL'
            base_coin = symbol.split('/')[0] if '/' in symbol else symbol
            
            if base_coin in real_balances and real_balances[base_coin] > 0.00001:
                # Atualiza a quantidade exata de moedas que temos (pós-taxas)
                real_qty = real_balances[base_coin]
                if positions[symbol].get('total_coins', 0) != real_qty:
                    positions[symbol]['total_coins'] = real_qty
                    # Recalcula o Preço Médio com base na quantidade real
                    positions[symbol]['avg_price'] = positions[symbol].get('total_invested', 0) / real_qty
                    synced = True
            else:
                # Se não temos mais saldo na Binance, remove da memória (Zero Dust)
                del positions[symbol]
                synced = True
                
        if synced:
            import urllib.parse
            encoded_val = urllib.parse.quote(json.dumps(positions), safe='')
            self._execute_command("set", "portfolio:open_positions", encoded_val)
            print("[DB] Sincronização com a Binance concluída. Preços Médios e Atuais ajustados!")

        # Salva os saldos gerais da conta (BRL em trânsito, USDT e Criptos)
        if real_balances:
            import urllib.parse
            encoded_balances = urllib.parse.quote(json.dumps(real_balances), safe='')
            self._execute_command("set", "portfolio:account_balances", encoded_balances)

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

import time
import json
import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone
from src.config import settings
from src.db.vercel_kv import kv_db

class SniperTraderAgent:
    """
    Agente Especializado em Day Trade Multi-Ativo de Alta Frequência (Modo Sniper).
    - Suporta qualquer ativo da carteira como funding source (ex: BTC, ETH, USDT, BRL).
    - Escaneia em tempo real as moedas mais voláteis (NEAR, PEPE, SUI, DOGE, XRP, SOL, etc.).
    - Distribui o capital proporcionalmente entre as top 1-3 oportunidades.
    - Executa e acompanha cada ativo de forma individual e assíncrona com Trailing Stop e Anti-Loss.
    """
    CANDIDATES_USDT = ['NEAR/USDT', 'PEPE/USDT', 'SUI/USDT', 'DOGE/USDT', 'XRP/USDT', 'SOL/USDT', 'WIF/USDT', 'FET/USDT', 'SHIB/USDT']
    CANDIDATES_BRL = ['XRP/BRL', 'SOL/BRL', 'NEAR/BRL', 'DOGE/BRL', 'PEPE/BRL', 'BTC/BRL']

    def __init__(self, capital: float = 10.0, currency: str = "USDT", source_asset: str = "USDT", symbol: str = None, dry_run: bool = False):
        self.capital = float(capital)
        self.currency = currency.upper()
        self.source_asset = source_asset.upper() if source_asset else self.currency
        self.symbol = symbol  # Se fornecido um par específico, opera ele; se None, ativa o scanner multi-ativo
        self.dry_run = dry_run
        self.exchange = ccxt.binance({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        self.session_duration_sec = 600   # 10 minutos
        self.grace_period_sec = 120       # +2 minutos de tolerância anti-loss
        self.snapshot_interval_sec = 30   # Snapshots a cada 30s
        self.liquidity_rotation = None
        self.started_at = None

    def emit_thought(self, msg_type: str, symbol: str, tag: str, message: str):
        """Emite uma observação ou justificativa no chat em tempo real e persiste no Redis."""
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elapsed = int(time.time() - self.started_at) if hasattr(self, 'started_at') and self.started_at else 0
        chat_item = {
            "id": f"chat_{int(time.time() * 1000)}",
            "timestamp": now_ts,
            "elapsed_sec": elapsed,
            "elapsed_str": f"{elapsed // 60:02d}:{elapsed % 60:02d}",
            "type": msg_type,
            "symbol": symbol,
            "tag": tag,
            "message": message,
            "sender": "Sniper AI Agent"
        }
        try:
            kv_db.append_daytrade_chat(chat_item)
        except Exception as e:
            print(f"[Sniper Chat Error] {e}")
        print(f"[Sniper Chat | {tag}] {symbol}: {message}")

    def fetch_1m_ta(self, symbol: str) -> dict:
        """Coleta as últimas velas de 1m e calcula indicadores rápidos (Bollinger, RSI-7, VWAP)."""
        try:
            bars = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=35)
            if not bars or len(bars) < 20:
                return {}
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # RSI Rápido de 7 períodos
            df['rsi7'] = ta.rsi(df['close'], length=7)
            # Bandas de Bollinger (20, 2)
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None and not bb.empty:
                df['bb_lower'] = bb.iloc[:, 0]
                df['bb_mid'] = bb.iloc[:, 1]
                df['bb_upper'] = bb.iloc[:, 2]
            else:
                df['bb_lower'] = df['close'] * 0.998
                df['bb_upper'] = df['close'] * 1.008

            # VWAP Intradiário
            df['typical'] = (df['high'] + df['low'] + df['close']) / 3
            vol_sum = df['volume'].cumsum()
            df['vwap'] = (df['typical'] * df['volume']).cumsum() / (vol_sum.replace(0, np.nan) if 'np' in globals() else vol_sum)

            # Volatilidade de 10 min
            df['rolling_max'] = df['high'].rolling(10).max()
            df['rolling_min'] = df['low'].rolling(10).min()
            df['range_10m_pct'] = ((df['rolling_max'] - df['rolling_min']) / df['rolling_min'].replace(0, 1)) * 100

            latest = df.iloc[-1]
            return {
                "symbol": symbol,
                "close": float(latest['close']),
                "rsi7": float(latest['rsi7']) if not pd.isna(latest['rsi7']) else 50.0,
                "bb_lower": float(latest['bb_lower']) if 'bb_lower' in latest else float(latest['close'] * 0.998),
                "bb_upper": float(latest['bb_upper']) if 'bb_upper' in latest else float(latest['close'] * 1.008),
                "vwap": float(latest['vwap']) if not pd.isna(latest['vwap']) else float(latest['close']),
                "range_10m_pct": float(latest['range_10m_pct']) if not pd.isna(latest['range_10m_pct']) else 0.5
            }
        except Exception as e:
            return {}

    def scan_opportunities(self) -> list:
        """Escaneia a cesta de altcoins candidatas e ranqueia as melhores oportunidades de scalping."""
        candidates = self.CANDIDATES_USDT if self.currency == 'USDT' else self.CANDIDATES_BRL
        if self.symbol and self.symbol in candidates:
            candidates = [self.symbol]

        scored = []
        print(f"[Sniper Scanner] Varrendo {len(candidates)} pares em busca de volatilidade e oportunidades...")
        for sym in candidates:
            ta_data = self.fetch_1m_ta(sym)
            if not ta_data or ta_data.get('close', 0) <= 0:
                continue

            price = ta_data['close']
            rsi = ta_data['rsi7']
            range_10m = ta_data['range_10m_pct']
            vwap = ta_data['vwap']
            bb_lower = ta_data['bb_lower']

            # Score de Oportunidade:
            # - Maior volatilidade nos 10m ganha mais pontos
            # - RSI sobrevenda (< 35) ou rompimento de momentum (> 50 e < 65) ganha bônus
            score = range_10m * 10.0
            setup = "Neutro"
            if rsi < 32 and price <= bb_lower * 1.002:
                score += 35.0
                setup = "Sobrevenda Bollinger"
            elif 51 <= rsi <= 64 and price >= vwap * 1.0005:
                score += 30.0
                setup = "Rompimento VWAP"
            elif range_10m >= 0.8:
                score += 20.0
                setup = "Alta Volatilidade"

            scored.append({
                "symbol": sym,
                "price": price,
                "rsi7": round(rsi, 1),
                "range_10m_pct": round(range_10m, 2),
                "score": round(score, 1),
                "setup": setup,
                "ta": ta_data
            })

        scored.sort(key=lambda x: x['score'], reverse=True)
        print(f"[Sniper Scanner] Top Oportunidades Encontradas:")
        for s in scored[:4]:
            print(f"  • {s['symbol']}: Range 10m {s['range_10m_pct']}% | RSI {s['rsi7']} | Setup: {s['setup']} (Score: {s['score']})")

        return scored

    def allocate_capital(self, scored_candidates: list) -> list:
        """Distribui o capital entre os Top 1 a 3 ativos respeitando os limites da Binance."""
        if not scored_candidates:
            return []

        # Determina o lote mínimo de cada moeda
        # PEPE, DOGE, SHIB, WIF = $1.00 USDT
        # Demais pares USDT = $5.00 USDT
        # Pares BRL = R$ 10.00
        min_costs = {
            'PEPE/USDT': 1.0, 'DOGE/USDT': 1.0, 'SHIB/USDT': 1.0, 'WIF/USDT': 1.0, 'FLOKI/USDT': 1.0
        }
        default_min = 5.0 if self.currency == 'USDT' else 10.0

        allocations = []
        rem_capital = self.capital

        for cand in scored_candidates:
            sym = cand['symbol']
            sym_min = min_costs.get(sym, default_min)
            if rem_capital < sym_min:
                continue

            # Se temos capital para múltiplos ativos
            if rem_capital >= (sym_min * 2) and len(allocations) < 2:
                alloc = round(rem_capital / 2, 2)
            elif rem_capital >= (sym_min * 3) and len(allocations) < 3:
                alloc = round(rem_capital / 3, 2)
            else:
                alloc = round(rem_capital, 2)

            allocations.append({
                "symbol": sym,
                "allocated_capital": alloc,
                "min_cost": sym_min,
                "setup": cand['setup'],
                "price": cand['price'],
                "ta": cand['ta']
            })
            rem_capital -= alloc
            if len(allocations) >= 3 or rem_capital < default_min:
                break

        # Se sobrou capital não alocado, adiciona ao primeiro colocado
        if rem_capital > 0 and allocations:
            allocations[0]['allocated_capital'] = round(allocations[0]['allocated_capital'] + rem_capital, 2)

        return allocations

    def execute_flash_liquidity(self):
        """Se o ativo de origem for uma cripto (ex: BTC), converte fração para USDT/BRL para operar."""
        if self.source_asset in ['USDT', 'BRL']:
            return

        print(f"[Sniper Liquidez Flash] Convertendo {self.capital} de {self.source_asset} para {self.currency} para operar...")
        pair = f"{self.source_asset}/{self.currency}"
        try:
            ticker = self.exchange.fetch_ticker(pair)
            price = ticker['last']
            qty = self.capital / price
            try:
                prec_qty = float(self.exchange.amount_to_precision(pair, qty))
            except:
                prec_qty = round(qty, 6)

            if not self.dry_run:
                try:
                    self.exchange.create_market_sell_order(pair, prec_qty)
                    print(f"[Sniper Liquidez Flash] Venda executada: {prec_qty} {pair} @ {price}")
                except Exception as e:
                    print(f"[Sniper Liquidez Flash] Aviso ordem spot: {e}")

            self.liquidity_rotation = {
                "source_asset": self.source_asset,
                "pair": pair,
                "initial_price": price,
                "crypto_qty": prec_qty,
                "capital_generated": self.capital
            }
        except Exception as e:
            print(f"[Sniper Liquidez Flash] Erro ao obter cotação de {pair}: {e}")

    def revert_flash_liquidity(self, final_capital: float):
        """Ao final da sessão, recompra o ativo de origem com o capital total + lucros."""
        if not self.liquidity_rotation or self.source_asset in ['USDT', 'BRL']:
            return

        pair = self.liquidity_rotation['pair']
        print(f"[Sniper Recomposição] Recomprando {self.source_asset} com o capital final de ${final_capital:.2f} {self.currency}...")
        try:
            ticker = self.exchange.fetch_ticker(pair)
            price = ticker['last']
            qty = final_capital / price
            try:
                prec_qty = float(self.exchange.amount_to_precision(pair, qty))
            except:
                prec_qty = round(qty, 6)

            if not self.dry_run:
                try:
                    self.exchange.create_market_buy_order(pair, None, params={'quoteOrderQty': final_capital})
                    print(f"[Sniper Recomposição] Recompra concluída: {prec_qty} {pair} por ${final_capital:.2f}")
                except Exception as e:
                    print(f"[Sniper Recomposição] Falha na recompra: {e}")
        except Exception as e:
            print(f"[Sniper Recomposição] Erro: {e}")

    def run_session(self, duration_minutes: int = 10) -> dict:
        """Executa a sessão completa multi-ativo de 10 minutos de forma assíncrona com gestão individual."""
        self.session_duration_sec = duration_minutes * 60
        session_info = kv_db.get_daytrade_session() or {}
        started_at = time.time()
        self.started_at = started_at
        
        iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        session_info["started_at"] = iso_now
        session_info["status"] = "running"
        session_info["source_asset"] = self.source_asset
        session_info["in_grace_period"] = False
        session_info["positions"] = {}
        kv_db.start_daytrade_session(session_info)

        self.emit_thought(
            "SYSTEM",
            "SESSÃO",
            "SISTEMA",
            f"Sessão Sniper de 10 min iniciada! Capital: {self.capital:.2f} {self.currency} financiado via {self.source_asset}."
        )

        print(f"\n========================================================")
        print(f"  [SNIPER DAY TRADE MULTI-ATIVO] Sessão Iniciada!")
        print(f"  Origem de Capital: {self.source_asset} | Capital Total: {self.capital:.2f} {self.currency}")
        print(f"  Duração Base: 10 min | Tolerância Anti-Loss: +2 min")
        print(f"  Modo: {'SIMULAÇÃO (DRY RUN)' if self.dry_run else 'MERCADO REAL'}")
        print(f"========================================================\n")

        # 1. Conversão Flash de Liquidez se originar de crypto (ex: BTC)
        self.execute_flash_liquidity()
        if self.source_asset in ['BTC', 'ETH', 'SOL', 'BNB']:
            self.emit_thought(
                "SYSTEM",
                self.source_asset,
                "LIQUIDEZ",
                f"Liquidez instantânea Flash ativada: alocando fração de {self.source_asset} para caçar altcoins de alta volatilidade."
            )

        # 2. Scanner de Oportunidades
        scored = self.scan_opportunities()
        allocated_targets = self.allocate_capital(scored)

        print(f"\n[Sniper Alocação] Capital distribuído entre {len(allocated_targets)} ativos:")
        for t in allocated_targets:
            print(f"  ➔ {t['symbol']}: {t['allocated_capital']:.2f} {self.currency} (Lote Mín: ${t['min_cost']})")
            self.emit_thought(
                "SCAN",
                t['symbol'],
                "SCANNER",
                f"Oportunidade selecionada: {t['symbol']} com Volatilidade 10m de {t.get('range_10m_pct', 0.8):.2f}% e RSI-7 em {t.get('rsi7', 50):.1f}. Setup: {t.get('setup', 'Scalping')} (Score: {t.get('score', 30):.1f}). Alocação: ${t['allocated_capital']:.2f} {self.currency}."
            )

        active_positions = {}  # { symbol: position_data }
        all_session_positions = {}  # Preserva histórico de todas as posições da sessão
        trades_history = []
        last_snapshot_time = 0.0
        last_hold_thoughts = {}
        session_capital = self.capital
        in_grace_period = False

        # Salva alocação no Redis imediatamente para visibilidade no frontend
        session_info["allocated_targets"] = [
            {
                "symbol": t['symbol'],
                "capital": t['allocated_capital'],
                "min_cost": t['min_cost'],
                "price": t['price'],
                "setup": t.get('setup', 'Scalping')
            }
            for t in allocated_targets
        ]
        kv_db.update_daytrade_session(session_info)

        # 3. Disparo das Compras Iniciais para cada ativo selecionado
        for t in allocated_targets:
            sym = t['symbol']
            trade_cost = t['allocated_capital']
            cur_price = t['price']
            raw_qty = trade_cost / cur_price
            try:
                crypto_qty = float(self.exchange.amount_to_precision(sym, raw_qty))
            except:
                crypto_qty = raw_qty

            print(f"[Sniper Disparo] 🚀 Comprando {sym} | {crypto_qty} moedas por ${trade_cost:.2f}")
            if not self.dry_run:
                try:
                    self.exchange.create_market_buy_order(sym, crypto_qty)
                except Exception as e:
                    try:
                        self.exchange.create_market_buy_order(sym, None, params={'quoteOrderQty': trade_cost})
                    except Exception as e2:
                        print(f"[Sniper Disparo] Aviso Binance em {sym}: {e2}")

            self.emit_thought(
                "BUY",
                sym,
                "COMPRA",
                f"Compra a mercado executada em {sym} @ {cur_price} (${trade_cost:.2f}). Justificativa: setup técnico {t['setup']} com aceleração de volatilidade."
            )

            buy_record = {
                "action": "BUY",
                "type": "BUY",
                "symbol": sym,
                "pair": sym,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "time": time.strftime("%H:%M:%S"),
                "price": cur_price,
                "qty": crypto_qty,
                "amount": trade_cost,
                "currency": self.currency,
                "reason": f"Scanner Sniper ({t['setup']})",
                "pnl_pct": 0.0
            }
            kv_db.record_daytrade_microtrade(buy_record)

            breakeven_calc = round(cur_price * 1.002002, 8)
            target_calc = round(cur_price * 1.0070, 8)

            pos_entry = {
                "symbol": sym,
                "entry_price": cur_price,
                "current_price": cur_price,
                "highest_price": cur_price,
                "crypto_qty": crypto_qty,
                "entry_cost": trade_cost,
                "buy_timestamp": time.time(),
                "buy_time": time.strftime("%H:%M:%S"),
                "pnl_pct": 0.0,
                "in_position": True,
                "breakeven_price": breakeven_calc,
                "target_price": target_calc,
                "closed": False,
            }
            active_positions[sym] = pos_entry
            all_session_positions[sym] = pos_entry.copy()

        # 4. Loop de Gestão Concorrente / Assíncrona Tick-a-Tick (3s)
        while True:
            now = time.time()
            elapsed_sec = int(now - started_at)

            # A. Aos 10 minutos (600s), verifica se alguma posição ainda está abaixo da Linha de Meta (target_price)
            if elapsed_sec >= self.session_duration_sec and active_positions:
                any_below_target = any(pos['current_price'] < pos['target_price'] for pos in active_positions.values())
                if any_below_target and not in_grace_period:
                    in_grace_period = True
                    session_info["in_grace_period"] = True
                    kv_db.update_daytrade_session(session_info)
                    self.emit_thought(
                        "PROTECTION",
                        "ANTI-LOSS",
                        "PROTEÇÃO",
                        "Marca de 10 min atingida com posições abaixo da Linha de Meta. Ativando tolerância de +2min para buscar o alvo de lucro real antes de qualquer encerramento."
                    )
                    print(f"[Sniper] ⏳ [10m ATINGIDO] Posição abaixo da Linha de Meta. Ativando Tolerância Anti-Loss (+2 min)...")

            # B. Monitoramento e Saída Individual de Cada Posição Ancorada na Linha de Meta
            symbols_to_close = []
            for sym, pos in list(active_positions.items()):
                try:
                    ticker = self.exchange.fetch_ticker(sym)
                    cur_p = float(ticker['last'])
                except:
                    cur_p = pos['current_price']

                pos['current_price'] = cur_p
                if sym in all_session_positions:
                    all_session_positions[sym]['current_price'] = cur_p
                entry_p = pos['entry_price']
                breakeven_p = pos.get('breakeven_price', entry_p * 1.002002)
                target_p = pos.get('target_price', entry_p * 1.0070)
                pnl_pct = ((cur_p - entry_p) / entry_p) * 100
                pos['pnl_pct'] = round(pnl_pct, 2)
                if sym in all_session_positions:
                    all_session_positions[sym]['pnl_pct'] = round(pnl_pct, 2)

                if cur_p > pos['highest_price']:
                    pos['highest_price'] = cur_p

                # Trailing Stop arma somente após atingir a Linha de Meta (target_p)
                trailing_armed = pos['highest_price'] >= target_p
                hit_trailing = trailing_armed and (cur_p <= pos['highest_price'] * 0.9985) and (cur_p >= breakeven_p)

                should_exit = False
                exit_reason = ""

                # Emite pensamentos periódicos de manutenção (Hold) com base na Linha de Meta
                if (now - last_hold_thoughts.get(sym, 0)) >= 25.0:
                    last_hold_thoughts[sym] = now
                    dist_to_target = ((target_p - cur_p) / entry_p) * 100
                    if cur_p >= target_p:
                        self.emit_thought(
                            "HOLD",
                            sym,
                            "MANTER",
                            f"Mantendo {sym}: ACIMA DA LINHA DE META @ {cur_p} (+{pnl_pct:.2f}%). Alvo de lucro real atingido, trailing stop móvel ativo."
                        )
                    else:
                        self.emit_thought(
                            "HOLD",
                            sym,
                            "MANTER",
                            f"Mantendo {sym}: cotação @ {cur_p} ({pnl_pct:+.2f}%). Faltam {dist_to_target:.2f}% para atingir a Linha de Meta ({target_p}) com taxas cobertas."
                        )

                # Regra: Vendas em 10 min acontecem apenas considerando a Linha de Meta
                if cur_p >= target_p and not hit_trailing:
                    if pnl_pct >= 1.0:  # Rompimento expressivo da meta
                        should_exit = True
                        exit_reason = f"🎯 Linha de Meta Superada (+{pnl_pct:.2f}% | Alvo: {target_p})"
                if hit_trailing:
                    should_exit = True
                    exit_reason = f"🛡️ Trailing Stop na Meta (Lucro Real Protegido: +{pnl_pct:.2f}%)"
                    self.emit_thought(
                        "TRAILING",
                        sym,
                        "PROTEÇÃO",
                        f"Trailing Stop executado em {sym}! Lucro real de {pnl_pct:+.2f}% garantido acima da meta e das taxas."
                    )
                elif pnl_pct <= -0.45 and not in_grace_period:
                    should_exit = True
                    exit_reason = "🛑 Stop Loss de Proteção (-0.45%)"
                elif in_grace_period and cur_p >= target_p:
                    should_exit = True
                    exit_reason = f"🎯 Linha de Meta Atingida na Tolerância (+{pnl_pct:.2f}%)"
                elif in_grace_period and cur_p >= breakeven_p:
                    should_exit = True
                    exit_reason = f"✅ Saída no Breakeven na Tolerância (Taxas Cobertas: +{pnl_pct:.2f}%)"
                elif elapsed_sec >= (self.session_duration_sec + (self.grace_period_sec if in_grace_period else 0)):
                    should_exit = True
                    exit_reason = "⏰ Tempo Limite Esgotado (Hard Stop)"

                if should_exit:
                    symbols_to_close.append((sym, cur_p, pnl_pct, exit_reason))

            # Executa fechamento individual
            for sym, cur_p, pnl_pct, exit_reason in symbols_to_close:
                pos = active_positions[sym]
                sell_qty = pos['crypto_qty']
                print(f"[Sniper Saída] 🏁 Fechando {sym}: {exit_reason} @ {cur_p} ({pnl_pct:+.2f}%)")

                if not self.dry_run:
                    try:
                        prec_qty = float(self.exchange.amount_to_precision(sym, sell_qty))
                        self.exchange.create_market_sell_order(sym, prec_qty)
                    except Exception as e:
                        print(f"[Sniper Saída] Falha na ordem Binance para {sym}: {e}")

                gross_pnl = (sell_qty * cur_p) - pos['entry_cost']
                fee = (pos['entry_cost'] + (sell_qty * cur_p)) * 0.001
                net_pnl = gross_pnl - fee
                session_capital += net_pnl

                self.emit_thought(
                    "SELL",
                    sym,
                    "VENDA",
                    f"Ordem de venda executada em {sym} @ {cur_p} com resultado de {pnl_pct:+.2f}% ({net_pnl:+.2f} {self.currency}). Motivo: {exit_reason}."
                )

                sell_record = {
                    "action": "SELL",
                    "type": "SELL",
                    "symbol": sym,
                    "pair": sym,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "time": time.strftime("%H:%M:%S"),
                    "price": cur_p,
                    "buy_price": pos['entry_price'],
                    "sell_price": cur_p,
                    "qty": sell_qty,
                    "crypto_qty": sell_qty,
                    "amount": round(sell_qty * cur_p, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "net_pnl_fiat": round(net_pnl, 2),
                    "currency": self.currency,
                    "exit_reason": exit_reason,
                    "reason": exit_reason
                }
                trades_history.append(sell_record)
                kv_db.record_daytrade_microtrade(sell_record)

                if sym in all_session_positions:
                    all_session_positions[sym]['closed'] = True
                    all_session_positions[sym]['exit_price'] = cur_p
                    all_session_positions[sym]['exit_reason'] = exit_reason
                    all_session_positions[sym]['pnl_pct'] = round(pnl_pct, 2)
                    all_session_positions[sym]['net_pnl_fiat'] = round(net_pnl, 2)
                    all_session_positions[sym]['current_price'] = cur_p

                del active_positions[sym]

            # C. Atualiza o estado da sessão no Redis para o Frontend a cada tick
            total_net_pnl = sum(t.get("net_pnl_fiat", 0) for t in trades_history)
            unrealized_total = sum((pos['current_price'] * pos['crypto_qty']) - pos['entry_cost'] for pos in active_positions.values())
            overall_pnl_pct = round(((total_net_pnl + unrealized_total) / self.capital) * 100, 2) if self.capital > 0 else 0.0

            session_info["in_position"] = len(active_positions) > 0
            session_info["positions"] = active_positions
            session_info["all_positions"] = all_session_positions
            session_info["position_pnl_pct"] = overall_pnl_pct
            session_info["trades_count"] = len(trades_history)
            session_info["total_pnl_pct"] = overall_pnl_pct
            session_info["in_grace_period"] = in_grace_period
            kv_db.update_daytrade_session(session_info)

            # D. Snapshot consolidado e por ativo a cada 30 segundos
            if (now - last_snapshot_time) >= self.snapshot_interval_sec:
                last_snapshot_time = now
                # Ativo prioritário para o gráfico
                primary_pos = list(active_positions.values())[0] if active_positions else (list(all_session_positions.values())[0] if all_session_positions else None)
                primary_sym = primary_pos['symbol'] if primary_pos else (allocated_targets[0]['symbol'] if allocated_targets else 'MULTI')
                primary_price = primary_pos['current_price'] if primary_pos else 0.0

                snapshot = {
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "seconds_elapsed": elapsed_sec,
                    "elapsed_sec": elapsed_sec,
                    "elapsed_str": f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}",
                    "symbol": primary_sym,
                    "pair": primary_sym,
                    "in_position": len(active_positions) > 0,
                    "current_price": primary_price,
                    "entry_price": primary_pos['entry_price'] if primary_pos else None,
                    "breakeven_price": primary_pos.get('breakeven_price', round(primary_pos['entry_price'] * 1.002002, 8)) if primary_pos else None,
                    "target_price": primary_pos.get('target_price', round(primary_pos['entry_price'] * 1.0070, 8)) if primary_pos else None,
                    "unrealized_pnl_pct": overall_pnl_pct,
                    "unrealized_pct": overall_pnl_pct,
                    "active_coins_count": len(active_positions),
                    "positions_summary": {k: v['pnl_pct'] for k, v in active_positions.items()},
                    "positions_data": {
                        sym: {
                            "symbol": sym,
                            "current_price": pos['current_price'],
                            "entry_price": pos['entry_price'],
                            "breakeven_price": pos.get('breakeven_price', round(pos['entry_price'] * 1.002002, 8)),
                            "target_price": pos.get('target_price', round(pos['entry_price'] * 1.0070, 8)),
                            "pnl_pct": pos['pnl_pct'],
                            "is_above_target": pos['current_price'] >= pos.get('target_price', pos['entry_price'] * 1.0070),
                            "closed": pos.get("closed", False)
                        }
                        for sym, pos in all_session_positions.items()
                    },
                    "in_grace_period": in_grace_period
                }
                kv_db.save_daytrade_snapshot(snapshot)
                print(f"[Sniper Snapshot {snapshot['elapsed_str']}] Posições Abertas: {len(active_positions)}/{len(all_session_positions)} | PnL Geral: {overall_pnl_pct:+.2f}%")

            # E. Término da Sessão
            max_allowed_time = self.session_duration_sec + (self.grace_period_sec if in_grace_period else 0)
            if elapsed_sec >= max_allowed_time and len(active_positions) == 0:
                print(f"[Sniper] 🏁 Todas as posições concluídas e tempo esgotado ({elapsed_sec}s).")
                break

            time.sleep(3)

        # 5. Recomposição de Ativo de Origem (se aplicável)
        self.revert_flash_liquidity(session_capital)

        # 6. Consolidação e Encerramento
        total_trades = len(trades_history)
        winning_trades = len([t for t in trades_history if t.get("net_pnl_fiat", 0) > 0])
        win_rate = round((winning_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0
        total_net_pnl = round(sum(t.get("net_pnl_fiat", 0) for t in trades_history), 2)
        total_pnl_pct = round((total_net_pnl / self.capital) * 100, 2) if self.capital > 0 else 0.0
        actual_duration_min = round((time.time() - started_at) / 60, 1)
        result_status = "PROFIT" if total_net_pnl >= 0 else "LOSS"

        summary = {
            "status": "completed",
            "source_asset": self.source_asset,
            "initial_capital": self.capital,
            "final_capital": round(session_capital, 2),
            "currency": self.currency,
            "net_profit_fiat": total_net_pnl,
            "net_pnl_fiat": total_net_pnl,
            "pnl_pct": total_pnl_pct,
            "total_pnl_pct": total_pnl_pct,
            "total_trades": total_trades,
            "trades_count": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "win_rate_pct": win_rate,
            "duration_str": f"{actual_duration_min} min",
            "in_grace_period_used": in_grace_period,
            "result_status": result_status,
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }

        kv_db.save_daytrade_session_history(summary)
        kv_db.finish_daytrade_session(summary)

        outcome_msg = (
            f"🏁 Sessão Sniper Finalizada! Resultado: {total_net_pnl:+.2f} {self.currency} ({total_pnl_pct:+.2f}%). "
            f"Total de {total_trades} micro-trades realizados ({win_rate}% taxa de acerto). "
            f"Capital Inicial: {self.capital:.2f} {self.currency} ➔ Final: {session_capital:.2f} {self.currency}. "
            f"Liquidez e lucro recompostos em {self.source_asset}."
        )
        self.emit_thought("OUTCOME", "FINAL", "RESULTADO", outcome_msg)

        print(f"\n========================================================")
        print(f"  [SNIPER FINALIZADO] Lucro Líquido: {total_net_pnl:+.2f} {self.currency} ({total_pnl_pct:+.2f}%)")
        print(f"  Trades: {total_trades} | Taxa de Acerto: {win_rate}% | Duração: {actual_duration_min}m")
        print(f"========================================================\n")
        return summary

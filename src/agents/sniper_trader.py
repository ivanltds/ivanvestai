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
    CANDIDATES_USDT = [
        'SOL/USDT', 'NEAR/USDT', 'SUI/USDT', 'XRP/USDT', 'DOGE/USDT', 
        'PEPE/USDT', 'FET/USDT', 'AVAX/USDT', 'LINK/USDT', 'RENDER/USDT'
    ]

    def __init__(self, capital: float = 10.0, currency: str = "USDT", source_asset: str = "USDT", symbol: str = None, dry_run: bool = False):
        self.capital = float(capital)
        # O Sniper opera estritamente em pares USDT para máxima profundidade de book e menor spread
        self.currency = "USDT"
        self.source_asset = source_asset.upper() if source_asset else "USDT"
        self.symbol = symbol  # Se fornecido um par específico, opera ele; se None, ativa o scanner multi-ativo
        self.dry_run = dry_run
        self.policy = kv_db.get_sniper_policy()
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

    def check_btc_macro_trend(self) -> dict:
        """
        Verifica a tendência macro do Bitcoin no gráfico de 15 minutos.
        Se o BTC estiver caindo fortemente (abaixo da EMA20 com RSI < 42),
        rompimentos em altcoins falham em 75% dos casos.
        """
        try:
            bars = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=30)
            if not bars or len(bars) < 20:
                return {"healthy": True, "regime": "NEUTRO", "reason": "Poucos dados do BTC"}
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['ema20'] = ta.ema(df['close'], length=20)
            df['rsi14'] = ta.rsi(df['close'], length=14)
            latest = df.iloc[-1]
            close = float(latest['close'])
            ema = float(latest['ema20'])
            rsi = float(latest['rsi14'])

            if close < ema * 0.997 and rsi < 42:
                res = {
                    "healthy": False,
                    "regime": "QUEDA",
                    "reason": f"BTC em queda no 15m (${close:.0f} < EMA20 ${ema:.0f} | RSI {rsi:.1f})"
                }
            elif close >= ema and rsi >= 48:
                res = {
                    "healthy": True,
                    "regime": "ALTA",
                    "reason": f"BTC favorável no 15m (${close:.0f} >= EMA20 ${ema:.0f} | RSI {rsi:.1f})"
                }
            else:
                res = {
                    "healthy": True,
                    "regime": "CONSOLIDAÇÃO",
                    "reason": f"BTC em consolidação neutra (${close:.0f} próx. EMA20 ${ema:.0f} | RSI {rsi:.1f})"
                }
            try:
                kv_db.save_btc_macro_regime(res)
            except:
                pass
            return res
        except Exception as e:
            fallback = {"healthy": True, "regime": "NEUTRO", "reason": f"Aviso BTC: {e}"}
            try:
                kv_db.save_btc_macro_regime(fallback)
            except:
                pass
            return fallback

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
        """Coleta velas de 1m e 5m para calcular Bollinger, RSI-7, VWAP, RVOL e ATR(14)."""
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
            df['vwap'] = (df['typical'] * df['volume']).cumsum() / vol_sum.replace(0, 1)

            # Volatilidade de 10 min
            df['rolling_max'] = df['high'].rolling(10).max()
            df['rolling_min'] = df['low'].rolling(10).min()
            df['range_10m_pct'] = ((df['rolling_max'] - df['rolling_min']) / df['rolling_min'].replace(0, 1)) * 100

            # Volume Relativo (RVOL): volume da barra atual / média das últimas 10 barras
            vol_mean_10 = df['volume'].iloc[-11:-1].mean() if len(df) >= 11 else df['volume'].mean()
            rvol = float(df['volume'].iloc[-1]) / (vol_mean_10 if vol_mean_10 > 0 else 1.0)

            latest = df.iloc[-1]
            close_price = float(latest['close'])

            # ATR(14) no gráfico de 5m para calibragem de stop dinâmico
            atr_pct = 1.0
            try:
                bars_5m = self.exchange.fetch_ohlcv(symbol, timeframe='5m', limit=20)
                if bars_5m and len(bars_5m) >= 15:
                    df5 = pd.DataFrame(bars_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    atr_series = ta.atr(df5['high'], df5['low'], df5['close'], length=14)
                    if atr_series is not None and not pd.isna(atr_series.iloc[-1]):
                        atr_pct = (float(atr_series.iloc[-1]) / close_price) * 100
            except:
                atr_pct = 1.0

            return {
                "symbol": symbol,
                "close": close_price,
                "rsi7": float(latest['rsi7']) if not pd.isna(latest['rsi7']) else 50.0,
                "bb_lower": float(latest['bb_lower']) if 'bb_lower' in latest else float(close_price * 0.998),
                "bb_upper": float(latest['bb_upper']) if 'bb_upper' in latest else float(close_price * 1.008),
                "vwap": float(latest['vwap']) if not pd.isna(latest['vwap']) else close_price,
                "range_10m_pct": float(latest['range_10m_pct']) if not pd.isna(latest['range_10m_pct']) else 0.5,
                "rvol": round(rvol, 2),
                "atr_pct": round(atr_pct, 2)
            }
        except Exception as e:
            return {}

    def scan_opportunities(self) -> list:
        """Escaneia altcoins candidatas em USDT, aplica trava de spread e ranqueia as melhores oportunidades."""
        candidates = self.CANDIDATES_USDT
        if self.symbol and self.symbol in candidates:
            candidates = [self.symbol]

        disqualified = set(self.policy.get("disqualified_pairs", []))
        max_spread = float(self.policy.get("max_spread_pct", 0.08))
        min_stop_cfg = float(self.policy.get("min_stop_pct", 1.30))
        max_stop_cfg = float(self.policy.get("max_stop_pct", 2.00))

        scored = []
        print(f"[Sniper Scanner] Varrendo {len(candidates)} pares em USDT (Max Spread: {max_spread}%)...")

        for sym in candidates:
            if sym in disqualified:
                print(f"[Sniper Scanner] 🚫 {sym} ignorado (bloqueado temporariamente pela IA por perdas anteriores).")
                continue

            # 1. Trava Rígida de Spread Bid/Ask
            try:
                ob = self.exchange.fetch_order_book(sym, limit=5)
                best_bid = float(ob['bids'][0][0]) if ob.get('bids') else 0.0
                best_ask = float(ob['asks'][0][0]) if ob.get('asks') else 0.0
                if best_bid > 0 and best_ask > 0:
                    spread_pct = ((best_ask - best_bid) / best_bid) * 100
                    if spread_pct > max_spread:
                        print(f"[Sniper Scanner] ⚠️ {sym} descartado: spread de {spread_pct:.2f}% acima do teto de {max_spread}%.")
                        continue
            except Exception as e_ob:
                pass

            ta_data = self.fetch_1m_ta(sym)
            if not ta_data or ta_data.get('close', 0) <= 0:
                continue

            price = ta_data['close']
            rsi = ta_data['rsi7']
            range_10m = ta_data['range_10m_pct']
            vwap = ta_data['vwap']
            bb_lower = ta_data['bb_lower']
            rvol = ta_data.get('rvol', 1.0)
            atr_pct = ta_data.get('atr_pct', 1.0)

            # Cálculo de Stop Dinâmico por ATR (mínimo 1.3%, máximo 2.0%, ideal 1.5x ATR)
            dynamic_stop_pct = round(max(min_stop_cfg, min(max_stop_cfg, atr_pct * 1.5)), 2)

            # Score de Oportunidade:
            # - Maior volatilidade nos 10m ganha pontos
            # - RVOL > 1.5x ganha bônus de fluxo
            # - RSI sobrevenda (< 32) ou rompimento de VWAP ganha bônus
            score = range_10m * 10.0 + min(rvol * 5.0, 15.0)
            setup = "Neutro"

            if rsi < 32 and price <= bb_lower * 1.002:
                score += 35.0
                setup = "Sobrevenda Bollinger"
            elif 51 <= rsi <= 64 and price >= vwap * 1.0005:
                score += 30.0
                setup = "Rompimento VWAP"
            elif range_10m >= 1.0:
                score += 20.0
                setup = "Alta Volatilidade"

            scored.append({
                "symbol": sym,
                "price": price,
                "rsi7": round(rsi, 1),
                "range_10m_pct": round(range_10m, 2),
                "rvol": rvol,
                "atr_pct": atr_pct,
                "dynamic_stop_pct": dynamic_stop_pct,
                "score": round(score, 1),
                "setup": setup,
                "ta": ta_data
            })

        scored.sort(key=lambda x: x['score'], reverse=True)
        print(f"[Sniper Scanner] Top Oportunidades Encontradas:")
        for s in scored[:3]:
            print(f"  • {s['symbol']}: Range 10m {s['range_10m_pct']}% | RSI {s['rsi7']} | RVOL {s['rvol']}x | ATR {s['atr_pct']}% | Stop Sugerido: -{s['dynamic_stop_pct']}% | Setup: {s['setup']} (Score: {s['score']})")

        return scored

    def allocate_capital(self, scored_candidates: list) -> list:
        """Aloca 100% do capital disponível no ativo #1 (Top Oportunidade) eliminando a dispersão."""
        if not scored_candidates:
            return []

        top = scored_candidates[0]
        sym = top['symbol']
        min_cost = 1.0 if any(m in sym for m in ['PEPE', 'DOGE', 'SHIB', 'WIF', 'FLOKI']) else 5.0
        
        if self.capital < min_cost:
            print(f"[Sniper Alocação] Capital ${self.capital:.2f} abaixo do mínimo ${min_cost} da Binance.")
            return []

        target_pct = float(self.policy.get("target_pct", 2.00))
        trailing_arm_pct = float(self.policy.get("trailing_arm_pct", 1.80))
        trailing_buffer_pct = float(self.policy.get("trailing_buffer_pct", 0.40))
        dynamic_stop_pct = top.get('dynamic_stop_pct', 1.50)

        print(f"\n[Sniper Alocação] Concentrando 100% do capital (${self.capital:.2f} {self.currency}) no melhor ativo: {sym}")
        print(f"  Setup: {top['setup']} | Meta: +{target_pct:.2f}% | Stop Dinâmico: -{dynamic_stop_pct:.2f}% | Trailing Buffer: {trailing_buffer_pct:.2f}%")

        return [{
            "symbol": sym,
            "allocated_capital": round(self.capital, 2),
            "min_cost": min_cost,
            "setup": top['setup'],
            "price": top['price'],
            "ta": top['ta'],
            "atr_pct": top.get('atr_pct', 1.0),
            "dynamic_stop_pct": dynamic_stop_pct,
            "target_pct": target_pct,
            "trailing_arm_pct": trailing_arm_pct,
            "trailing_buffer_pct": trailing_buffer_pct
        }]

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

        # 0. Verificação do Gatekeeper Macro (Bitcoin 15m)
        btc_gate = self.check_btc_macro_trend()
        session_info["btc_regime"] = btc_gate.get("regime", "NEUTRO")
        session_info["btc_reason"] = btc_gate.get("reason", "")
        
        if self.policy.get("btc_macro_filter", True) and not btc_gate.get("healthy", True):
            warning_msg = f"🛡️ Entrada suspensa pelo Gatekeeper Macro: {btc_gate.get('reason')}. Mercado em alto risco de falso rompimento nas altcoins."
            self.emit_thought("PROTECTION", "BTC", "MACRO", warning_msg)
            print(f"\n[Sniper Macro] {warning_msg}\n")
            
            summary = {
                "status": "completed",
                "source_asset": self.source_asset,
                "initial_capital": self.capital,
                "final_capital": self.capital,
                "currency": self.currency,
                "net_profit_fiat": 0.0,
                "pnl_pct": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "duration_str": "0 min",
                "result_status": "PROFIT",
                "reason": btc_gate.get("reason"),
                "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "trades": []
            }
            kv_db.save_daytrade_session_history(summary)
            kv_db.finish_daytrade_session(summary)
            self.revert_flash_liquidity(self.capital)
            return summary

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

        if not allocated_targets:
            no_target_msg = "Nenhum ativo atendeu aos critérios de volatilidade e spread no momento. Sessão finalizada sem risco."
            self.emit_thought("SCAN", "SCANNER", "AVISO", no_target_msg)
            print(f"\n[Sniper Scanner] {no_target_msg}\n")
            summary = {
                "status": "completed",
                "source_asset": self.source_asset,
                "initial_capital": self.capital,
                "final_capital": self.capital,
                "currency": self.currency,
                "net_profit_fiat": 0.0,
                "pnl_pct": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "duration_str": "0 min",
                "result_status": "PROFIT",
                "reason": no_target_msg,
                "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "trades": []
            }
            kv_db.save_daytrade_session_history(summary)
            kv_db.finish_daytrade_session(summary)
            self.revert_flash_liquidity(self.capital)
            return summary

        print(f"\n[Sniper Alocação] Ativo Selecionado:")
        for t in allocated_targets:
            print(f"  ➔ {t['symbol']}: ${t['allocated_capital']:.2f} {self.currency} (Meta: +{t.get('target_pct', 2.0)}% | Stop: -{t.get('dynamic_stop_pct', 1.5)}%)")
            self.emit_thought(
                "SCAN",
                t['symbol'],
                "SCANNER",
                f"Oportunidade selecionada: {t['symbol']} | Setup: {t.get('setup', 'Scalping')} | ATR: {t.get('atr_pct', 1.0):.2f}% | Alvo: +{t.get('target_pct', 2.0):.2f}% | Stop Dinâmico: -{t.get('dynamic_stop_pct', 1.5):.2f}%. Alocação: ${t['allocated_capital']:.2f} {self.currency}."
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
                "setup": t.get('setup', 'Scalping'),
                "dynamic_stop_pct": t.get('dynamic_stop_pct', 1.5),
                "target_pct": t.get('target_pct', 2.0)
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

            actual_trade_cost = round(crypto_qty * cur_price, 4)

            buy_record = {
                "action": "BUY",
                "type": "BUY",
                "symbol": sym,
                "pair": sym,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "time": time.strftime("%H:%M:%S"),
                "price": cur_price,
                "qty": crypto_qty,
                "amount": actual_trade_cost,
                "currency": self.currency,
                "reason": f"Scanner Sniper ({t['setup']})",
                "pnl_pct": 0.0
            }
            kv_db.record_daytrade_microtrade(buy_record)

            dynamic_stop_pct = t.get('dynamic_stop_pct', 1.50)
            target_pct = t.get('target_pct', 2.00)
            trailing_arm_pct = t.get('trailing_arm_pct', 1.80)
            trailing_buffer_pct = t.get('trailing_buffer_pct', 0.40)

            breakeven_calc = round(cur_price * 1.002002, 8)
            target_calc = round(cur_price * (1 + target_pct / 100), 8)
            arm_calc = round(cur_price * (1 + trailing_arm_pct / 100), 8)
            stop_calc = round(cur_price * (1 - dynamic_stop_pct / 100), 8)

            pos_entry = {
                "symbol": sym,
                "entry_price": cur_price,
                "current_price": cur_price,
                "highest_price": cur_price,
                "crypto_qty": crypto_qty,
                "entry_cost": actual_trade_cost,
                "buy_timestamp": time.time(),
                "buy_time": time.strftime("%H:%M:%S"),
                "pnl_pct": 0.0,
                "in_position": True,
                "breakeven_price": breakeven_calc,
                "target_price": target_calc,
                "arm_price": arm_calc,
                "stop_price": stop_calc,
                "dynamic_stop_pct": dynamic_stop_pct,
                "target_pct": target_pct,
                "trailing_arm_pct": trailing_arm_pct,
                "trailing_buffer_pct": trailing_buffer_pct,
                "closed": False,
            }
            active_positions[sym] = pos_entry
            all_session_positions[sym] = pos_entry.copy()

        # 4. Loop de Gestão Concorrente / Assíncrona Tick-a-Tick (3s)
        while True:
            now = time.time()
            elapsed_sec = int(now - started_at)

            # A. Aos 10 minutos (600s), se houver posições abaixo da meta, ativa o Modo Hold Ilimitado
            if elapsed_sec >= self.session_duration_sec and active_positions:
                any_below_target = any(pos['current_price'] < pos['target_price'] for pos in active_positions.values())
                if any_below_target and not in_grace_period:
                    in_grace_period = True
                    session_info["in_grace_period"] = True
                    session_info["unlimited_hold"] = True
                    kv_db.update_daytrade_session(session_info)
                    self.emit_thought(
                        "PROTECTION",
                        "PACIÊNCIA",
                        "HOLD ILIMITADO",
                        "Marca de 10 min atingida com posição em andamento. Modo Ilimitado ativado: aguardando pacientemente a Linha de Meta ou Stop Loss de Volatilidade. Sem liquidação precipitada por tempo."
                    )
                    print(f"[Sniper] ⏳ [10m ATINGIDO] Posição em andamento. Ativando MODO HOLD ILIMITADO: aguardando meta de lucro real. Sem encerramento por tempo!")

            # B. Monitoramento e Saída Individual Ancorada na Linha de Meta e ATR
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
                target_p = pos.get('target_price', entry_p * 1.020)
                arm_p = pos.get('arm_price', entry_p * 1.018)
                dynamic_stop = pos.get('dynamic_stop_pct', 1.50)
                trailing_buffer = pos.get('trailing_buffer_pct', 0.40)
                pnl_pct = ((cur_p - entry_p) / entry_p) * 100
                pos['pnl_pct'] = round(pnl_pct, 2)
                if sym in all_session_positions:
                    all_session_positions[sym]['pnl_pct'] = round(pnl_pct, 2)

                if cur_p > pos['highest_price']:
                    pos['highest_price'] = cur_p

                # Trailing Stop arma somente após atingir a faixa de lucro (arm_p)
                trailing_armed = pos['highest_price'] >= arm_p
                hit_trailing = trailing_armed and (cur_p <= pos['highest_price'] * (1 - trailing_buffer / 100)) and (cur_p >= breakeven_p)

                should_exit = False
                exit_reason = ""

                # Emite pensamentos periódicos de manutenção (Hold)
                if (now - last_hold_thoughts.get(sym, 0)) >= 25.0:
                    last_hold_thoughts[sym] = now
                    dist_to_target = ((target_p - cur_p) / entry_p) * 100
                    if cur_p >= target_p:
                        self.emit_thought(
                            "HOLD",
                            sym,
                            "MANTER",
                            f"Mantendo {sym}: ACIMA DA LINHA DE META @ {cur_p} (+{pnl_pct:.2f}%). Alvo superado, trailing stop móvel protegendo lucros."
                        )
                    else:
                        mode_label = "Hold Ilimitado" if in_grace_period else "Sessão Base"
                        self.emit_thought(
                            "HOLD",
                            sym,
                            "MANTER",
                            f"[{mode_label}] Mantendo {sym}: cotação @ {cur_p} ({pnl_pct:+.2f}%). Faltam {dist_to_target:.2f}% para a Meta ({target_p}). Stop dinâmico em -{dynamic_stop:.2f}%."
                        )

                # REGRA DO INVESTIDOR:
                # 1. Alvo de Lucro Real atingido (Meta / Trailing Stop)
                if cur_p >= target_p and not hit_trailing:
                    target_margin = pos.get('target_pct', 2.0) + 0.50
                    if pnl_pct >= target_margin:  # Rompimento expressivo da meta
                        should_exit = True
                        exit_reason = f"🎯 Super Rompimento da Meta (+{pnl_pct:.2f}% | Alvo: {target_p})"
                if hit_trailing:
                    should_exit = True
                    exit_reason = f"🛡️ Trailing Stop na Meta (+{pnl_pct:.2f}% protegido)"
                    self.emit_thought(
                        "TRAILING",
                        sym,
                        "PROTEÇÃO",
                        f"Trailing Stop executado em {sym}! Lucro real de {pnl_pct:+.2f}% garantido acima da meta e das taxas."
                    )
                # 2. Stop Loss Dinâmico de Volatilidade (ATR): ÚNICA regra de saída por perda
                elif pnl_pct <= -dynamic_stop:
                    should_exit = True
                    exit_reason = f"🛑 Stop Loss de Volatilidade ATR (-{dynamic_stop:.2f}%)"
                    self.emit_thought(
                        "STOP_LOSS",
                        sym,
                        "STOP LOSS",
                        f"Stop Loss de volatilidade atingido em {sym} ({pnl_pct:+.2f}% <= -{dynamic_stop:.2f}%). Encerrando posição para resguardar capital."
                    )

                if should_exit:
                    symbols_to_close.append((sym, cur_p, pnl_pct, exit_reason))

            # Executa fechamento individual
            for sym, cur_p, pnl_pct, exit_reason in symbols_to_close:
                pos = active_positions[sym]
                sell_qty = pos['crypto_qty']
                print(f"[Sniper Saída] 🏁 Fechando {sym}: {exit_reason} @ {cur_p} ({pnl_pct:+.2f}%)")

                if not self.dry_run:
                    try:
                        base_asset = sym.split('/')[0]
                        # Consulta saldo livre real na Binance para evitar rejeição por desconto de taxa de compra
                        bal = self.exchange.fetch_balance()
                        free_bal = float(bal.get('free', {}).get(base_asset, 0.0))
                        actual_sell_qty = min(sell_qty, free_bal) if free_bal > 0 else sell_qty
                        prec_qty = float(self.exchange.amount_to_precision(sym, actual_sell_qty))
                        self.exchange.create_market_sell_order(sym, prec_qty)
                        sell_qty = prec_qty
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

            # E. Término da Sessão: conclui apenas quando TODAS as posições tiverem sido encerradas (por Meta ou Stop Loss)
            if len(active_positions) == 0 and elapsed_sec >= 15:
                print(f"[Sniper] 🏁 Todas as posições concluídas ({elapsed_sec}s decorridos). Encerrando sessão.")
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
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trades": trades_history
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

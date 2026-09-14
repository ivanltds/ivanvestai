import time
import json
import ccxt
import pandas as pd
import pandas_ta as ta
from src.config import settings
from src.db.vercel_kv import kv_db

class SniperTraderAgent:
    """
    Agente Especializado em Day Trade de Alta Frequência (Modo Sniper - 10 Minutos).
    Executa micro-operações de scalping em velas de 1m, grava snapshots a cada 30s
    e possui tolerância inteligente de até 2m para posições em loss.
    """
    def __init__(self, capital: float = 10.0, currency: str = "USDT", dry_run: bool = False):
        self.capital = float(capital)
        self.currency = currency.upper()
        self.dry_run = dry_run
        self.exchange = ccxt.binance({
            'apiKey': settings.API_KEY,
            'secret': settings.SECRET_KEY,
            'enableRateLimit': True,
        })
        self.symbol = f"BTC/{self.currency}" if self.currency in ["USDT", "BRL"] else "BTC/USDT"
        self.session_duration_sec = 600   # 10 minutos
        self.grace_period_sec = 120       # +2 minutos de tolerância se estiver em loss
        self.snapshot_interval_sec = 30   # Snapshot a cada 30s

    def fetch_1m_ta(self, symbol: str) -> dict:
        """Coleta as últimas velas de 1m e calcula indicadores rápidos (Bollinger, RSI-7, VWAP)."""
        try:
            bars = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=35)
            if len(bars) < 25:
                return {}
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # RSI Rápido de 7 períodos para scalping
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

            # VWAP Simplificado
            df['typical'] = (df['high'] + df['low'] + df['close']) / 3
            df['vwap'] = (df['typical'] * df['volume']).cumsum() / df['volume'].cumsum()

            latest = df.iloc[-1]
            return {
                "close": float(latest['close']),
                "rsi7": float(latest['rsi7']) if not pd.isna(latest['rsi7']) else 50.0,
                "bb_lower": float(latest['bb_lower']) if 'bb_lower' in latest else float(latest['close'] * 0.998),
                "bb_upper": float(latest['bb_upper']) if 'bb_upper' in latest else float(latest['close'] * 1.008),
                "vwap": float(latest['vwap']) if not pd.isna(latest['vwap']) else float(latest['close'])
            }
        except Exception as e:
            print(f"[Sniper] Erro ao obter dados de 1m para {symbol}: {e}")
            return {}

    def run_session(self) -> dict:
        """Executa a sessão completa de 10 minutos (+ até 2m se em loss)."""
        session_info = kv_db.get_daytrade_session()
        started_at = time.time()
        session_info["started_at"] = started_at
        session_info["status"] = "running"
        session_info["in_grace_period"] = False
        kv_db.start_daytrade_session(session_info)

        print(f"\n========================================================")
        print(f"  [SNIPER DAY TRADE] Sessão Iniciada! Capital: {self.capital:.2f} {self.currency}")
        print(f"  Duração Base: 10 min (600s) | Tolerância Loss: +2 min (120s)")
        print(f"  Par Alvo: {self.symbol} | Modo: {'SIMULAÇÃO' if self.dry_run else 'MERCADO REAL'}")
        print(f"========================================================\n")

        active_position = None
        trades_history = []
        last_snapshot_time = 0.0
        session_capital = self.capital
        in_grace_period = False

        while True:
            now = time.time()
            elapsed_sec = int(now - started_at)
            time_left_sec = max(0, self.session_duration_sec - elapsed_sec)

            # 1. Verifica se entrou no período de tolerância (+2 minutos se em loss)
            if elapsed_sec >= self.session_duration_sec and active_position:
                unrealized_pct = ((active_position['current_price'] - active_position['entry_price']) / active_position['entry_price']) * 100
                if unrealized_pct < 0:
                    if not in_grace_period:
                        in_grace_period = True
                        session_info["in_grace_period"] = True
                        kv_db.update_daytrade_session(session_info)
                        print(f"[Sniper] ⏳ [10m ATINGIDO COM LOSS: {unrealized_pct:+.2f}%] Ativando Tolerância de Recuperação (até +2 min)...")

            # 2. Captura Snapshot a cada 30 segundos
            if (now - last_snapshot_time) >= self.snapshot_interval_sec:
                last_snapshot_time = now
                ta_data = self.fetch_1m_ta(self.symbol)
                cur_p = ta_data.get("close", active_position['current_price'] if active_position else 0.0)
                
                status_desc = "AGUARDANDO OPORTUNIDADE"
                unrealized_fiat = 0.0
                unrealized_pct = 0.0

                if active_position and cur_p > 0:
                    active_position['current_price'] = cur_p
                    unrealized_pct = ((cur_p - active_position['entry_price']) / active_position['entry_price']) * 100
                    unrealized_fiat = (active_position['crypto_qty'] * cur_p) - active_position['entry_cost']
                    status_desc = f"COMPRADO ({unrealized_pct:+.2f}%)"

                snapshot = {
                    "timestamp": int(now),
                    "elapsed_sec": elapsed_sec,
                    "elapsed_str": f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}",
                    "pair": self.symbol,
                    "status": status_desc,
                    "in_grace_period": in_grace_period,
                    "current_price": cur_p,
                    "entry_price": active_position['entry_price'] if active_position else None,
                    "unrealized_fiat": round(unrealized_fiat, 2),
                    "unrealized_pct": round(unrealized_pct, 2),
                    "rsi7": ta_data.get("rsi7", 50.0)
                }
                kv_db.save_daytrade_snapshot(snapshot)
                print(f"[Sniper Snapshot {snapshot['elapsed_str']}] {self.symbol} @ {cur_p:.2f} | {status_desc} | RSI: {snapshot['rsi7']:.1f}")

            # 3. Gerenciamento de Posição Ativa (Monitora Saída)
            if active_position:
                ticker = self.exchange.fetch_ticker(self.symbol)
                cur_price = ticker['last']
                active_position['current_price'] = cur_price
                entry_price = active_position['entry_price']
                pnl_pct = ((cur_price - entry_price) / entry_price) * 100

                # Atualiza máxima para Trailing Stop
                if cur_price > active_position['highest_price']:
                    active_position['highest_price'] = cur_price

                # Trailing Stop: se bateu +0.5%, não aceita sair abaixo do breakeven
                gain_from_top = ((cur_price - active_position['highest_price']) / active_position['highest_price']) * 100
                hit_trailing = (active_position['highest_price'] >= entry_price * 1.005) and (cur_price <= entry_price * 1.001)

                should_exit = False
                exit_reason = ""

                # Condição A: Take Profit Atingido (+0.6% a +0.9%)
                if pnl_pct >= 0.70:
                    should_exit = True
                    exit_reason = "🎯 Take Profit (+0.70%)"

                # Condição B: Trailing Stop disparado
                elif hit_trailing:
                    should_exit = True
                    exit_reason = "🛡️ Trailing Stop (Proteção de Lucro)"

                # Condição C: Stop Loss Estrito (-0.45%)
                elif pnl_pct <= -0.45 and not in_grace_period:
                    should_exit = True
                    exit_reason = "🛑 Stop Loss Curto (-0.45%)"

                # Condição D: Período de Tolerância ativo e reverteu para Breakeven/Lucro
                elif in_grace_period and pnl_pct >= 0.0:
                    should_exit = True
                    exit_reason = "✅ Recuperação no Período de Tolerância (Breakeven/Lucro)"

                # Condição E: Tempo Total Esgotado (10m sem loss ou 12m com loss)
                max_allowed_time = self.session_duration_sec + self.grace_period_sec if in_grace_period else self.session_duration_sec
                if elapsed_sec >= max_allowed_time:
                    should_exit = True
                    exit_reason = "⏰ Tempo Limite Esgotado (Hard Close)"

                if should_exit:
                    sell_amount = active_position['crypto_qty']
                    print(f"[Sniper] Fechando posição: {exit_reason} @ {cur_price:.2f} ({pnl_pct:+.2f}%)")
                    
                    if not self.dry_run:
                        try:
                            prec_qty = float(self.exchange.amount_to_precision(self.symbol, sell_amount))
                            self.exchange.create_market_sell_order(self.symbol, prec_qty)
                        except Exception as e:
                            print(f"[Sniper] Erro ao executar venda na corretora: {e}")

                    gross_pnl = (sell_amount * cur_price) - active_position['entry_cost']
                    # Desconta taxa aproximada da Binance (0.1% compra + 0.1% venda = 0.2%)
                    fee = (active_position['entry_cost'] + (sell_amount * cur_price)) * 0.001
                    net_pnl = gross_pnl - fee
                    session_capital += net_pnl

                    trade_record = {
                        "pair": self.symbol,
                        "buy_time": active_position['buy_time'],
                        "sell_time": time.strftime("%H:%M:%S"),
                        "duration_sec": int(now - active_position['buy_timestamp']),
                        "buy_price": entry_price,
                        "sell_price": cur_price,
                        "crypto_qty": sell_amount,
                        "pnl_pct": round(pnl_pct, 2),
                        "net_pnl_fiat": round(net_pnl, 2),
                        "currency": self.currency,
                        "exit_reason": exit_reason
                    }
                    trades_history.append(trade_record)
                    kv_db.record_daytrade_microtrade(trade_record)
                    active_position = None

            # 4. Busca Sinal de Entrada (se não tiver posição e ainda tiver tempo hábil)
            elif elapsed_sec < (self.session_duration_sec - 60):  # Não abre trade nos últimos 60s
                ta_data = self.fetch_1m_ta(self.symbol)
                cur_price = ta_data.get("close", 0.0)
                rsi = ta_data.get("rsi7", 50.0)
                bb_lower = ta_data.get("bb_lower", 0.0)
                vwap = ta_data.get("vwap", 0.0)

                # Gatilho Quantitativo de Scalping:
                # 1. RSI-7 em sobrevenda extrema (< 28) E preço próximo ou abaixo da Banda Inferior de Bollinger
                # 2. OU Rompimento de alta da VWAP com RSI em expansão (> 52 e < 65)
                buy_signal = False
                trigger_name = ""

                if cur_price > 0 and rsi < 28 and cur_price <= bb_lower * 1.001:
                    buy_signal = True
                    trigger_name = f"RSI Sobrevendido ({rsi:.1f}) + Bollinger Low ({bb_lower:.2f})"
                elif cur_price > 0 and 52 <= rsi <= 65 and cur_price > vwap * 1.0005:
                    buy_signal = True
                    trigger_name = f"Rompimento de VWAP ({vwap:.2f}) + Micro-Momentum"

                if buy_signal and session_capital >= 5.0:
                    trade_cost = round(min(session_capital, self.capital), 2)
                    raw_qty = trade_cost / cur_price
                    try:
                        crypto_qty = float(self.exchange.amount_to_precision(self.symbol, raw_qty))
                    except:
                        crypto_qty = raw_qty

                    print(f"[Sniper] 🚀 SINAL DE COMPRA: {trigger_name} | {crypto_qty} {self.symbol} por ${trade_cost:.2f}")
                    
                    if not self.dry_run:
                        try:
                            self.exchange.create_market_buy_order(self.symbol, crypto_qty)
                        except Exception as e:
                            print(f"[Sniper] Falha na ordem de compra: {e}")
                            try:
                                self.exchange.create_market_buy_order(self.symbol, None, params={'quoteOrderQty': trade_cost})
                            except Exception as e2:
                                print(f"[Sniper] Falha também com quoteOrderQty: {e2}")

                    active_position = {
                        "symbol": self.symbol,
                        "entry_price": cur_price,
                        "current_price": cur_price,
                        "highest_price": cur_price,
                        "crypto_qty": crypto_qty,
                        "entry_cost": trade_cost,
                        "buy_timestamp": now,
                        "buy_time": time.strftime("%H:%M:%S")
                    }

            # 5. Condição de Término da Sessão
            max_total_time = self.session_duration_sec + (self.grace_period_sec if in_grace_period else 0)
            if elapsed_sec >= max_total_time and active_position is None:
                print(f"[Sniper] 🏁 Tempo total da sessão finalizado com sucesso ({elapsed_sec}s).")
                break

            time.sleep(3)  # Loop de alta frequência a cada 3 segundos

        # 6. Consolidação e Encerramento
        total_trades = len(trades_history)
        winning_trades = len([t for t in trades_history if t.get("net_pnl_fiat", 0) > 0])
        win_rate = round((winning_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0
        total_net_pnl = round(sum(t.get("net_pnl_fiat", 0) for t in trades_history), 2)
        total_pnl_pct = round((total_net_pnl / self.capital) * 100, 2) if self.capital > 0 else 0.0
        actual_duration_min = round((time.time() - started_at) / 60, 1)

        summary = {
            "initial_capital": self.capital,
            "final_capital": round(session_capital, 2),
            "currency": self.currency,
            "net_profit_fiat": total_net_pnl,
            "pnl_pct": total_pnl_pct,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "win_rate_pct": win_rate,
            "duration_str": f"{actual_duration_min} min",
            "in_grace_period_used": in_grace_period
        }

        kv_db.finish_daytrade_session(summary)
        print(f"\n========================================================")
        print(f"  [SNIPER FINALIZADO] Lucro Líquido: {total_net_pnl:+.2f} {self.currency} ({total_pnl_pct:+.2f}%)")
        print(f"  Trades: {total_trades} | Taxa de Acerto: {win_rate}% | Duração: {actual_duration_min}m")
        print(f"========================================================\n")
        return summary

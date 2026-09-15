"""
IvanvestAI — Daemon de Execução com Dois Loops Paralelos e Independentes.

Arquitetura:
  Thread 1 — MONITORAMENTO (a cada 15 min):
    Executa main.py: notícias, portfólio, análise, auditoria.
    Nunca é bloqueado pela sessão Sniper.

  Thread 2 — SNIPER DAY TRADE (a cada 1h, ou imediatamente se solicitação manual):
    Executa sessões de day trading de forma totalmente assíncrona.
    Verifica sessões manuais pendentes a cada 30 segundos enquanto aguarda.

Ambas as threads rodam como daemon — Ctrl+C encerra tudo de forma limpa.
"""
import time
import sys
import io
import threading
from datetime import datetime
from src.db.vercel_kv import kv_db

# Força UTF-8 no stdout/stderr para evitar erros de charmap no Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ─── Configuração ──────────────────────────────────────────────────────
MONITOR_INTERVAL_MIN = 15   # Ciclo de monitoramento (min)
SNIPER_CHECK_SEC     = 30   # Frequência de verificação do sniper (seg)
SNIPER_SESSION_MIN   = 10   # Duração de cada sessão Sniper (min)

# ─── Utilidades ────────────────────────────────────────────────────────
def log(tag: str, msg: str):
    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)


# ─── THREAD 1: Loop de Monitoramento (15 min) ──────────────────────────
def run_loop_monitor():
    """Dispara o pipeline principal (main.py) a cada MONITOR_INTERVAL_MIN minutos."""
    import subprocess
    import os
    cycle = 1
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    while True:
        log("MONITOR", f">>> Iniciando ciclo de monitoramento #{cycle} <<<")
        try:
            subprocess.run([sys.executable, "main.py"], check=False, env=env)
        except Exception as e:
            log("MONITOR", f"Erro no ciclo #{cycle}: {e}")

        log("MONITOR", f"Ciclo #{cycle} concluido. Proximo em {MONITOR_INTERVAL_MIN} minutos.")
        cycle += 1
        time.sleep(MONITOR_INTERVAL_MIN * 60)


# ─── THREAD 2: Loop do Sniper Day Trade (autônomo 1h + manual imediato) ─
def run_loop_sniper():
    """
    Gerencia sessões de Day Trade de forma independente.
    - Verifica a cada SNIPER_CHECK_SEC segundos se há:
        a) Sessão manual pendente → dispara imediatamente
        b) Intervalo horário atingido → dispara automaticamente
    - Uma sessão em execução não bloqueia o loop de monitoramento.
    """
    from src.agents.sniper_trader import SniperTraderAgent

    log("SNIPER", "Thread do Sniper Day Trade iniciada.")
    sniper_running = False  # Flag para evitar sobreposição de sessões

    while True:
        try:
            auto_config   = kv_db.get_sniper_auto_config()
            daytrade_sess = kv_db.get_daytrade_session()
            bot_config    = kv_db.get_bot_config()
            dry_run       = bot_config.get("dry_run", True)
            now_ts        = time.time()

            auto_enabled  = bool(auto_config.get("enabled", True))
            interval_sec  = int(auto_config.get("interval_minutes", 60)) * 60
            last_auto_run = auto_config.get("last_run_timestamp")

            is_manual_pending = bool(
                daytrade_sess and daytrade_sess.get("status") == "pending"
            )

            is_hourly_due = False
            if auto_enabled and not is_manual_pending:
                if last_auto_run is None or (now_ts - float(last_auto_run)) >= interval_sec:
                    is_hourly_due = True

            should_fire = is_manual_pending or is_hourly_due

            if should_fire and not sniper_running:
                sniper_running = True
                trigger = "MANUAL" if is_manual_pending else f"AUTÔNOMO ({auto_config.get('interval_minutes', 60)}min)"
                log("SNIPER", f"Disparando sessão [{trigger}]")

                if is_manual_pending:
                    cap = float(daytrade_sess.get("capital", 15.0))
                    curr = daytrade_sess.get("currency", "USDT")
                    src  = daytrade_sess.get("source_asset", "USDT")
                    sym  = daytrade_sess.get("symbol", "SCANNER_AUTO")
                else:
                    cap  = float(auto_config.get("capital", 15.0))
                    curr = auto_config.get("currency", "USDT")
                    src  = auto_config.get("source_asset", "USDT")
                    sym  = "SCANNER_AUTO"

                try:
                    sniper = SniperTraderAgent(
                        capital=cap,
                        currency=curr,
                        source_asset=src,
                        symbol=sym,
                        dry_run=dry_run,
                    )
                    result = sniper.run_session(duration_minutes=SNIPER_SESSION_MIN)
                    pnl = result.get("total_pnl_pct", 0.0)
                    ops = result.get("trades_count", 0)
                    log("SNIPER", f"Sessão finalizada. PnL: {pnl:+.2f}% | Operações: {ops}")
                except Exception as e:
                    log("SNIPER", f"Erro na sessão: {e}")
                    try:
                        kv_db.finish_daytrade_session({"status": "completed", "error": str(e)})
                    except Exception:
                        pass

                try:
                    auto_config["last_run_timestamp"] = time.time()
                    kv_db.save_sniper_auto_config(auto_config)
                except Exception as e:
                    log("SNIPER", f"Aviso: falha ao salvar timestamp: {e}")

                sniper_running = False
                log("SNIPER", f"Aguardando. Próxima verificação em {SNIPER_CHECK_SEC}s.")

            elif auto_enabled and last_auto_run:
                elapsed_m   = int((now_ts - float(last_auto_run)) / 60)
                remaining_m = max(0, int(auto_config.get("interval_minutes", 60)) - elapsed_m)
                # Log apenas a cada ~5 min para não poluir o terminal
                if elapsed_m > 0 and elapsed_m % 5 == 0:
                    log("SNIPER", f"Aguardando. Último disparo há {elapsed_m}min. Próximo em ~{remaining_m}min.")

        except Exception as outer_e:
            log("SNIPER", f"Erro inesperado no loop: {outer_e}")

        time.sleep(SNIPER_CHECK_SEC)


# ─── ENTRY POINT ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 56)
    print("  IvanvestAI — Daemon de Execução Paralela")
    print(f"  Monitoramento : a cada {MONITOR_INTERVAL_MIN} minutos (Thread 1)")
    print(f"  Sniper Trade  : autônomo 1h + manual imediato (Thread 2)")
    print(f"  As duas threads são totalmente independentes.")
    print("=" * 56)
    print()

    t_monitor = threading.Thread(
        target=run_loop_monitor,
        name="Monitor-15min",
        daemon=True,
    )
    t_sniper = threading.Thread(
        target=run_loop_sniper,
        name="Sniper-1h",
        daemon=True,
    )

    t_monitor.start()
    log("DAEMON", "Thread de Monitoramento (15min) iniciada.")

    time.sleep(2)  # Pequena pausa para não colidir os logs iniciais

    t_sniper.start()
    log("DAEMON", "Thread do Sniper Day Trade (1h) iniciada.")
    log("DAEMON", "Pressione Ctrl+C para encerrar.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("DAEMON", "Interrupção recebida. Encerrando daemon...")
        sys.exit(0)

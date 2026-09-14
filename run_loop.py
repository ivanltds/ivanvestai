"""
IvanvestAI - Daemon de Execução Periódica Local
Executa o ciclo do Hedge Fund automaticamente a cada intervalo definido (padrão: 15 minutos).
Ideal para rodar a partir do Brasil sem bloqueios de IP da Binance.
"""
import time
import sys
from datetime import datetime
from main import main

INTERVAL_MINUTES = 15

if __name__ == "__main__":
    print(f"==================================================")
    print(f"  IvanvestAI - Daemon Local Iniciado")
    print(f"  Frequência: a cada {INTERVAL_MINUTES} minutos")
    print(f"  IP: Brasil (Acesso nativo liberado à Binance)")
    print(f"==================================================\n")

    cycle_count = 1
    while True:
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        print(f"\n[{now_str}] >>> DISPARANDO CICLO #{cycle_count} <<<")
        try:
            main()
        except Exception as e:
            print(f"[{now_str}] Erro inesperado no ciclo #{cycle_count}: {e}")

        print(f"\nCiclo #{cycle_count} finalizado. Próxima execução em {INTERVAL_MINUTES} minutos...")
        cycle_count += 1
        time.sleep(INTERVAL_MINUTES * 60)

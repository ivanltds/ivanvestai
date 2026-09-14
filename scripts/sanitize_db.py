"""
Script de Sanitização do Banco de Dados (Vercel KV / Upstash Redis)
Apaga todos os dados operacionais e reinicia o sistema do zero.
As configurações (config:bot_settings) são PRESERVADAS.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.vercel_kv import kv_db

KEYS_TO_DELETE = [
    # Portfólio e Posições
    "portfolio:open_positions",
    # Dashboard - logs e histórico
    "dashboard:audit_logs",
    "dashboard:audit_logs_archive",
    "dashboard:pnl_history",
    # Sentimento de mercado
    "dashboard:current_sentiment",
    # Diretrizes do usuário (overrides temporários)
    "ai:user_directives",
]

def sanitize():
    print("=" * 50)
    print("  SANITIZAÇÃO DO BANCO DE DADOS IVANVEST AI")
    print("=" * 50)
    print()

    if not kv_db.enabled:
        print("[ERRO] Banco de dados não está configurado. Verifique as variáveis de ambiente.")
        return

    print("As seguintes chaves serão APAGADAS:")
    for key in KEYS_TO_DELETE:
        print(f"  - {key}")
    
    print()
    print("[PRESERVADO] config:bot_settings (suas configurações de DCA, limites, etc)")
    print()

    confirm = input("Confirma a limpeza? Digite 'SIM' para continuar: ").strip()
    if confirm != "SIM":
        print("Operação cancelada.")
        return

    print()
    deleted = 0
    for key in KEYS_TO_DELETE:
        result = kv_db._execute_command("del", key)
        status = "OK" if result is not None else "NAO ENCONTRADA"
        print(f"  [{status}] {key}")
        deleted += 1

    print()
    print(f"Sanitização concluída! {deleted} chaves processadas.")
    print("O sistema está pronto para começar do zero.")
    print()
    print("Próximos passos:")
    print("  1. Verifique as configurações em /settings no Dashboard")
    print("  2. Execute o robô manualmente com: python main.py")
    print("     ou aguarde o próximo ciclo automático do GitHub Actions (a cada 15 min)")

if __name__ == "__main__":
    sanitize()

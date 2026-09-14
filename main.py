"""
Ponto de entrada do IvanvestAI - Executado pelo Cloud Scheduler ou Cron.
"""
import sys
from src.dca_bot import DCABot

def dca_http_trigger(request):
    """
    Ponto de entrada HTTP para o Google Cloud Functions.
    O Cloud Scheduler fará uma requisição para cá.
    """
    bot = DCABot()
    result = bot.execute()
    
    if result.get("status") in ("success", "success_simulated"):
        return (result, 200)
    else:
        return (result, 500)

def main():
    """Execução via terminal local."""
    bot = DCABot()
    result = bot.execute()
    
    if result.get("status") in ("success", "success_simulated"):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()

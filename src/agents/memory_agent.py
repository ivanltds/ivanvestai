from src.db.vercel_kv import kv_db
import time

class MemoryAgent:
    """
    Agente 0: Guardião da Memória.
    Responsável por puxar as diretrizes do usuário do banco antes do ciclo,
    e por gravar o diário de bordo e estatísticas no banco no final do ciclo.
    """
    
    def fetch_context(self) -> str:
        """Busca as ordens supremas (Overrides) que o usuário enviou pelo Dashboard"""
        user_directives = kv_db.get_user_directives()
        if user_directives:
            print(f"[Agente 0] AVISO: Diretriz humana ativa encontrada! '{user_directives}'")
            return user_directives
        return ""
        
    def commit_cycle(self, news_insights: dict, final_trades: list, current_balances: dict):
        """
        Salva tudo que aconteceu na execução para o Dashboard ler depois.
        """
        print("[Agente 0] Realizando o Commit final do ciclo no Vercel KV...")
        
        # 1. Salva o Sentimento (Fear & Greed)
        is_bullish = news_insights.get("is_bullish", True)
        kv_db.save_market_sentiment(is_bullish, news_insights.get("summary", ""))
        
        # 2. Salva o Diário de Bordo (Audit Log)
        # Filtra trades não executados se houver falha matemática, 
        # mas aqui simplificamos e salvamos o que foi enviado para execução
        audit_entry = {
            "timestamp": int(time.time()),
            "news_summary": news_insights.get("summary", ""),
            "trades": final_trades,
            "directives_applied": self.fetch_context()
        }
        
        # O MemoryAgent sincroniza primeiro o saldo real com o banco
        kv_db.sync_with_binance(current_balances)
        
        kv_db.save_audit_log(audit_entry)
        
        print("[Agente 0] Histórico e Sentimento atualizados no banco com sucesso!")

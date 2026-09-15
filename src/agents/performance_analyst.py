import json
from src.llm.provider import get_llm_provider

class PerformanceAnalystAgent:
    """
    Agente 1.5: Analista de Performance (Aprendizado Contínuo)
    Lê o histórico recente de operações e o PnL atual para extrair 
    uma "Lição Aprendida" a ser usada pelo Gestor de Risco no ciclo atual.
    """
    def __init__(self):
        self.llm = get_llm_provider()

    def generate_lessons(
        self,
        audit_logs: list,
        open_positions: dict,
        daytrade_history: list = None,
        daytrade_chat: list = None
    ) -> str:
        """
        Gera conselhos e lições contínuas baseadas nos acertos/erros recentes de DCA e Day Trade Sniper.
        """
        if not audit_logs and not open_positions and not daytrade_history:
            return "Ainda não há histórico suficiente para extrair lições. Siga as regras padrão."

        print("[Agente 1.5] Analisando histórico de DCA e Day Trade Sniper para gerar lições aprendidas...")
        
        system_prompt = """
        Você é o Chefe de Otimização e Aprendizado de um Fundo Quantitativo com foco em Swing Trade (DCA) e Day Trade Sniper de Alta Frequência.
        Seu objetivo é analisar as operações recentes de carteira E os resultados e justificativas do Day Trade Sniper
        para deduzir o que deu certo e o que deu errado.
        
        Você deve extrair no MÁXIMO 2 lições práticas e diretas de uma linha.
        Exemplos de Saída Esperada:
        - "No day trade recente com PEPE (+0.85%), o setup de rompimento de VWAP funcionou perfeitamente; priorize moedas com range > 1.0%."
        - "A tolerância anti-loss evitou prejuízo desnecessário no minuto final; mantenha saídas no breakeven para preservar capital."
        - "O ativo XYZ está dando prejuízo persistente no DCA; considere rebalancear para Bitcoin se houver fraqueza."
        
        Retorne APENAS o texto das lições em plain text (sem JSON). Se estiver tudo dentro do esperado, 
        diga: "Mantenha a estratégia atual, sem erros críticos recentes."
        """
        
        # Filtra pensamentos de decisão relevantes do chat
        recent_thoughts = []
        if daytrade_chat:
            for m in daytrade_chat[-15:]:
                if isinstance(m, dict) and m.get('tag') in ['COMPRA', 'VENDA', 'RESULTADO', 'SCANNER']:
                    recent_thoughts.append(f"[{m.get('tag')}] {m.get('symbol')}: {m.get('message')}")

        # Simplifica logs do DCA para não estourar o limite de tokens da OpenAI (ignora raw_news enorme)
        simplified_logs = []
        if audit_logs:
            for log in audit_logs[:10]:
                simplified_logs.append({
                    "timestamp": log.get("timestamp"),
                    "market_sentiment": log.get("news_insights", {}).get("market_sentiment", ""),
                    "trades": log.get("trades", [])
                })

        user_prompt = f"""
        Histórico Recente de Swing Trade / DCA (Logs):
        {json.dumps(simplified_logs)}
        
        Posições Abertas Atuais na Carteira (PnL):
        {json.dumps(open_positions if open_positions else {})}

        Histórico Recente do Day Trade Sniper (Lucro/Prejuízo/Setups):
        {json.dumps(daytrade_history[:5] if daytrade_history else [])}

        Observações e Justificativas de Decisão do Sniper (Chat da IA):
        {json.dumps(recent_thoughts)}
        """
        
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_json=False
        )
        
        print(f"[Agente 1.5] Lição Aprendida: {response_text.strip()}")

        # Retroalimentação Fechada (Closed-Loop Feedback): calibra a política do Sniper no Redis
        try:
            self._calibrate_sniper_policy(daytrade_history)
        except Exception as e:
            print(f"[Agente 1.5] Aviso ao calibrar política do Sniper: {e}")

        return response_text.strip()

    def _calibrate_sniper_policy(self, daytrade_history: list):
        """Calibra dinamicamente as variáveis de risco, stop e target do Sniper com base no histórico."""
        from src.db.vercel_kv import kv_db
        policy = kv_db.get_sniper_policy()

        if not daytrade_history:
            return

        recent = daytrade_history[:8]
        total_trades = sum(h.get("total_trades", 0) for h in recent)
        winning_trades = sum(h.get("winning_trades", 0) for h in recent)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 50.0

        disqualified = set(policy.get("disqualified_pairs", []))

        # Analisa moedas com perdas repetidas para desqualificar temporariamente
        for h in recent:
            trades = h.get("trades", [])
            for t in trades:
                sym = t.get("symbol") or t.get("pair")
                pnl = t.get("net_pnl_fiat", 0)
                if sym and pnl < -0.30:
                    disqualified.add(sym)

        # Se win rate recente < 35%: Modo Conservador Defensivo
        if total_trades >= 3 and win_rate < 35.0:
            policy["risk_mode"] = "conservative"
            policy["target_pct"] = 2.20
            policy["trailing_arm_pct"] = 1.90
            policy["trailing_buffer_pct"] = 0.40
            policy["min_stop_pct"] = 1.50
            policy["max_stop_pct"] = 2.20
            policy["max_spread_pct"] = 0.06
            policy["min_rvol"] = 2.0
            print(f"[Agente 1.5] 🛡️ Calibragem Defensiva Ativada: Win Rate baixo ({win_rate:.1f}%). Elevando seletividade e stops.")
        # Se win rate recente >= 60%: Modo Expansivo
        elif total_trades >= 3 and win_rate >= 60.0:
            policy["risk_mode"] = "growth"
            policy["target_pct"] = 2.50
            policy["trailing_arm_pct"] = 2.00
            policy["trailing_buffer_pct"] = 0.50
            policy["min_stop_pct"] = 1.20
            policy["max_stop_pct"] = 1.80
            policy["max_spread_pct"] = 0.08
            policy["min_rvol"] = 1.5
            print(f"[Agente 1.5] 🚀 Calibragem Otimista Ativada: Win Rate sólido ({win_rate:.1f}%). Alvos expandidos para 2.5%.")
        else:
            policy["risk_mode"] = "balanced"
            policy["target_pct"] = 2.00
            policy["trailing_arm_pct"] = 1.80
            policy["trailing_buffer_pct"] = 0.40
            policy["min_stop_pct"] = 1.30
            policy["max_stop_pct"] = 2.00
            policy["max_spread_pct"] = 0.08
            policy["min_rvol"] = 1.5

        # Limita lista de desqualificados a no máximo 5 mais recentes
        policy["disqualified_pairs"] = list(disqualified)[-5:]
        kv_db.save_sniper_policy(policy)
        print(f"[Agente 1.5] Política quantitativa do Sniper atualizada no Redis: Modo={policy['risk_mode']} | Target={policy['target_pct']}% | MinStop={policy['min_stop_pct']}%")


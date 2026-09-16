# Commit: piso de stop-loss/take-profit no RiskCommitteeAgent
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/config/settings.py bot/agents/risk_committee_agent.py

git commit -m "Adiciona piso de stop-loss/take-profit no RiskCommitteeAgent

Achado no primeiro dry-run completo do comite (16/09): em ativos de
baixissima volatilidade (tokens lastreados em ouro, stablecoins), o
stop/take ancorado so no ATR ficava tao colado no preco de entrada
que ruido normal de mercado ja disparava a saida segundos depois de
abrir a posicao (ex: ARBUSDT +0.13% take profit, SYNUSDT -0.89%
stop loss, XAUTUSDT +0.06% take profit).

- settings.py: min_stop_loss_pct=1.0, min_take_profit_pct=1.5
- risk_committee_agent.py: instrucao no prompt pro LLM respeitar o
  piso, mais um clamp programatico depois da resposta do modelo,
  garantindo o piso mesmo se o LLM nao seguir a instrucao

Ainda nao testado contra a API real -- validar com
python run_cycle_once.py --ignore-macro-window"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

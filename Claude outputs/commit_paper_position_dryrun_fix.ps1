# Commit: fix critico -- venda de posicao usava dry_run ATUAL em vez do
# is_paper da propria posicao. Achado assim que uma posicao aberta em paper
# (seculo 9.9, nenhum ativo real comprado) bateu take profit com dry_run ja
# desligado, e o bot tentou vender de verdade um ativo que nunca existiu na
# conta -- so nao virou venda real por coincidencia (faltou saldo, -2010).
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/agents/execution_agent.py bot/orchestrator/cycle_runner.py

git commit -m "Fix critico: venda de posicao respeita is_paper da posicao, nao dry_run atual

_fill_price() decidia simular ou executar de verdade olhando pro
settings.dry_run ATUAL, nunca pro is_paper da posicao sendo fechada. Isso e
correto pra abrir posicao nova ou vender direto da carteira (decisoes
tomadas agora, com o regime atual) mas errado pra FECHAR uma posicao ja
existente: posicoes abertas em paper (dry_run=True, nenhum ativo comprado
de verdade) continuam com is_paper=True no banco pra sempre, mas se o
dry_run global for desligado depois, sell_position() passava a tentar
vender de verdade um ativo que nunca foi comprado. Na pratica isso falhou
com -2010 (insufficient balance) -- na pior hipotese, se por coincidencia
existisse saldo real do mesmo ativo, teria vendido saldo real pra fechar
uma posicao que era so simulacao.

- agents/execution_agent.py: _fill_price() agora recebe is_paper explicito
  de quem chama -- open_position()/sell_wallet_asset() passam
  settings.dry_run (decisao nova), sell_position() passa position.is_paper
  (fix da causa raiz)
- orchestrator/cycle_runner.py: _manage_open_positions() agora trata cada
  posicao isoladamente (try/except por posicao) -- uma venda que falha por
  qualquer motivo nao impede mais a checagem das outras posicoes abertas
  no mesmo ciclo, nem derruba o ciclo inteiro. Try/except extra em volta da
  chamada tambem.

IMPORTANTE: reiniciar o main.py depois deste commit -- o processo em
memoria ainda roda o codigo antigo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green
Write-Host "LEMBRETE: reinicie o python main.py agora (Ctrl+C e rode de novo) pra pegar o fix." -ForegroundColor Yellow

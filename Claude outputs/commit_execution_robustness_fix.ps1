# Commit: falha ao executar uma ordem real nao derruba mais o ciclo inteiro
# (a gestao de stop/take de posicoes reais ja abertas continua rodando), e
# nova checagem de saldo LIVRE em USDT antes de sugerir uma ordem.
# Achado logo depois do primeiro ciclo real com dry_run=False: uma tentativa
# de compra de NEARUSDT falhou com -2010 "insufficient balance" (o patrimonio
# total de $46 estava quase todo em BTC/ETH/FET/SOL/NEAR/SUI, nao em USDT
# livre) e isso derrubou o cycle_runner ANTES da gestao de stop/take das
# posicoes reais ja abertas rodar naquele ciclo.
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/orchestrator/cycle_runner.py bot/agents/portfolio_comparison_agent.py

git commit -m "Falha de ordem nao derruba mais o ciclo; checa saldo livre antes de sugerir ordem

Achado no primeiro ciclo real com dry_run=False: NEARUSDT foi aprovado pelo
comite e a ordem falhou com -2010 (insufficient balance) -- o patrimonio
total (~US\$46) estava quase todo em outros ativos (BTC/ETH/FET/SOL/NEAR/SUI),
nao em USDT livre. Sem tratamento, essa excecao subia sem ser pega ate fora
do asyncio.wait_for em run_cycle() e pulava _manage_open_positions() (a
checagem de stop/take das posicoes REAIS ja abertas) naquele ciclo inteiro.

- orchestrator/cycle_runner.py:
  - try/except em volta de execution_agent.open_position() -- uma ordem que
    falha agora e logada, pula pra proxima oportunidade, e o ciclo continua
  - except Exception (alem do ja existente except asyncio.TimeoutError) em
    volta do asyncio.wait_for -- qualquer erro nao previsto na avaliacao de
    oportunidades tambem nao impede mais a gestao de posicoes abertas de rodar
  - calcula available_stablecoin (saldo livre na stablecoin de seguranca, nao
    o patrimonio total) e repassa pra PortfolioComparisonAgent.check()
- agents/portfolio_comparison_agent.py: novo parametro opcional
  available_stablecoin em check() -- reprova a oportunidade com motivo claro
  quando o saldo livre for menor que o valor sugerido, em vez de deixar a
  ordem chegar na Binance e falhar la (economiza a chamada de LLM do
  RiskCommitteeAgent nesses casos tambem)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

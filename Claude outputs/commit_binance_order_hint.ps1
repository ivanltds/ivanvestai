# Commit: dica de erro -2015 tambem aparece quando uma ORDEM real falha
# (nao so na leitura de saldo) -- achado logo depois do Ivan desligar o
# dry_run e o primeiro ciclo tentar executar uma ordem de verdade pela
# primeira vez.
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/core/binance_client.py

git commit -m "Dica de erro -2015 tambem aparece em ordens reais, nao so leitura

place_market_order/place_limit_order chamavam create_order() sem nenhum
try/except -- so get_account_balances() tinha a dica amigavel plugada.
Resultado: quando o primeiro ciclo com dry_run=False tentou executar uma
ordem de verdade (HEIUSDT) e levou -2015, o Ivan so viu o traceback cru
do apscheduler, sem nenhuma pista.

Tambem ajustada a ordem de probabilidade da dica: -2015 numa ORDEM depois
de uma leitura assinada (saldo, get_my_trades) ja ter funcionado no mesmo
processo quase sempre significa que falta marcar 'Enable Spot & Margin
Trading' na API key -- 'Enable Reading' sozinho nao e suficiente pra
operar, mesmo que leituras funcionem normalmente.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

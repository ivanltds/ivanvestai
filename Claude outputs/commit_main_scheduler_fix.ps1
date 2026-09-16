# Commit: corrige o run_cycle nunca disparando sozinho no scheduler
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/main.py

git commit -m "Corrige run_cycle nunca disparando sozinho no scheduler do main.py

Achado rodando o main.py pela primeira vez de verdade (16/09): o job era
adicionado com next_run_time=None, que no APScheduler nao dispara ja na
largada como o comentario original dizia -- na verdade adiciona o job
PAUSADO, so roda depois de um scheduler.resume_job() explicito, que o
codigo nunca chamava. Resultado: bot_status podia estar 'running' e o
processo vivo, consumindo comandos normalmente, mas o ciclo do comite
nunca era chamado pelo scheduler, nem na largada nem no intervalo.

Corrigido passando next_run_time=datetime.now(timezone.utc) -- dispara
na largada e depois a cada cycle_interval_minutes, como sempre foi a
intencao."

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

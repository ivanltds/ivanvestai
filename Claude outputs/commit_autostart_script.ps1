# Commit: script pronto pra subir o bot automaticamente no login do Windows,
# com reinicio automatico se o processo cair.
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/service/setup_autostart.ps1 bot/service/README.md

git commit -m "Adiciona script pra rodar o bot automaticamente no login do Windows

service/setup_autostart.ps1 automatiza a Opcao B do README (Task Scheduler)
via Register-ScheduledTask -- cria a tarefa IvanVestAI-Bot disparada ao
fazer login, com reinicio automatico (ate 50x, a cada 2min) se o processo
cair, e MultipleInstances=IgnoreNew pra nunca rodar dois main.py ao mesmo
tempo sem querer.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

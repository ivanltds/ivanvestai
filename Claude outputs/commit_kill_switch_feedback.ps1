# Commit: kill switch (pausar/retomar bot) com status visivel, feedback
# e so o botao relevante visivel de cada vez.
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add web/app/api/bot-status/route.ts web/app/dashboard/kill-switch.tsx web/app/dashboard/page.tsx

git commit -m "Kill switch: status visivel, feedback de cada etapa, um botao por vez

Antes os dois botoes (Pausar/Retomar) ficavam sempre visiveis, redundantes,
e sem nenhuma indicacao do status atual do bot nem confirmacao de que o
comando realmente foi consumido -- so um 'disabled' rapido durante o fetch.

- web/app/api/bot-status/route.ts (novo): GET que le bot_status direto da
  tabela settings (mesma fonte que o bot usa em cada ciclo)
- web/app/dashboard/kill-switch.tsx: agora mostra um selo de status (bolinha
  verde/cinza + texto), so o botao da acao que faz sentido no estado atual,
  e feedback em texto em cada etapa (enviando / aguardando confirmacao /
  confirmado / timeout de 30s avisando que o main.py pode nao estar rodando)
- web/app/dashboard/page.tsx: le o bot_status inicial no server e passa
  pro componente, evitando o estado 'sem status' no primeiro carregamento"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

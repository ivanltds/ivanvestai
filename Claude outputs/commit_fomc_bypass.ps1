# Commit: permite desbloquear entradas novas na janela de risco FOMC/CPI
# a partir do dashboard (/settings), com aviso de risco explicito na tela.
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/core/config_store.py bot/orchestrator/cycle_runner.py web/app/settings/settings-form.tsx web/app/settings/page.tsx

git commit -m "Permite desbloquear janela de risco FOMC/CPI pelo dashboard

Antes so existia um bypass via CLI (run_cycle_once.py --ignore-macro-window),
pra teste manual pontual. Agora o dashboard (/settings) tem um checkbox
'bypass_macro_risk_window' que, uma vez salvo, e lido pelo bot a cada ciclo
(load_runtime_config, igual qualquer outra config) e passa a valer pro
scheduler automatico do main.py tambem -- nao so pra teste manual.

- bot/core/config_store.py: novo campo bypass_macro_risk_window na
  RuntimeConfig (default False), com caster bool proprio (_cast_bool)
- bot/orchestrator/cycle_runner.py: novo ramo em run_cycle() que ignora o
  bloqueio de ENTRADA NOVA quando config.bypass_macro_risk_window esta
  ligado, distinto do bypass antigo (--ignore-macro-window, so CLI). Nenhum
  dos dois bypasses afeta dry_run nem a gestao de posicoes ja abertas -- so
  a checagem de abrir posicao nova.
- web/app/settings/settings-form.tsx: suporte a campo tipo checkbox no
  formulario generico, com caixa de aviso de risco em vermelho que aparece
  quando a opcao esta marcada, explicando o que ela desliga e por que isso
  aumenta a exposicao a volatilidade extrema
- web/app/settings/page.tsx: default bypass_macro_risk_window = false

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

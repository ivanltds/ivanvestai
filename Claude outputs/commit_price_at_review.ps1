# Commit: instrumentacao de price_at_review no PositionReviewAgent
# Rode este script inteiro no PowerShell, na pasta do projeto
# (C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents)

cd C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents

git add bot/db/models.py bot/agents/position_review_agent.py bot/migrate_add_position_review_price.py bot/analyze_dry_run_performance.py

git commit -m "Guarda price_at_review em position_reviews pra medir acerto do PositionReviewAgent

A primeira leitura dos dados de dry-run (16/09) mostrou que nao dava pra
saber se os vereditos hold/sell do PositionReviewAgent tinham sido bons,
porque nao guardavamos o preco do ativo no momento do veredito.

- db/models.py: nova coluna price_at_review (nullable) em PositionReview
- migrate_add_position_review_price.py: migracao idempotente pra coluna nova
- agents/position_review_agent.py: calcula e grava price_at_review
  (value_usdt / quantity) em cada veredito daqui pra frente
- analyze_dry_run_performance.py: nova comparacao preco-no-veredito vs
  preco atual, pra estimar se 'hold' ficou bom (preco subiu) ou 'sell'
  ficou bom (preco caiu) -- aproximacao, nao medida definitiva

Vereditos anteriores a esta mudanca ficam com price_at_review = NULL
(nao da pra reconstruir retroativamente)."

git push

Write-Host ""
Write-Host "Commit e push concluidos." -ForegroundColor Green

// Compartilhado entre app/api/settings/route.ts (servidor) e app/settings/*
// (cliente) -- multi-conta-plano.md, Fase E (ver 10.8): `settings` agora tem
// chave composta (bot/db/models.py -- id substituto + 2 índices únicos
// parciais), então toda chave PODE ter um valor "master" (account_id NULL,
// vale pra quem não tiver override) e um valor POR CONTA (account_id = X,
// vence sobre o master pra essa conta só).
//
// `display_currency` é a única exceção: é só a moeda de EXIBIÇÃO do
// dashboard, que mostra a visão agregada de todas as contas (seção 6 do
// plano) -- não faz sentido ela divergir por conta, então fica sempre
// global mesmo editando com uma conta selecionada.
//
// Perfil de estratégia e travas do perfil final (29/09/2026) também são sempre
// globais: o scanner roda UMA vez por ciclo pra todas as contas, e o bot aplica
// esses valores no objeto `settings` compartilhado (bot/core/config_store.py).
export const ALWAYS_GLOBAL_KEYS = new Set<string>([
  "display_currency",
  "strategy_profile",
  "final_max_fear_greed",
  "final_pause_daily_loss_pct",
  "final_pause_drawdown_pct",
  "final_pause_days",
  "dust_sweep_enabled",
  "dust_sweep_interval_hours",
]);

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
export const ALWAYS_GLOBAL_KEYS = new Set<string>(["display_currency"]);

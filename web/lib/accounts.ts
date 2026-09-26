import { query } from "@/lib/db";

// multi-conta-plano.md, Fase E (seletor de conta no /dashboard e /operations,
// ver 10.9) -- mesma lista de contas ativas já usada em /settings, agora
// compartilhada num lugar só em vez de duplicar a query em cada página.
export interface ActiveAccount {
  id: string;
  label: string;
  dry_run: boolean;
}

export async function loadActiveAccounts(): Promise<ActiveAccount[]> {
  return query<ActiveAccount>(
    `select id, label, dry_run from accounts where is_active = true order by display_order, created_at`
  );
}

// Valida o ?account=<id> da URL contra as contas ativas de verdade -- um id
// desativado ou inventado na URL cai pra "todas as contas" (null) em vez de
// travar a página. Mesmo padrão usado em /settings.
export function resolveSelectedAccountId(accounts: ActiveAccount[], requested: string | undefined): string | null {
  return accounts.some((a) => a.id === requested) ? requested! : null;
}

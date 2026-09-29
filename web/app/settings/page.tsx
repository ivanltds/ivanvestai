import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";
import { ALWAYS_GLOBAL_KEYS } from "@/lib/settings-shared";
import SettingsForm from "./settings-form";

interface SettingRow { key: string; value: string; updated_at: string; account_id: string | null; }
interface AccountRow { id: string; label: string; dry_run: boolean; }

const DEFAULTS: Record<string, string> = {
  safety_stablecoin: "USDT",
  min_confidence_to_trade: "0.80",
  max_allocation_pct_per_trade: "0.50",
  daily_loss_alert_pct: "0.10",
  top_n_pairs: "100",
  cycle_interval_minutes: "15",
  display_currency: "BRL",
  bypass_macro_risk_window: "false",
  // Mesmos defaults de bot/config/settings.py (29/09/2026).
  strategy_profile: "final",
  final_max_fear_greed: "50",
  final_pause_daily_loss_pct: "3",
  final_pause_drawdown_pct: "10",
  final_pause_days: "7",
};

export const dynamic = "force-dynamic";

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  // multi-conta-plano.md, Fase E (ver 10.8): settings agora pode ter um valor
  // "Padrão" (account_id NULL) e um override POR CONTA (account_id = X, vence
  // sobre o padrão pra essa conta só). O seletor abaixo escolhe qual conta esta
  // página está editando -- "Padrão" edita account_id NULL, igual sempre foi.
  const accounts = await query<AccountRow>(
    `select id, label, dry_run from accounts where is_active = true order by display_order, created_at`
  );

  const { account: requestedAccountId } = await searchParams;
  // Só aceita account= se bater com uma conta ativa de verdade -- um id
  // desativado ou inventado na URL cai pro Padrão em vez de travar a página.
  const selectedAccountId = accounts.some((a) => a.id === requestedAccountId) ? requestedAccountId! : null;

  const rows = selectedAccountId
    ? await query<SettingRow>(
        `select key, value, updated_at, account_id from settings where account_id is null or account_id = $1`,
        [selectedAccountId]
      )
    : await query<SettingRow>(`select key, value, updated_at, account_id from settings where account_id is null`);

  const masterRows = rows.filter((r) => r.account_id === null);
  const accountRows = rows.filter((r) => r.account_id !== null);

  // Valor efetivo mostrado/editado: começa no Padrão do banco (ou DEFAULTS se
  // a chave nunca foi salva), e -- se uma conta estiver selecionada -- o
  // override dessa conta (se existir) vence por cima, pra toda chave que não
  // seja sempre-global (ver ALWAYS_GLOBAL_KEYS).
  const current = { ...DEFAULTS, ...Object.fromEntries(masterRows.map((r) => [r.key, r.value])) };

  // updatedAt rastreia a linha que VAI SER GRAVADA no próximo save -- a do
  // Padrão (modo Padrão, ou chave sempre-global) ou a da conta (se já existir
  // um override; null = ainda não existe, o próximo save vai CRIAR um override
  // novo pra essa conta, não sobrescrever o Padrão). Isso alimenta o controle
  // de concorrência otimista em /api/settings.
  const updatedAt: Record<string, string | null> = Object.fromEntries(masterRows.map((r) => [r.key, r.updated_at]));

  const overriddenKeys = new Set<string>();
  if (selectedAccountId) {
    for (const key of Object.keys(current)) {
      if (!ALWAYS_GLOBAL_KEYS.has(key)) updatedAt[key] = null;
    }
    for (const row of accountRows) {
      if (ALWAYS_GLOBAL_KEYS.has(row.key)) continue; // não deveria acontecer, mas por garantia
      current[row.key] = row.value;
      updatedAt[row.key] = row.updated_at;
      overriddenKeys.add(row.key);
    }
  }

  const selectedAccount = accounts.find((a) => a.id === selectedAccountId) ?? null;

  return (
    <div>
      <h1 style={{ fontSize: 20 }}>Configurações de trading</h1>

      {accounts.length > 0 && (
        <div style={{ display: "flex", gap: 8, margin: "14px 0", flexWrap: "wrap" }}>
          <a
            href="/settings"
            style={{
              fontSize: 13,
              padding: "5px 12px",
              borderRadius: 999,
              border: "1px solid var(--border)",
              textDecoration: "none",
              color: selectedAccountId === null ? "#fff" : "var(--text)",
              background: selectedAccountId === null ? "var(--accent)" : "transparent",
            }}
          >
            Padrão (todas as contas)
          </a>
          {accounts.map((acc) => (
            <a
              key={acc.id}
              href={`/settings?account=${acc.id}`}
              style={{
                fontSize: 13,
                padding: "5px 12px",
                borderRadius: 999,
                border: "1px solid var(--border)",
                textDecoration: "none",
                color: selectedAccountId === acc.id ? "#fff" : "var(--text)",
                background: selectedAccountId === acc.id ? "var(--accent)" : "transparent",
              }}
            >
              {acc.label}
              {acc.dry_run ? " (simulação)" : ""}
            </a>
          ))}
        </div>
      )}

      {selectedAccount && (
        <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12, maxWidth: 460, lineHeight: 1.5 }}>
          Editando só a conta <strong>{selectedAccount.label}</strong>. Um campo marcado &quot;próprio desta conta&quot;
          abaixo já tem um valor diferente do Padrão -- os demais mostram e usam o valor Padrão até você mudar e
          salvar aqui (o que cria um override só pra esta conta, sem afetar as outras). Moeda de exibição, estratégia e travas do
          perfil final valem sempre para todas as contas (gravam no Padrão).
        </p>
      )}

      <SettingsForm
        initial={current}
        initialUpdatedAt={updatedAt}
        accountId={selectedAccountId}
        overriddenKeys={[...overriddenKeys]}
      />
    </div>
  );
}

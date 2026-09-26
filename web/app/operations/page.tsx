import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadRecentCycles } from "@/lib/cycles";
import { loadActiveAccounts, resolveSelectedAccountId } from "@/lib/accounts";
import AccountTabs from "@/components/account-tabs";
import AutoRefresh from "./auto-refresh";
import OperationsTree from "./tree";

export const dynamic = "force-dynamic";

export default async function OperationsPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  // multi-conta-plano.md, Fase E (ver 10.9): mesmo seletor de /settings e
  // /dashboard -- "todas as contas" (sem filtro, o comportamento de sempre)
  // ou uma conta só, filtrando entradas/saídas/revisões/oportunidades dessa
  // conta e as linhas de log etiquetadas com o rótulo dela.
  const accounts = await loadActiveAccounts();
  const { account: requestedAccountId } = await searchParams;
  const selectedAccountId = resolveSelectedAccountId(accounts, requestedAccountId);
  const selectedAccount = accounts.find((a) => a.id === selectedAccountId) ?? null;

  const { cycles, tableMissing } = await loadRecentCycles(
    5,
    accounts.map((a) => a.label),
    selectedAccount ? { id: selectedAccount.id, label: selectedAccount.label } : null
  );

  return (
    <div>
      <AutoRefresh seconds={30} />
      <h1 style={{ fontSize: 20 }}>Operações</h1>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        Últimos 5 ciclos do bot. Clique para expandir: ciclo → agentes (gestão, coleta, revisão, comitê, execução)
        → entradas e saídas, e o resumo da operação no mesmo nível dos agentes. Atualiza sozinho a cada 30s.
      </p>

      <AccountTabs basePath="/operations" accounts={accounts} selectedAccountId={selectedAccountId} />

      {selectedAccount && (
        <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12, maxWidth: 520, lineHeight: 1.5 }}>
          Mostrando só <strong>{selectedAccount.label}</strong>: entradas, saídas, revisões e oportunidades
          filtradas por essa conta. Um ciclo roda as duas contas juntas, então etapas compartilhadas (notícias,
          scanner de mercado) continuam aparecendo mesmo aqui — só o que é específico de uma conta some quando
          é de outra.
        </p>
      )}

      {tableMissing ? (
        <div className="card">
          A tabela <code>bot_logs</code> ainda não existe. Ela é criada quando o bot sobe com a versão que grava logs no banco.
        </div>
      ) : (
        <OperationsTree cycles={cycles} />
      )}
    </div>
  );
}

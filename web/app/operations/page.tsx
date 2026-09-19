import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadRecentCycles } from "@/lib/cycles";
import AutoRefresh from "./auto-refresh";
import OperationsTree from "./tree";

export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const { cycles, tableMissing } = await loadRecentCycles(5);

  return (
    <div>
      <AutoRefresh seconds={30} />
      <h1 style={{ fontSize: 20 }}>Operações</h1>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        Últimos 5 ciclos do bot. Clique para expandir: ciclo → agentes (gestão, coleta, revisão, comitê, execução)
        → entradas e saídas, e o resumo da operação no mesmo nível dos agentes. Atualiza sozinho a cada 30s.
      </p>
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

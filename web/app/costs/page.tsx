import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { loadActiveAccounts } from "@/lib/accounts";
import { loadCostBreakdown, COST_AGENT_LABELS, type CostRow } from "@/lib/costs";

export const dynamic = "force-dynamic";

function formatUsd(value: number): string {
  // Custo por chamada costuma ser fração de centavo -- 4 casas decimais pra
  // não arredondar tudo pra "$0.00" e esconder diferença real entre agentes.
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`;
}

interface AccountTotal {
  label: string;
  costToday: number;
  callsToday: number;
  costAllTime: number;
  callsAllTime: number;
}

export default async function CostsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const accounts = await loadActiveAccounts();
  const accountLabel = new Map<string, string>(accounts.map((a) => [a.id, a.label]));

  // multi-conta-plano.md, Fase E (ver 10.10): api_cost_log é uma tabela
  // antiga (existe desde antes deste plano) -- em try/catch por segurança,
  // mas não deveria faltar num banco já rodando o bot.
  let todayRows: CostRow[] = [];
  let allTimeRows: CostRow[] = [];
  let tableMissing = false;
  try {
    const todayStart = new Date();
    todayStart.setUTCHours(0, 0, 0, 0);
    [todayRows, allTimeRows] = await Promise.all([loadCostBreakdown(todayStart), loadCostBreakdown(null)]);
  } catch (err: unknown) {
    if ((err as { code?: string } | null)?.code === "42P01") {
      tableMissing = true;
    } else {
      throw err;
    }
  }

  if (tableMissing) {
    return (
      <div className="card">
        <h1 style={{ fontSize: 20 }}>Custo de LLM</h1>
        <p style={{ color: "var(--muted)" }}>
          A tabela <code>api_cost_log</code> ainda não existe nesse banco. Rode <code>python -m db.init_db</code> no
          PC onde o bot roda e recarregue esta página.
        </p>
      </div>
    );
  }

  // "Compartilhado" = account_id NULL: agentes que rodam uma vez por ciclo
  // (news_agent) OU custo gravado antes deste painel existir (linhas
  // antigas nunca vão ganhar account_id retroativamente -- ver lib/costs.ts).
  const bucketKey = (accountId: string | null) => accountId ?? "shared";
  const bucketLabel = (accountId: string | null) =>
    accountId ? (accountLabel.get(accountId) ?? "Conta desativada/removida") : "Compartilhado";

  const totals = new Map<string, AccountTotal>();
  for (const row of allTimeRows) {
    const key = bucketKey(row.account_id);
    const existing = totals.get(key) ?? {
      label: bucketLabel(row.account_id),
      costToday: 0,
      callsToday: 0,
      costAllTime: 0,
      callsAllTime: 0,
    };
    existing.costAllTime += row.cost_usd;
    existing.callsAllTime += row.calls;
    totals.set(key, existing);
  }
  for (const row of todayRows) {
    const key = bucketKey(row.account_id);
    const existing = totals.get(key) ?? {
      label: bucketLabel(row.account_id),
      costToday: 0,
      callsToday: 0,
      costAllTime: 0,
      callsAllTime: 0,
    };
    existing.costToday += row.cost_usd;
    existing.callsToday += row.calls;
    totals.set(key, existing);
  }

  // Ordem de exibição: contas ativas primeiro (na ordem do seletor de
  // sempre), "Compartilhado" por último.
  const orderedKeys = [...accounts.map((a) => a.id), "shared"].filter((k) => totals.has(k));

  // Detalhe por agente dentro de cada bucket, maior custo primeiro.
  const byBucket = new Map<string, CostRow[]>();
  for (const row of allTimeRows) {
    const key = bucketKey(row.account_id);
    const list = byBucket.get(key) ?? [];
    list.push(row);
    byBucket.set(key, list);
  }
  for (const list of byBucket.values()) list.sort((a, b) => b.cost_usd - a.cost_usd);

  return (
    <div>
      <h1 style={{ fontSize: 20 }}>Custo de LLM</h1>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0, maxWidth: 640 }}>
        Estimativa (não é a fatura exata da OpenAI/DeepSeek) por conta, baseada em tokens de entrada/saída de cada
        chamada — ver <code>core/llm_client.py</code>. &quot;Compartilhado&quot; junta o NewsAgent (roda uma vez por
        ciclo, não dá pra atribuir a uma conta só) e qualquer custo gravado antes deste painel existir (linhas
        antigas nunca ganham conta retroativamente).
      </p>

      <div className="grid grid-2">
        {orderedKeys.map((key) => {
          const t = totals.get(key)!;
          return (
            <div className="card" key={key}>
              <div style={{ color: "var(--muted)", fontSize: 13 }}>{t.label}</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>{formatUsd(t.costAllTime)}</div>
              <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>
                total · {t.callsAllTime} chamada(s)
              </div>
              <div style={{ marginTop: 8, fontSize: 13 }}>
                Hoje: {formatUsd(t.costToday)} ({t.callsToday} chamada(s))
              </div>
            </div>
          );
        })}
        {orderedKeys.length === 0 && (
          <div className="card">
            <p style={{ color: "var(--muted)" }}>Nenhum custo registrado ainda.</p>
          </div>
        )}
      </div>

      {orderedKeys.map((key) => {
        const rows = byBucket.get(key) ?? [];
        if (rows.length === 0) return null;
        const t = totals.get(key)!;
        return (
          <div className="card" key={`detail-${key}`}>
            <h2 style={{ fontSize: 16 }}>{t.label} — por agente (total)</h2>
            {rows.map((row) => (
              <div
                key={row.agent_name}
                style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}
              >
                <span>{COST_AGENT_LABELS[row.agent_name] ?? row.agent_name}</span>
                <span>
                  {formatUsd(row.cost_usd)} <span style={{ color: "var(--muted)", fontSize: 12 }}>({row.calls}x)</span>
                </span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

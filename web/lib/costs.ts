import { query } from "@/lib/db";

// multi-conta-plano.md, Fase E (painel de custo de LLM por conta, ver 10.10)
// -- account_id em api_cost_log só passou a ser gravado de fato a partir da
// rodada que criou este painel (core/llm_client.py). Toda linha ANTERIOR a
// isso fica com account_id NULL pra sempre (não dá pra saber retroativamente
// qual conta gerou aquele custo) -- aparece junto do "Compartilhado" mesmo
// sendo, na verdade, custo antigo de antes do bot rodar 2 contas. O rótulo na
// página avisa isso.
export interface CostRow {
  account_id: string | null;
  agent_name: string;
  cost_usd: number;
  calls: number;
}

// Nomes amigáveis pros agent_name gravados em api_cost_log (mesmo espírito
// do AGENT_LABELS de lib/cycles.ts, mas com o conjunto completo de agentes
// que chamam LLM -- incluindo news_agent, que cycles.ts não precisa nomear).
export const COST_AGENT_LABELS: Record<string, string> = {
  news_agent: "Notícias",
  viability_agent: "Viabilidade",
  viability_agent_reconciliation: "Viabilidade (reconciliação)",
  position_review_agent: "Revisão de posição",
  risk_committee_agent: "Comitê de risco",
};

export async function loadCostBreakdown(since: Date | null): Promise<CostRow[]> {
  return query<CostRow>(
    since
      ? `select account_id, agent_name, sum(estimated_cost_usd)::float as cost_usd, count(*)::int as calls
         from api_cost_log where timestamp >= $1 group by account_id, agent_name`
      : `select account_id, agent_name, sum(estimated_cost_usd)::float as cost_usd, count(*)::int as calls
         from api_cost_log group by account_id, agent_name`,
    since ? [since] : []
  );
}

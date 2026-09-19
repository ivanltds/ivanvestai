import { query } from "@/lib/db";

// Monta a visão "Ver operações": os últimos ciclos do bot, cada um com o que
// cada agente fez e as decisões tomadas. Fontes:
//  - bot_logs (cycle_id, seções "-- Título --" emitidas pelo vlog) -> início/fim,
//    modo, ordem do fluxo e as linhas de cada etapa;
//  - opportunities + committee_decisions (por cycle_id) -> decisões do comitê;
//  - trades / position_reviews / wallet_snapshots / news_items dentro da janela
//    de tempo do ciclo -> entradas, saídas, revisões, carteira, notícias.

export interface LogLine {
  ts: string;
  level: string;
  message: string;
}

export interface DecisionView {
  agent: string;
  label: string;
  decision: string;
  confidence: number;
  reasoning: string;
  model: string;
}

export interface OpportunityView {
  id: string;
  pair: string;
  strategy: string;
  regime: string;
  status: string;
  confidence: number | null;
  decisions: DecisionView[];
}

export interface TradeView {
  id: string;
  ts: string;
  pair: string;
  side: string;
  quantity: number;
  price: number;
  value: number;
  fee: number;
  feeAsset: string;
  reason: string;
  isPaper: boolean;
}

export interface ReviewView {
  id: string;
  ts: string;
  asset: string;
  decision: string;
  confidence: number;
  reasoning: string;
  valueUsdt: number;
  acted: boolean;
  isPaper: boolean;
}

export interface WalletAssetView {
  asset: string;
  quantity: number;
  valueUsdt: number;
}

export interface NewsView {
  id: string;
  source: string;
  summary: string;
  sentiment: number;
}

export type FlowState = "ok" | "error" | "skipped" | "idle";

export interface FlowStep {
  key: string;
  label: string;
  state: FlowState;
  detail: string;
}

export interface CycleView {
  id: string;
  startedAt: string;
  endedAt: string;
  durationSec: number;
  dryRun: boolean;
  status: "concluido" | "em_andamento" | "interrompido";
  warnings: number;
  errors: number;
  flow: FlowStep[];
  agents: {
    management: { lines: LogLine[] };
    collection: {
      news: { count: number | null; lines: LogLine[]; items: NewsView[] };
      portfolio: { equity: number | null; free: number | null; assets: WalletAssetView[]; lines: LogLine[] };
      scanner: { detected: number | null; evaluated: number; lines: LogLine[] };
      other: LogLine[];
    };
    review: { lines: LogLine[]; reviews: ReviewView[] };
    committee: { lines: LogLine[]; opportunities: OpportunityView[] };
    execution: { entries: TradeView[]; exits: TradeView[] };
  };
  summary: {
    headline: string;
    equity: number | null;
    free: number | null;
    news: number | null;
    detected: number | null;
    evaluated: number;
    approved: number;
    rejected: number;
    entries: number;
    exits: number;
    reviews: number;
    reviewSells: number;
    topRejection: string | null;
    alerts: LogLine[];
    allLines: LogLine[];
  };
}

const AGENT_ORDER = ["portfolio_comparison_agent", "viability_agent", "risk_committee_agent"];
const AGENT_LABELS: Record<string, string> = {
  portfolio_comparison_agent: "Portfólio",
  viability_agent: "Viabilidade",
  risk_committee_agent: "Comitê de risco",
};

type SectionKey = "header" | "management" | "collection" | "review" | "committee" | "ended";

function sectionFor(title: string): SectionKey | null {
  if (title.includes("Gestão")) return "management";
  if (title.includes("Coleta")) return "collection";
  if (title.includes("Revisão")) return "review";
  if (title.includes("Passo 2-6")) return "committee";
  if (title.includes("Ciclo encerrado")) return "ended";
  return null;
}

// "12,345.67" -> 12345.67 (o vlog formata com vírgula de milhar e ponto decimal)
function num(text: string | undefined): number | null {
  if (!text) return null;
  const n = Number(text.replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

// O aviso do circuit breaker sai como ERROR no log mas é um alerta de patrimônio
// (o bot segue operando), não uma falha da etapa -- não deve pintar a etapa de vermelho.
const isAlertOnly = (l: LogLine) => l.message.startsWith("Circuit breaker");

function pickState(lines: LogLine[], present: boolean): FlowState {
  if (!present) return "skipped";
  return lines.some((l) => (l.level === "ERROR" || l.level === "CRITICAL") && !isAlertOnly(l)) ? "error" : "ok";
}

// Agrupa motivos de reprovação parecidos ("$30.43" e "$12.10" viram "N") e devolve o mais comum.
function topRejectionReason(opps: OpportunityView[]): string | null {
  const counts = new Map<string, number>();
  for (const opp of opps) {
    if (opp.status !== "rejected") continue;
    const source =
      opp.decisions.find((d) => d.agent === "portfolio_comparison_agent" && d.decision === "reject") ??
      opp.decisions.find((d) => d.decision === "reject");
    if (!source) continue;
    const key = source.reasoning.replace(/\$?\d+([.,]\d+)?/g, "N").slice(0, 110);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [key, count] of counts) {
    if (count > bestCount) {
      best = key;
      bestCount = count;
    }
  }
  return best ? `${best} (${bestCount}x)` : null;
}

function makeHeadline(args: {
  entries: TradeView[];
  exits: TradeView[];
  evaluated: number;
  approved: number;
  rejected: number;
  topRejection: string | null;
  headerLines: LogLine[];
  hasCommittee: boolean;
  detected: number | null;
  allLines: LogLine[];
}): string {
  const { entries, exits, evaluated, rejected, topRejection, headerLines, hasCommittee, detected, allLines } = args;
  const parts: string[] = [];
  if (entries.length > 0) {
    parts.push(`${entries.length} entrada(s): ${entries.map((t) => t.pair).join(", ")}`);
  }
  if (exits.length > 0) {
    parts.push(`${exits.length} saída(s): ${exits.map((t) => `${t.pair} (${t.reason})`).join(", ")}`);
  }
  if (parts.length > 0) return parts.join(" · ");

  if (headerLines.some((l) => l.message.includes("Bot pausado"))) return "Bot pausado — só gestão de posições abertas.";
  if (headerLines.some((l) => l.message.includes("Janela de risco macro")) && !hasCommittee) {
    return "Janela de risco macro — sem novas entradas.";
  }
  if (allLines.some((l) => l.message.startsWith("Saldo livre") && l.message.includes("abaixo do mínimo"))) {
    return `Sem novas entradas: saldo livre abaixo do mínimo de ordem${evaluated > 0 ? ` (${evaluated} oportunidade(s) avaliada(s) antes)` : ""}.`;
  }
  if (evaluated > 0) {
    return `Nenhuma entrada — ${rejected} de ${evaluated} oportunidade(s) reprovada(s)${topRejection ? `. Principal motivo: ${topRejection}` : "."}`;
  }
  if (detected === 0) return "Sem oportunidades do scanner neste ciclo.";
  return "Sem entradas ou saídas neste ciclo.";
}

export async function loadRecentCycles(limit = 5): Promise<{ cycles: CycleView[]; tableMissing: boolean }> {
  let cycleRows: {
    cycle_id: string;
    started_at: Date;
    ended_at: Date;
    dry_run: boolean;
    warnings: number;
    errors: number;
    concluded: boolean;
  }[];

  try {
    cycleRows = await query(
      `select cycle_id,
              min(timestamp) as started_at,
              max(timestamp) as ended_at,
              bool_and(dry_run) as dry_run,
              (count(*) filter (where level = 'WARNING'))::int as warnings,
              (count(*) filter (where level in ('ERROR', 'CRITICAL')))::int as errors,
              bool_or(message like '%CICLO CONCLU%' or message like '-- Ciclo encerrado --') as concluded
       from bot_logs
       where cycle_id is not null
       group by cycle_id
       order by min(timestamp) desc
       limit $1`,
      [limit]
    );
  } catch (err: unknown) {
    if ((err as { code?: string } | null)?.code === "42P01") return { cycles: [], tableMissing: true };
    throw err;
  }
  if (cycleRows.length === 0) return { cycles: [], tableMissing: false };

  const ids = cycleRows.map((c) => c.cycle_id);
  const windowStart = new Date(Math.min(...cycleRows.map((c) => c.started_at.getTime())) - 2000);
  const windowEnd = new Date(Math.max(...cycleRows.map((c) => c.ended_at.getTime())) + 8000);

  const [logRows, oppRows, tradeRows, reviewRows, walletRows, newsRows] = await Promise.all([
    query<{ cycle_id: string; timestamp: Date; level: string; message: string }>(
      `select cycle_id, timestamp, level, message from bot_logs
       where cycle_id = any($1) order by timestamp, id`,
      [ids]
    ),
    query<{
      id: string; cycle_id: string; pair: string; strategy: string; market_regime: string;
      status: string; final_confidence: number | null;
    }>(
      `select id, cycle_id, pair, strategy, market_regime, status, final_confidence
       from opportunities where cycle_id = any($1) order by timestamp, id`,
      [ids]
    ),
    query<{
      id: string; timestamp: Date; pair: string; side: string; quantity: number; price: number;
      fee: number; fee_asset: string; reason: string; is_paper: boolean;
    }>(
      `select id, timestamp, pair, side, quantity, price, fee, fee_asset, reason, is_paper
       from trades where timestamp between $1 and $2 order by timestamp`,
      [windowStart, windowEnd]
    ),
    query<{
      id: string; timestamp: Date; asset: string; decision: string; confidence: number;
      reasoning: string; value_usdt: number; acted: boolean; is_paper: boolean;
    }>(
      `select id, timestamp, asset, decision, confidence, reasoning, value_usdt, acted, is_paper
       from position_reviews where timestamp between $1 and $2 order by timestamp`,
      [windowStart, windowEnd]
    ),
    query<{ timestamp: Date; asset: string; quantity: number; value_usdt: number }>(
      `select timestamp, asset, quantity, value_usdt from wallet_snapshots
       where timestamp between $1 and $2 order by timestamp`,
      [windowStart, windowEnd]
    ),
    query<{ id: string; timestamp: Date; source: string; summary_pt: string; sentiment_score: number }>(
      `select id, timestamp, source, summary_pt, sentiment_score from news_items
       where timestamp between $1 and $2 order by timestamp`,
      [windowStart, windowEnd]
    ),
  ]);

  const oppIds = oppRows.map((o) => o.id);
  const decisionRows = oppIds.length
    ? await query<{
        opportunity_id: string; agent_name: string; decision: string; confidence: number;
        reasoning: string; model_used: string;
      }>(
        `select opportunity_id, agent_name, decision, confidence, reasoning, model_used
         from committee_decisions where opportunity_id = any($1) order by timestamp, id`,
        [oppIds]
      )
    : [];

  const now = Date.now();
  const cycles: CycleView[] = cycleRows.map((c) => {
    const start = c.started_at.getTime() - 2000;
    const end = c.ended_at.getTime() + 8000;
    const inWindow = (d: Date) => d.getTime() >= start && d.getTime() <= end;

    // --- logs agrupados por seção ("-- Título --") ---
    const buckets: Record<SectionKey, LogLine[]> = {
      header: [], management: [], collection: [], review: [], committee: [], ended: [],
    };
    const allLines: LogLine[] = [];
    const seen = new Set<SectionKey>();
    let current: SectionKey = "header";
    for (const row of logRows.filter((r) => r.cycle_id === c.cycle_id)) {
      const line: LogLine = { ts: row.timestamp.toISOString(), level: row.level, message: row.message };
      allLines.push(line);
      const sectionMatch = /^-- (.+) --$/.exec(row.message);
      if (sectionMatch) {
        const key = sectionFor(sectionMatch[1]);
        if (key) {
          current = key;
          seen.add(key);
        }
        continue;
      }
      buckets[current].push(line);
    }

    // --- coleta: separa por agente ---
    const newsLines: LogLine[] = [];
    const portfolioLines: LogLine[] = [];
    const scannerLines: LogLine[] = [];
    const otherCollection: LogLine[] = [];
    let newsCount: number | null = null;
    let detected: number | null = null;
    let equity: number | null = null;
    let free: number | null = null;
    for (const line of buckets.collection) {
      const m = line.message;
      const counts = /(\d+) notícia\(s\) coletada\(s\) \| (\d+) oportunidade\(s\)/.exec(m);
      const equityMatch = /Patrimônio total: \$([\d,.]+) USDT \(livre em \w+: \$([\d,.]+)\)/.exec(m);
      if (counts) {
        newsCount = Number(counts[1]);
        detected = Number(counts[2]);
        newsLines.push(line);
        scannerLines.push(line);
      } else if (equityMatch) {
        equity = num(equityMatch[1]);
        free = num(equityMatch[2]);
        portfolioLines.push(line);
      } else if (m.startsWith("NewsAgent")) newsLines.push(line);
      else if (m.startsWith("PortfolioAgent") || m.startsWith("Circuit breaker")) portfolioLines.push(line);
      else if (m.startsWith("MarketScannerAgent")) scannerLines.push(line);
      else otherCollection.push(line);
    }

    // --- comitê ---
    const opportunities: OpportunityView[] = oppRows
      .filter((o) => o.cycle_id === c.cycle_id)
      .map((o) => ({
        id: o.id,
        pair: o.pair,
        strategy: o.strategy,
        regime: o.market_regime,
        status: o.status,
        confidence: o.final_confidence,
        decisions: decisionRows
          .filter((d) => d.opportunity_id === o.id)
          .sort((a, b) => AGENT_ORDER.indexOf(a.agent_name) - AGENT_ORDER.indexOf(b.agent_name))
          .map((d) => ({
            agent: d.agent_name,
            label: AGENT_LABELS[d.agent_name] ?? d.agent_name,
            decision: d.decision,
            confidence: d.confidence,
            reasoning: d.reasoning,
            model: d.model_used,
          })),
      }));

    // --- execução ---
    const trades: TradeView[] = tradeRows.filter((t) => inWindow(t.timestamp)).map((t) => ({
      id: t.id,
      ts: t.timestamp.toISOString(),
      pair: t.pair,
      side: t.side,
      quantity: t.quantity,
      price: t.price,
      value: t.quantity * t.price,
      fee: t.fee,
      feeAsset: t.fee_asset,
      reason: t.reason,
      isPaper: t.is_paper,
    }));
    const entries = trades.filter((t) => t.side === "buy");
    const exits = trades.filter((t) => t.side === "sell");

    // --- revisão de carteira / carteira / notícias ---
    const reviews: ReviewView[] = reviewRows.filter((r) => inWindow(r.timestamp)).map((r) => ({
      id: r.id,
      ts: r.timestamp.toISOString(),
      asset: r.asset,
      decision: r.decision,
      confidence: r.confidence,
      reasoning: r.reasoning,
      valueUsdt: r.value_usdt,
      acted: r.acted,
      isPaper: r.is_paper,
    }));
    const assets: WalletAssetView[] = walletRows
      .filter((w) => inWindow(w.timestamp) && w.value_usdt >= 0.01)
      .map((w) => ({ asset: w.asset, quantity: w.quantity, valueUsdt: w.value_usdt }))
      .sort((a, b) => b.valueUsdt - a.valueUsdt);
    const newsItems: NewsView[] = newsRows.filter((n) => inWindow(n.timestamp)).map((n) => ({
      id: n.id,
      source: n.source,
      summary: n.summary_pt,
      sentiment: n.sentiment_score,
    }));

    const approved = opportunities.filter((o) => o.status === "approved").length;
    const rejected = opportunities.filter((o) => o.status === "rejected").length;
    const topRejection = topRejectionReason(opportunities);
    const alerts = allLines.filter((l) => l.level === "WARNING" || l.level === "ERROR" || l.level === "CRITICAL");

    const status: CycleView["status"] = c.concluded
      ? "concluido"
      : now - c.ended_at.getTime() < 3 * 60 * 1000
        ? "em_andamento"
        : "interrompido";

    const flow: FlowStep[] = [
      {
        key: "management", label: "Gestão", state: pickState(buckets.management, seen.has("management")),
        detail: "stop/take das posições abertas",
      },
      {
        key: "collection", label: "Coleta", state: pickState(buckets.collection, seen.has("collection")),
        detail: `${newsCount ?? "?"} notícias · ${detected ?? "?"} oportunidades`,
      },
      {
        key: "review", label: "Revisão", state: pickState(buckets.review, seen.has("review")),
        detail: `${reviews.length} posição(ões) revisada(s)`,
      },
      {
        key: "committee", label: "Comitê", state: pickState(buckets.committee, seen.has("committee")),
        detail: seen.has("committee") ? `${opportunities.length} avaliada(s)` : "não rodou (sem oportunidades/janela macro)",
      },
      {
        key: "execution", label: "Execução", state: entries.length + exits.length > 0 ? "ok" : "idle",
        detail: `${entries.length} entrada(s) · ${exits.length} saída(s)`,
      },
    ];

    return {
      id: c.cycle_id,
      startedAt: c.started_at.toISOString(),
      endedAt: c.ended_at.toISOString(),
      durationSec: Math.round((c.ended_at.getTime() - c.started_at.getTime()) / 1000),
      dryRun: c.dry_run,
      status,
      warnings: c.warnings,
      errors: c.errors,
      flow,
      agents: {
        management: { lines: buckets.management },
        collection: {
          news: { count: newsCount, lines: newsLines, items: newsItems },
          portfolio: { equity, free, assets, lines: portfolioLines },
          scanner: { detected, evaluated: opportunities.length, lines: scannerLines },
          other: otherCollection,
        },
        review: { lines: buckets.review, reviews },
        committee: { lines: buckets.committee, opportunities },
        execution: { entries, exits },
      },
      summary: {
        headline: makeHeadline({
          entries, exits, evaluated: opportunities.length, approved, rejected, topRejection,
          headerLines: [...buckets.header, ...buckets.management, ...buckets.ended], hasCommittee: seen.has("committee"), detected, allLines,
        }),
        equity,
        free,
        news: newsCount,
        detected,
        evaluated: opportunities.length,
        approved,
        rejected,
        entries: entries.length,
        exits: exits.length,
        reviews: reviews.length,
        reviewSells: reviews.filter((r) => r.acted).length,
        topRejection,
        alerts,
        allLines,
      },
    };
  });

  return { cycles, tableMissing: false };
}

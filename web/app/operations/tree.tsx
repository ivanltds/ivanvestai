import type {
  CycleView,
  DecisionView,
  FlowState,
  LogLine,
  OpportunityView,
  ReviewView,
  TradeView,
} from "@/lib/cycles";

const TZ = "America/Sao_Paulo";

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const fmtTimeSec = (iso: string) =>
  new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: TZ });
const fmtDay = (iso: string) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: TZ });
const usd = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`);
const qty = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 8 });
const price = (n: number) => `$${n.toLocaleString("en-US", { maximumFractionDigits: n < 1 ? 6 : 2 })}`;

const REASON_LABELS: Record<string, string> = {
  committee: "comitê",
  stop_loss: "stop loss",
  take_profit: "take profit",
  manual_flag: "venda manual",
  position_review: "revisão de carteira",
};

type Tone = "green" | "red" | "yellow" | "blue" | "muted";

function Badge({ tone = "muted", children }: { tone?: Tone; children: React.ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

// Nó colapsável (nativo <details>: sem JS, acessível, começa sempre fechado).
function Node({
  level,
  icon,
  title,
  badges,
  children,
  hint,
}: {
  level: 1 | 2 | 3 | 4 | 5;
  icon?: string;
  title: React.ReactNode;
  badges?: React.ReactNode;
  hint?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <details className={`node node-l${level}`}>
      <summary>
        {icon && <span className="node-icon">{icon}</span>}
        <span className="node-title">{title}</span>
        {badges}
        {hint && <span className="node-hint">{hint}</span>}
      </summary>
      <div className="node-body">{children}</div>
    </details>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}

function LogList({ lines }: { lines: LogLine[] }) {
  if (lines.length === 0) return <Empty>Sem registros nesta etapa.</Empty>;
  return (
    <ul className="loglist">
      {lines.map((l, i) => (
        <li key={i} className={`log-${l.level.toLowerCase()}`}>
          <span className="log-ts">{fmtTimeSec(l.ts)}</span>
          <span className="log-msg">{l.message}</span>
        </li>
      ))}
    </ul>
  );
}

function decisionTone(decision: string): Tone {
  if (decision === "approve" || decision === "hold") return "green";
  if (decision === "reject" || decision === "sell") return "red";
  return "muted";
}
const decisionLabel: Record<string, string> = { approve: "aprovou", reject: "reprovou", abstain: "absteve-se", hold: "manter", sell: "vender" };

function DecisionNode({ d }: { d: DecisionView }) {
  return (
    <Node
      level={5}
      title={d.label}
      badges={
        <>
          <Badge tone={decisionTone(d.decision)}>{decisionLabel[d.decision] ?? d.decision}</Badge>
          <Badge>{pct(d.confidence)}</Badge>
        </>
      }
      hint={d.model || "regras"}
    >
      <p className="reasoning">{d.reasoning}</p>
    </Node>
  );
}

function OpportunityNode({ o }: { o: OpportunityView }) {
  const approved = o.status === "approved";
  return (
    <Node
      level={4}
      icon={approved ? "✅" : "⛔"}
      title={o.pair}
      badges={
        <>
          <Badge tone={approved ? "green" : "red"}>{approved ? "aprovada" : "reprovada"}</Badge>
          <Badge>{o.strategy}</Badge>
        </>
      }
      hint={`regime ${o.regime}${o.confidence !== null ? ` · confiança ${pct(o.confidence)}` : ""}`}
    >
      {o.decisions.length === 0 ? <Empty>Sem decisões registradas.</Empty> : o.decisions.map((d) => <DecisionNode key={d.agent} d={d} />)}
    </Node>
  );
}

function TradeTable({ trades, kind }: { trades: TradeView[]; kind: "entry" | "exit" }) {
  if (trades.length === 0) return <Empty>{kind === "entry" ? "Nenhuma entrada neste ciclo." : "Nenhuma saída neste ciclo."}</Empty>;
  return (
    <div className="table-wrap">
      <table className="mini">
        <thead>
          <tr>
            <th>Hora</th><th>Par</th><th>Qtd</th><th>Preço</th><th>Valor</th><th>Taxa</th><th>Motivo</th><th>Modo</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id}>
              <td>{fmtTimeSec(t.ts)}</td>
              <td>{t.pair}</td>
              <td>{qty(t.quantity)}</td>
              <td>{price(t.price)}</td>
              <td>{usd(t.value)}</td>
              <td>{t.fee ? `${qty(t.fee)} ${t.feeAsset}` : "—"}</td>
              <td>{REASON_LABELS[t.reason] ?? t.reason}</td>
              <td>{t.isPaper ? <Badge tone="blue">simulado</Badge> : <Badge tone="red">real</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewNode({ r }: { r: ReviewView }) {
  return (
    <Node
      level={4}
      icon="🧐"
      title={r.asset}
      badges={
        <>
          <Badge tone={r.decision === "sell" ? "red" : "green"}>{decisionLabel[r.decision] ?? r.decision}</Badge>
          <Badge>{pct(r.confidence)}</Badge>
          {r.acted && <Badge tone="red">{r.isPaper ? "vendeu (simulado)" : "vendeu"}</Badge>}
        </>
      }
      hint={usd(r.valueUsdt)}
    >
      <p className="reasoning">{r.reasoning}</p>
    </Node>
  );
}

const FLOW_ICON: Record<FlowState, string> = { ok: "✔", error: "✘", skipped: "⤼", idle: "·" };

function FlowStrip({ flow }: { flow: CycleView["flow"] }) {
  return (
    <div className="flow" aria-label="Fluxo do ciclo">
      {flow.map((step, i) => (
        <span key={step.key} className="flow-item">
          <span className={`flow-chip flow-${step.state}`} title={step.detail}>
            <span className="flow-mark">{FLOW_ICON[step.state]}</span> {step.label}
            <small>{step.detail}</small>
          </span>
          {i < flow.length - 1 && <span className="flow-arrow">→</span>}
        </span>
      ))}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: Tone }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value${tone ? ` tone-${tone}` : ""}`}>{value}</span>
    </div>
  );
}

function CycleNode({ c }: { c: CycleView }) {
  const s = c.summary;
  const statusBadge =
    c.status === "concluido" ? <Badge tone="green">concluído</Badge>
    : c.status === "em_andamento" ? <Badge tone="blue">em andamento</Badge>
    : <Badge tone="yellow">interrompido</Badge>;

  const a = c.agents;
  return (
    <Node
      level={2}
      icon={c.errors > 0 ? "🔴" : c.warnings > 0 ? "🟡" : "🟢"}
      title={
        <>
          Ciclo {fmtTime(c.startedAt)} <span className="muted">({fmtDay(c.startedAt)})</span>
        </>
      }
      badges={
        <>
          {statusBadge}
          {c.dryRun ? <Badge tone="blue">simulado</Badge> : <Badge tone="red">real</Badge>}
          {s.entries > 0 && <Badge tone="green">{s.entries} entrada(s)</Badge>}
          {s.exits > 0 && <Badge tone="yellow">{s.exits} saída(s)</Badge>}
        </>
      }
      hint={s.headline}
    >
      <FlowStrip flow={c.flow} />

      {/* Nível 3: Agentes e Resumo da operação, lado a lado na hierarquia */}
      <Node level={3} icon="🤖" title="Agentes" hint="gestão → coleta → revisão → comitê → execução">
        <Node
          level={4}
          icon="🛡️"
          title="Gestão de posições abertas"
          badges={<Badge tone={c.flow[0].state === "error" ? "red" : c.flow[0].state === "skipped" ? "muted" : "green"}>{c.flow[0].state === "skipped" ? "não rodou" : "rodou"}</Badge>}
          hint="stop loss · take profit · trailing"
        >
          <LogList lines={a.management.lines} />
        </Node>

        <Node
          level={4}
          icon="📡"
          title="Coleta"
          badges={<Badge tone={c.flow[1].state === "error" ? "red" : c.flow[1].state === "skipped" ? "muted" : "green"}>{c.flow[1].state === "skipped" ? "não rodou" : "rodou"}</Badge>}
          hint={c.flow[1].detail}
        >
          <Node level={5} icon="📰" title="NewsAgent" badges={<Badge>{a.collection.news.count ?? "?"} notícia(s)</Badge>}>
            {a.collection.news.items.length > 0 ? (
              <ul className="loglist">
                {a.collection.news.items.map((n) => (
                  <li key={n.id}>
                    <span className={`log-ts ${n.sentiment >= 0 ? "positive" : "negative"}`}>
                      {n.sentiment >= 0 ? "+" : ""}{n.sentiment.toFixed(2)}
                    </span>
                    <span className="log-msg">{n.summary} <span className="muted">— {n.source}</span></span>
                  </li>
                ))}
              </ul>
            ) : (
              <LogList lines={a.collection.news.lines} />
            )}
          </Node>
          <Node
            level={5}
            icon="💼"
            title="PortfolioAgent"
            badges={<><Badge>patrimônio {usd(a.collection.portfolio.equity)}</Badge><Badge>livre {usd(a.collection.portfolio.free)}</Badge></>}
          >
            {a.collection.portfolio.assets.length > 0 && (
              <div className="table-wrap">
                <table className="mini">
                  <thead><tr><th>Ativo</th><th>Quantidade</th><th>Valor (USDT)</th></tr></thead>
                  <tbody>
                    {a.collection.portfolio.assets.map((w) => (
                      <tr key={w.asset}><td>{w.asset}</td><td>{qty(w.quantity)}</td><td>{usd(w.valueUsdt)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <LogList lines={a.collection.portfolio.lines} />
          </Node>
          <Node
            level={5}
            icon="🔍"
            title="MarketScannerAgent"
            badges={<><Badge>{a.collection.scanner.detected ?? "?"} detectada(s)</Badge><Badge>{a.collection.scanner.evaluated} avaliada(s)</Badge></>}
          >
            <LogList lines={a.collection.scanner.lines} />
          </Node>
          {a.collection.other.length > 0 && (
            <Node level={5} icon="…" title="Outros registros da coleta"><LogList lines={a.collection.other} /></Node>
          )}
        </Node>

        <Node
          level={4}
          icon="🧐"
          title="Revisão de posições pré-existentes"
          badges={
            <>
              <Badge tone={c.flow[2].state === "skipped" ? "muted" : "green"}>{c.flow[2].state === "skipped" ? "não rodou" : "rodou"}</Badge>
              <Badge>{a.review.reviews.length} revisada(s)</Badge>
              {s.reviewSells > 0 && <Badge tone="red">{s.reviewSells} venda(s)</Badge>}
            </>
          }
          hint="PositionReviewAgent"
        >
          {a.review.reviews.length > 0 ? a.review.reviews.map((r) => <ReviewNode key={r.id} r={r} />) : <LogList lines={a.review.lines} />}
        </Node>

        <Node
          level={4}
          icon="⚖️"
          title="Comitê (oportunidades avaliadas)"
          badges={
            <>
              <Badge tone={c.flow[3].state === "skipped" ? "muted" : "green"}>{c.flow[3].state === "skipped" ? "não rodou" : "rodou"}</Badge>
              <Badge tone="green">{s.approved} aprovada(s)</Badge>
              <Badge tone="red">{s.rejected} reprovada(s)</Badge>
            </>
          }
          hint="portfólio → viabilidade → comitê de risco"
        >
          {a.committee.opportunities.length > 0 ? (
            a.committee.opportunities.map((o) => <OpportunityNode key={o.id} o={o} />)
          ) : (
            <Empty>{c.flow[3].state === "skipped" ? c.flow[3].detail : "Nenhuma oportunidade avaliada."}</Empty>
          )}
          {a.committee.lines.some((l) => l.level !== "INFO") && (
            <Node level={5} icon="⚠️" title="Avisos do comitê"><LogList lines={a.committee.lines.filter((l) => l.level !== "INFO")} /></Node>
          )}
        </Node>

        <Node
          level={4}
          icon="🚀"
          title="Execução"
          badges={
            <>
              <Badge tone={s.entries > 0 ? "green" : "muted"}>{s.entries} entrada(s)</Badge>
              <Badge tone={s.exits > 0 ? "yellow" : "muted"}>{s.exits} saída(s)</Badge>
            </>
          }
          hint="ExecutionAgent"
        >
          <Node level={5} icon="🎯" title="Entradas" badges={<Badge tone={s.entries > 0 ? "green" : "muted"}>{s.entries}</Badge>}>
            <TradeTable trades={a.execution.entries} kind="entry" />
          </Node>
          <Node level={5} icon="🚪" title="Saídas" badges={<Badge tone={s.exits > 0 ? "yellow" : "muted"}>{s.exits}</Badge>}>
            <TradeTable trades={a.execution.exits} kind="exit" />
          </Node>
        </Node>
      </Node>

      <Node level={3} icon="📋" title="Resumo da operação" hint={s.headline}>
        <p className="headline">{s.headline}</p>
        <div className="stats">
          <Stat label="Início" value={`${fmtTimeSec(c.startedAt)} (${c.durationSec}s)`} />
          <Stat label="Modo" value={c.dryRun ? "simulado" : "real"} tone={c.dryRun ? "blue" : "red"} />
          <Stat label="Patrimônio" value={usd(s.equity)} />
          <Stat label="Saldo livre" value={usd(s.free)} />
          <Stat label="Notícias" value={s.news ?? "—"} />
          <Stat label="Oportunidades detectadas" value={s.detected ?? "—"} />
          <Stat label="Avaliadas" value={s.evaluated} />
          <Stat label="Aprovadas" value={s.approved} tone={s.approved > 0 ? "green" : undefined} />
          <Stat label="Reprovadas" value={s.rejected} tone={s.rejected > 0 ? "red" : undefined} />
          <Stat label="Entradas" value={s.entries} tone={s.entries > 0 ? "green" : undefined} />
          <Stat label="Saídas" value={s.exits} tone={s.exits > 0 ? "yellow" : undefined} />
          <Stat label="Revisões de carteira" value={`${s.reviews} (${s.reviewSells} venda)`} />
        </div>
        {s.topRejection && <p className="reasoning"><strong>Principal motivo de reprovação:</strong> {s.topRejection}</p>}
        <Node
          level={4}
          icon="⚠️"
          title="Alertas e erros"
          badges={<><Badge tone={c.warnings > 0 ? "yellow" : "muted"}>{c.warnings} aviso(s)</Badge><Badge tone={c.errors > 0 ? "red" : "muted"}>{c.errors} erro(s)</Badge></>}
        >
          <LogList lines={s.alerts} />
        </Node>
        <Node level={4} icon="🧾" title="Log completo do ciclo" badges={<Badge>{s.allLines.length} linhas</Badge>}>
          <LogList lines={s.allLines} />
        </Node>
      </Node>
    </Node>
  );
}

export default function OperationsTree({ cycles }: { cycles: CycleView[] }) {
  return (
    <div className="tree">
      <Node level={1} icon="📈" title="Ver operações" badges={<Badge>{cycles.length} ciclo(s)</Badge>} hint="últimos ciclos do bot">
        {cycles.length === 0 ? (
          <Empty>Nenhum ciclo registrado ainda. Os ciclos aparecem aqui depois que o bot gravar logs (bot_logs).</Empty>
        ) : (
          cycles.map((c) => <CycleNode key={c.id} c={c} />)
        )}
      </Node>
    </div>
  );
}

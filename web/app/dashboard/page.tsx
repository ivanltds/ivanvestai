import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";
import { getCached } from "@/lib/redis";
import { resolveDisplayCurrencyPreference } from "@/lib/fx";
import { loadActiveAccounts, resolveSelectedAccountId } from "@/lib/accounts";
import AccountTabs from "@/components/account-tabs";
import AutoRefresh from "./auto-refresh";
import KillSwitch from "./kill-switch";
import WalletGrid from "./wallet-grid";
import { CurrencyProvider } from "./currency-context";
import Money, { CurrencyUnavailableNotice } from "./money";

// Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 6): o /dashboard não
// tinha NENHUM mecanismo de atualização automática -- só refletia dados novos
// depois de um F5 manual, diferente de /operations (que já usa esse mesmo
// componente AutoRefresh a cada 30s). force-dynamic garante que cada refresh
// busca dados de verdade do servidor (mesmo padrão de app/operations/page.tsx).
export const dynamic = "force-dynamic";

interface Position {
  id: string;
  pair: string;
  quantity: number;
  avg_entry_price: number;
  status: string;
}

interface Trade {
  id: string;
  pair: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
}

interface WalletRow {
  asset: string;
  quantity: number;
  value_usdt: number;
  avg_buy_price: number | null;
  timestamp: string;
}

interface PositionReviewRow {
  asset: string;
  decision: "hold" | "sell";
  confidence: number;
  reasoning: string;
  acted: boolean;
  is_paper: boolean;
}

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ account?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  // multi-conta-plano.md, Fase E (ver 10.9): "todas as contas" (sem filtro,
  // comportamento de sempre -- soma das duas) ou uma conta só, filtrando
  // posições/operações/carteira/revisões por account_id. bot_status e
  // display_currency continuam sempre globais (kill-switch por conta ainda
  // não existe, ver seção 5.3 do plano).
  const accounts = await loadActiveAccounts();
  const { account: requestedAccountId } = await searchParams;
  const selectedAccountId = resolveSelectedAccountId(accounts, requestedAccountId);
  const selectedAccount = accounts.find((a) => a.id === selectedAccountId) ?? null;

  // O cache "balance" no Redis é a SOMA de todas as contas (ver
  // orchestrator/cycle_runner.py, linha do redis_bridge.cache_set) -- correto
  // pra visão "todas as contas", mas errado pra uma conta só. Com conta
  // selecionada, ignora o cache e usa sempre o snapshot do Postgres (já
  // filtrado abaixo), com a mesma nota de "último snapshot" que aparece
  // quando o cache está vazio.
  const cachedBalance = selectedAccountId
    ? null
    : await getCached<{ total_equity_usdt: number }>("balance");

  // Moeda de exibição escolhida em /settings (BRL é o default -- muda pra
  // USDT lá). A cotação USDT->BRL em si é buscada no CLIENT (ver
  // currency-context.tsx) -- buscar aqui no servidor batia no bloqueio
  // geográfico da Binance pra IPs dos EUA (região padrão da função
  // serverless da Vercel). Aqui só decide qual moeda mostrar.
  // bot_status e display_currency são sempre "master" (account_id IS NULL) --
  // filtro explícito desde 25/09/2026 (multi-conta-plano.md, Fase E, ver 10.8:
  // settings ganhou chave composta, então key sozinha não garante mais uma
  // linha só por chave).
  const botStatusSetting = await query<{ value: string }>(
    `select value from settings where key = 'bot_status' and account_id is null limit 1`
  );
  const initialBotStatus = botStatusSetting[0]?.value === "running" ? "running" : "paused";

  const currencySetting = await query<{ value: string }>(
    `select value from settings where key = 'display_currency' and account_id is null limit 1`
  );
  const currency = resolveDisplayCurrencyPreference(currencySetting[0]?.value);

  // is_paper = false é essencial aqui: com o dry-run (arquitetura-tecnica.md
  // 9.6), positions/trades também recebem registros SIMULADOS (is_paper=true)
  // quando run_cycle_once.py é usado pra testar a orquestração real. Esse
  // dashboard mostra só operação de capital real -- nunca misturar.
  //
  // IMPORTANTE (achado em 16/09/2026, ver arquitetura-tecnica.md 9.7): isto
  // aqui é "posição que o BOT abriu" (via ExecutionAgent, com stop/take
  // definidos) -- fica vazio sempre que o bot nunca executou uma ordem de
  // verdade, mesmo que a carteira real na Binance tenha ativos comprados
  // manualmente antes de o bot existir. O card "Carteira na Binance" logo
  // abaixo é a carteira de verdade (o que o PortfolioAgent lê direto da
  // Binance a cada ciclo, independente de quem comprou o quê).
  const openPositions = await query<Position>(
    selectedAccountId
      ? `select id, pair, quantity, avg_entry_price, status from positions
         where status = 'open' and is_paper = false and account_id = $1 order by opened_at desc`
      : `select id, pair, quantity, avg_entry_price, status from positions
         where status = 'open' and is_paper = false order by opened_at desc`,
    selectedAccountId ? [selectedAccountId] : []
  );

  const todayTrades = await query<Trade>(
    selectedAccountId
      ? `select id, pair, side, quantity, price, timestamp from trades
         where timestamp >= date_trunc('day', now()) and is_paper = false and account_id = $1
         order by timestamp desc limit 50`
      : `select id, pair, side, quantity, price, timestamp from trades
         where timestamp >= date_trunc('day', now()) and is_paper = false order by timestamp desc limit 50`,
    selectedAccountId ? [selectedAccountId] : []
  );

  // Snapshot mais recente por ativo (não a série histórica inteira) -- é o
  // retrato da carteira real na última vez que o PortfolioAgent rodou.
  const walletRows = await query<WalletRow>(
    // Só o ÚLTIMO lote (o snapshot de um ciclo inteiro). O PortfolioAgent grava
    // apenas os ativos com saldo > 0, então um `distinct on (asset)` sobre todo o
    // histórico mantinha "fantasmas": ativos já vendidos (ex: SUI, XRP) apareciam
    // com o último valor que tinham e inflavam o total (achado em 19/09/2026:
    // dashboard $82,76 vs Binance $63,00). Um lote = linhas até 2 min antes do
    // snapshot mais recente (um ciclo grava tudo em poucos segundos). Com conta
    // selecionada, o "mais recente" também é só dessa conta (cada conta grava seu
    // próprio lote dentro do mesmo ciclo, em momentos ligeiramente diferentes).
    selectedAccountId
      ? `select distinct on (asset) asset, quantity, value_usdt, avg_buy_price, timestamp
         from wallet_snapshots
         where account_id = $1
           and timestamp >= (select max(timestamp) from wallet_snapshots where account_id = $1) - interval '2 minutes'
         order by asset, timestamp desc`
      : `select distinct on (asset) asset, quantity, value_usdt, avg_buy_price, timestamp
         from wallet_snapshots
         where timestamp >= (select max(timestamp) from wallet_snapshots) - interval '2 minutes'
         order by asset, timestamp desc`,
    selectedAccountId ? [selectedAccountId] : []
  );

  // Veredito mais recente do PositionReviewAgent por ativo ("vale manter ou
  // vender?", ver arquitetura-tecnica.md 9.8). Tabela nova -- em try/catch
  // pra não derrubar o dashboard inteiro se ainda não existir no banco (basta
  // rodar `python -m db.init_db` de novo, é idempotente).
  let positionReviews: PositionReviewRow[] = [];
  try {
    positionReviews = await query<PositionReviewRow>(
      selectedAccountId
        ? `select distinct on (asset) asset, decision, confidence, reasoning, acted, is_paper
           from position_reviews where account_id = $1 order by asset, timestamp desc`
        : `select distinct on (asset) asset, decision, confidence, reasoning, acted, is_paper
           from position_reviews order by asset, timestamp desc`,
      selectedAccountId ? [selectedAccountId] : []
    );
  } catch {
    positionReviews = [];
  }
  const reviewByAsset = new Map(positionReviews.map((r) => [r.asset, r]));

  const walletTotal = walletRows.reduce((sum, r) => sum + r.value_usdt, 0);
  const walletAsOf = walletRows.length
    ? walletRows.reduce((latest, r) => (r.timestamp > latest ? r.timestamp : latest), walletRows[0].timestamp)
    : null;

  // Prioriza o cache do Redis (atualizado a cada ciclo, quando o bot está
  // rodando continuamente) -- se estiver vazio/expirado (TTL de 60s, ver
  // core/redis_bridge.py) ou uma conta específica estiver selecionada (o
  // cache é sempre a soma de todas), cai pro último snapshot salvo no
  // Postgres em vez de mostrar zero ou o total errado.
  const totalEquity = cachedBalance?.total_equity_usdt ?? walletTotal;

  return (
    <CurrencyProvider currency={currency}>
      <div>
        <AutoRefresh seconds={20} />

        <AccountTabs basePath="/dashboard" accounts={accounts} selectedAccountId={selectedAccountId} />

        {selectedAccount && (
          <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12, maxWidth: 520, lineHeight: 1.5 }}>
            Mostrando só <strong>{selectedAccount.label}</strong>: balanço, carteira, posições e operações de hoje
            filtrados por essa conta. Status do bot é sempre o mesmo pras duas contas (kill-switch por conta ainda
            não existe).
          </p>
        )}

        <div className="grid grid-2">
          <div className="card">
            <div style={{ color: "var(--muted)", fontSize: 13 }}>Balanço geral</div>
            <div style={{ fontSize: 28, fontWeight: 700 }}>
              <Money usdt={totalEquity} />
            </div>
            <CurrencyUnavailableNotice />
            {!cachedBalance && walletAsOf && (
              <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>
                último snapshot: {new Date(walletAsOf).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}
              </div>
            )}
          </div>
          <div className="card">
            <div style={{ color: "var(--muted)", fontSize: 13 }}>Status do bot</div>
            <KillSwitch initialStatus={initialBotStatus} />
          </div>
        </div>

        <div className="card">
          <h2 style={{ fontSize: 16 }}>Carteira na Binance</h2>
          <p style={{ color: "var(--muted)", fontSize: 12 }}>
            A cada ciclo, o PositionReviewAgent avalia se ainda vale manter cada posição
            pré-existente (as que o bot não abriu) e vende sozinho quando a confiança é alta
            e o bot está fora do modo simulado — passe o mouse na etiqueta &quot;IA&quot; pra ver o motivo.
          </p>
          {walletRows.length === 0 && (
            <p style={{ color: "var(--muted)" }}>
              Nenhum snapshot ainda -- roda o PortfolioAgent (parte de um ciclo do bot) pra popular.
            </p>
          )}
          {walletRows.length > 0 && (
            <WalletGrid
              rows={walletRows.map((r) => {
                const review = reviewByAsset.get(r.asset);
                return {
                  asset: r.asset,
                  quantity: r.quantity,
                  valueUsdt: r.value_usdt,
                  review: review
                    ? {
                        decision: review.decision,
                        confidence: review.confidence,
                        reasoning: review.reasoning,
                        acted: review.acted,
                        isPaper: review.is_paper,
                      }
                    : null,
                };
              })}
            />
          )}
        </div>

        <div className="card">
          <h2 style={{ fontSize: 16 }}>Posições abertas pelo bot</h2>
          <p style={{ color: "var(--muted)", fontSize: 12 }}>
            Só operações que o bot executou de fato (com stop/take definidos) — não é a carteira toda.
          </p>
          {openPositions.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma posição aberta pelo bot.</p>}
          {openPositions.map((p) => (
            <div key={p.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
              <span>{p.pair}</span>
              <span>{p.quantity} @ <Money usdt={p.avg_entry_price} /></span>
            </div>
          ))}
        </div>

        <div className="card">
          <h2 style={{ fontSize: 16 }}>Operações de hoje</h2>
          {todayTrades.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma operação hoje.</p>}
          {todayTrades.map((t) => (
            <div key={t.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}>
              <span>{t.pair} — {t.side.toUpperCase()}</span>
              <span>{t.quantity} @ <Money usdt={t.price} /></span>
            </div>
          ))}
          <a href="/api/export/trades" style={{ fontSize: 13 }}>Exportar CSV</a>
        </div>
      </div>
    </CurrencyProvider>
  );
}

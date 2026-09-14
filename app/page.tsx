import { Redis } from '@upstash/redis'
import Link from 'next/link'
import {
  TrendingUp,
  TrendingDown,
  PieChart as PieChartIcon,
  Radio,
  ExternalLink,
  Sliders,
  BookOpen,
  ArrowUpRight,
  ArrowDownRight,
  Cpu,
  Zap,
  FlaskConical,
  Clock,
  CheckCircle2,
  DollarSign,
  Wallet,
  Activity,
  Bot,
} from 'lucide-react'
import PortfolioPieChart from './components/PortfolioPieChart'
import DailyTradingSummaryCard from './components/DailyTradingSummaryCard'
import CurrentDaytradeSession from './components/CurrentDaytradeSession'
import FloatingCommandRoom from './components/FloatingCommandRoom'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '',
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '',
})

export default async function DashboardPage() {
  // Puxa os dados em paralelo do Redis
  const [
    openPositionsRaw,
    sentimentRaw,
    pnlHistoryRaw,
    auditLogsRaw,
    accountBalancesRaw,
  ] = await Promise.all([
    redis.get<any>('portfolio:open_positions'),
    redis.get<any>('dashboard:current_sentiment'),
    redis.lrange<any>('dashboard:pnl_history', 0, 10),
    redis.lrange<any>('dashboard:audit_logs', 0, 15),
    redis.get<any>('portfolio:account_balances'),
  ])

  // Descodifica URI antes do parse JSON caso venha codificado pelo Python
  const safeParse = (raw: any, defaultObj: any) => {
    if (!raw) return defaultObj
    try {
      const decoded = typeof raw === 'string' ? decodeURIComponent(raw) : raw
      return typeof decoded === 'string' ? JSON.parse(decoded) : decoded
    } catch {
      return defaultObj
    }
  }

  const openPositions = safeParse(openPositionsRaw, {})
  const sentiment = safeParse(sentimentRaw, { is_bullish: true, summary: 'Aguardando consolidação macroeconômica dos agentes.' })
  const accountBalances = safeParse(accountBalancesRaw, {})
  const brlBalance = parseFloat(accountBalances.BRL || 0)
  const usdtBalance = parseFloat(accountBalances.USDT || 0)

  // Arrays de auditoria
  const auditLogs = (auditLogsRaw || []).map((log: any) => safeParse(log, {}))
  const currentPnl = pnlHistoryRaw && pnlHistoryRaw.length > 0 ? safeParse(pnlHistoryRaw[0], {}).value || 0 : 0
  const totalAllocated = Object.values(openPositions).length > 0
    ? Object.values(openPositions).reduce((acc: number, pos: any) => acc + (pos.total_invested || 0), 0)
    : currentPnl

  // Formatações utilitárias
  const formatCurrency = (val: number) =>
    new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val)

  const formatPrice = (val?: number, curr = 'BRL') => {
    if (!val || isNaN(val)) return curr === 'USDT' ? '$ 0,00' : 'R$ 0,00'
    if (curr === 'USDT' || curr === 'USD') {
      return `$ ${val.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`
    }
    return val.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
  }

  return (
    <div className="min-h-screen bg-black text-neutral-200 font-sans p-4 sm:p-8 selection:bg-emerald-500/30">
      {/* CABEÇALHO INSTITUCIONAL */}
      <header className="mb-8 border-b border-neutral-850 pb-6 flex flex-wrap justify-between items-end gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="w-2.5 h-2.5 rounded-full bg-neutral-400"></div>
            <h1 className="text-3xl font-extrabold tracking-tight text-white font-mono">
              Ivanvest<span className="text-neutral-400">AI</span>
            </h1>
          </div>
          <p className="text-xs text-neutral-400 font-mono">Terminal Quantitativo &amp; Gestão de Ativos</p>
        </div>

        <div className="flex items-end gap-6 flex-wrap justify-end">
          {brlBalance > 0 && (
            <div className="text-right border-r border-neutral-800 pr-6">
              <div className="flex items-center justify-end gap-1.5 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-neutral-500"></span>
                <p className="text-[10px] text-neutral-400 font-bold uppercase tracking-wider font-mono">
                  Saldo BRL
                </p>
              </div>
              <p className="text-xl font-bold font-mono text-white">{formatCurrency(brlBalance)}</p>
              <p className="text-[10px] text-neutral-500">Alocação disponível em Reais</p>
            </div>
          )}

          {usdtBalance > 0 && (
            <div className="text-right border-r border-neutral-800 pr-6">
              <div className="flex items-center justify-end gap-1.5 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-neutral-500"></span>
                <p className="text-[10px] text-neutral-400 font-bold uppercase tracking-wider font-mono">
                  Reserva USDT
                </p>
              </div>
              <p className="text-xl font-bold font-mono text-white">
                ${usdtBalance.toFixed(2)} <span className="text-xs text-neutral-400 font-sans">USDT</span>
              </p>
              <p className="text-[10px] text-neutral-500">Caixa em Dólar / Sniper</p>
            </div>
          )}

          <div className="text-right border-r border-neutral-800 pr-6">
            <p className="text-[10px] text-neutral-500 uppercase tracking-wider font-mono mb-1">Patrimônio Alocado</p>
            <p className="text-2xl font-bold font-mono text-white">{formatCurrency(totalAllocated)}</p>
          </div>

          <Link
            href="/settings"
            className="px-3.5 py-2 rounded-xl bg-neutral-900 hover:bg-neutral-800 text-xs font-mono font-semibold text-neutral-200 hover:text-white border border-neutral-800 hover:border-neutral-700 transition-all flex items-center gap-2"
          >
            <Sliders className="w-3.5 h-3.5 text-neutral-400" />
            <span>Configurações</span>
          </Link>
        </div>
      </header>

      {/* GRID SUPERIOR DE 2 COLUNAS */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-8">
        
        {/* COLUNA 1 (ESQUERDA: 40% / lg:col-span-5) */}
        <div className="lg:col-span-5 space-y-6">
          
          {/* 1. SENTIMENTO DE MERCADO (PRIMEIRO ITEM OBRIGATÓRIO) */}
          <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md transition-all">
            <div className="flex items-center justify-between pb-3 border-b border-neutral-800/60 mb-3">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-300">
                  {sentiment.is_bullish ? (
                    <TrendingUp className="w-4 h-4 text-neutral-300" />
                  ) : (
                    <TrendingDown className="w-4 h-4 text-neutral-300" />
                  )}
                </div>
                <div>
                  <h2 className="text-xs font-bold uppercase tracking-wider text-white font-mono">
                    Sentimento de Mercado
                  </h2>
                  <p className="text-[11px] text-neutral-400">Consenso dos agentes analistas</p>
                </div>
              </div>

              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold border ${
                  sentiment.is_bullish
                    ? 'bg-neutral-800 text-neutral-200 border-neutral-700'
                    : 'bg-rose-950/40 text-rose-300 border-rose-800/50'
                }`}
              >
                {sentiment.is_bullish ? 'Bullish (Greed)' : 'Bearish (Fear)'}
              </span>
            </div>

            <div className="p-3.5 rounded-xl bg-neutral-950/70 border border-neutral-850">
              <p className="text-xs text-neutral-300 leading-relaxed font-sans">
                {sentiment.summary}
              </p>
            </div>
          </section>

          {/* 2. BALANÇO DIÁRIO SNIPER (RESULTADOS DO DIA) */}
          <DailyTradingSummaryCard />

          {/* 3. ALOCAÇÃO DAS POSIÇÕES (GRÁFICO DE PIZZA) */}
          <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md transition-all">
            <div className="flex items-center justify-between pb-3 border-b border-neutral-800/60 mb-4">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-neutral-800/70 border border-neutral-700/60 text-neutral-300">
                  <PieChartIcon className="w-4 h-4" />
                </div>
                <div>
                  <h2 className="text-xs font-bold uppercase tracking-wider text-white font-mono">
                    Alocação da Carteira
                  </h2>
                  <p className="text-[11px] text-neutral-400">Distribuição patrimonial por ativo</p>
                </div>
              </div>

              <span className="text-[11px] font-mono text-neutral-400">
                {Object.keys(openPositions).length} ativos
              </span>
            </div>

            {/* Gráfico Recharts */}
            <PortfolioPieChart data={openPositions} />

            {/* Lista detalhada das posições */}
            <div className="space-y-2 mt-2">
              {Object.keys(openPositions).length === 0 ? (
                <p className="text-neutral-500 italic text-xs font-mono py-2 text-center">
                  Carteira vazia no momento.
                </p>
              ) : (
                Object.entries(openPositions).map(([symbol, data]: [string, any]) => {
                  const currentPrice = data.current_price || data.avg_price || 0
                  const lastPrice = data.last_price || currentPrice
                  const pnlPercentage =
                    data.avg_price > 0 ? ((currentPrice - data.avg_price) / data.avg_price) * 100 : 0
                  const isProfiting = pnlPercentage >= 0
                  const wentUp = currentPrice >= lastPrice
                  const [posCoin, posQuote] = symbol.includes('/') ? symbol.split('/') : [symbol, 'BRL']

                  return (
                    <div
                      key={symbol}
                      className="flex justify-between items-center p-2.5 rounded-xl bg-neutral-950/70 border border-neutral-850 hover:border-neutral-750 transition-all font-mono text-xs"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-white text-xs">{posCoin}</span>
                          <span
                            className="flex items-center text-[10px] text-neutral-400"
                            title={wentUp ? 'Alta recente' : 'Baixa recente'}
                          >
                            {wentUp ? (
                              <ArrowUpRight className="w-3 h-3 text-neutral-400" />
                            ) : (
                              <ArrowDownRight className="w-3 h-3 text-neutral-500" />
                            )}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-neutral-800 text-neutral-400 border border-neutral-700/60">
                            {posQuote}
                          </span>
                        </div>
                        <p className="text-[10px] text-neutral-500 mt-0.5">
                          {data.total_coins?.toFixed(6)} moedas
                        </p>
                      </div>

                      <div className="text-right">
                        <p className={`font-bold ${isProfiting ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {formatCurrency(currentPrice * (data.total_coins || 0))}
                        </p>
                        <p className="text-[10px] text-neutral-500 mt-0.5">
                          PM: {formatCurrency(data.avg_price || 0)}{' '}
                          <span className={isProfiting ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
                            ({pnlPercentage > 0 ? '+' : ''}{pnlPercentage.toFixed(2)}%)
                          </span>
                        </p>
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </section>

          {/* 4. FEED DE NOTÍCIAS AO VIVO (ORDENADAS POR MAIS RECENTES, TRADUZIDAS E RESUMIDAS PELA IA) */}
          {auditLogs.length > 0 && auditLogs[0].news_sources && auditLogs[0].news_sources.length > 0 && (() => {
            const sortedNews = [...auditLogs[0].news_sources].sort((a: any, b: any) => {
              const tA = a.timestamp ? Number(a.timestamp) : (a.published_at ? new Date(a.published_at).getTime() / 1000 : 0)
              const tB = b.timestamp ? Number(b.timestamp) : (b.published_at ? new Date(b.published_at).getTime() / 1000 : 0)
              return tB - tA
            })

            return (
              <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md transition-all">
                <div className="flex items-center justify-between pb-3 border-b border-neutral-800/60 mb-3">
                  <div className="flex items-center gap-2">
                    <div className="p-1.5 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400">
                      <Radio className="w-4 h-4" />
                    </div>
                    <div>
                      <h2 className="text-xs font-bold uppercase tracking-wider text-white font-mono">
                        Notícias &amp; Sinais da IA
                      </h2>
                      <p className="text-[11px] text-neutral-400">Traduzidas e resumidas em tempo real</p>
                    </div>
                  </div>

                  <span className="text-[10px] font-mono text-purple-400 bg-purple-950/40 px-2 py-0.5 rounded-full border border-purple-800/50">
                    {sortedNews.length} recentes
                  </span>
                </div>

                <div className="space-y-3 max-h-[500px] overflow-y-auto pr-1.5 custom-scrollbar">
                  {sortedNews.slice(0, 15).map((src: any, idx: number) => {
                    let hostname = src.source || 'Portal Cripto'
                    if (!src.source && src.url) {
                      try {
                        hostname = new URL(src.url).hostname.replace('www.', '')
                      } catch {}
                    }

                    const pubDateStr = src.published_at_str || (src.published_at ? new Date(src.published_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '')

                    return (
                      <div
                        key={idx}
                        className="p-3.5 rounded-xl border border-neutral-850 bg-neutral-950/70 hover:border-neutral-750 transition-all space-y-2 group"
                      >
                        {/* Meta: Fonte e Data/Horário de Publicação */}
                        <div className="flex items-center justify-between text-[10px] font-mono">
                          <span className="px-1.5 py-0.5 rounded bg-neutral-900 text-neutral-300 font-bold uppercase tracking-wider border border-neutral-800">
                            {hostname}
                          </span>
                          {pubDateStr && (
                            <span className="flex items-center gap-1 text-neutral-400">
                              <Clock className="w-3 h-3 text-neutral-500" />
                              {pubDateStr}
                            </span>
                          )}
                        </div>

                        {/* Título Traduzido para Português */}
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="block group/link"
                        >
                          <h3 className="text-xs font-semibold text-white group-hover/link:text-purple-300 transition-colors leading-snug font-sans flex items-start justify-between gap-2">
                            <span>{src.title_pt || src.title}</span>
                            <ExternalLink className="w-3.5 h-3.5 text-neutral-600 group-hover/link:text-purple-400 shrink-0 mt-0.5 transition-colors" />
                          </h3>
                        </a>

                        {/* Resumo Curto Feito pelo Agente */}
                        {src.summary_pt && (
                          <div className="p-2.5 rounded-lg bg-neutral-900/60 border border-neutral-850 text-[11px] text-neutral-300/90 leading-relaxed font-sans">
                            <span className="text-[9px] font-mono font-bold uppercase tracking-wider text-purple-400 block mb-1 flex items-center gap-1">
                              <Bot className="w-3 h-3" />
                              Resumo do Agente:
                            </span>
                            {src.summary_pt}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </section>
            )
          })()}

        </div>

        {/* COLUNA 2 (DIREITA: 60% / lg:col-span-7) */}
        <div className="lg:col-span-7 space-y-6">
          <CurrentDaytradeSession />

          {/* DIÁRIO DE BORDO & AUDIT LOG DO FUNDO */}
          <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md">
        <div className="flex items-center justify-between pb-4 border-b border-neutral-800/80 mb-6">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-neutral-800 border border-neutral-700 text-neutral-300">
              <BookOpen className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold uppercase tracking-wider text-white font-mono">
                Diário de Bordo &amp; Auditoria do Fundo
              </h2>
              <p className="text-xs text-neutral-400">
                Registro cronológico detalhado de todos os ciclos executados pelos agentes autônomos
              </p>
            </div>
          </div>

          <span className="text-xs font-mono text-neutral-500">
            {auditLogs.length} ciclos registrados
          </span>
        </div>

        <div className="space-y-4 max-h-[600px] overflow-y-auto pr-2 custom-scrollbar">
          {auditLogs.length === 0 ? (
            <p className="text-neutral-500 italic text-xs font-mono py-8 text-center">
              Nenhum log de ciclo executado registrado.
            </p>
          ) : (
            auditLogs.map((log: any, idx: number) => {
              const entry = log
              const date = new Date((entry.timestamp || 0) * 1000).toLocaleString('pt-BR')
              const isSimulation = entry.dry_run === true
              const isLive = entry.dry_run === false

              return (
                <div
                  key={idx}
                  className={`p-4 rounded-xl border relative pl-6 transition-all ${
                    isSimulation
                      ? 'border-amber-900/40 bg-amber-950/10'
                      : isLive
                      ? 'border-emerald-900/40 bg-emerald-950/10'
                      : 'border-neutral-850 bg-neutral-950/50'
                  }`}
                >
                  {/* Linha da timeline */}
                  <div className="absolute left-[11px] top-8 bottom-[-16px] w-[2px] bg-neutral-800 z-0"></div>
                  <div
                    className={`absolute left-2 top-4 w-3 h-3 rounded-full border-[3px] border-neutral-950 z-10 ${
                      isSimulation
                        ? 'bg-amber-400'
                        : isLive
                        ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]'
                        : 'bg-neutral-600'
                    }`}
                  ></div>

                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <p className="text-xs text-neutral-500 font-mono flex items-center gap-1">
                      <Clock className="w-3 h-3 text-neutral-600" />
                      {date}
                    </p>
                    {isSimulation && (
                      <span className="flex items-center gap-1 px-2 py-0.5 bg-amber-500/10 text-amber-400 text-[10px] font-mono font-bold rounded-full border border-amber-500/30 tracking-wider">
                        <FlaskConical className="w-3 h-3" />
                        SIMULAÇÃO
                      </span>
                    )}
                    {isLive && (
                      <span className="flex items-center gap-1 px-2 py-0.5 bg-neutral-800 text-neutral-300 text-[10px] font-mono font-bold rounded-full border border-neutral-700 tracking-wider">
                        <Zap className="w-3 h-3 text-neutral-400" />
                        REAL
                      </span>
                    )}
                  </div>

                  <p className="text-xs text-neutral-300 mb-3 leading-relaxed font-sans">
                    {entry.news_summary}
                  </p>

                  {/* Fontes consultadas */}
                  {entry.news_sources && entry.news_sources.length > 0 && (
                    <details className="mb-3 group bg-neutral-900/50 border border-neutral-800 rounded-lg text-xs text-neutral-400">
                      <summary className="p-2 cursor-pointer font-bold font-mono text-neutral-300 hover:text-white transition-colors focus:outline-none flex items-center justify-between">
                        <span>Fontes Consultadas ({entry.news_sources.length})</span>
                      </summary>
                      <div className="p-3 pt-0 border-t border-neutral-800/50 mt-1">
                        <ul className="list-disc pl-4 space-y-1.5 mt-2 font-mono text-[11px]">
                          {entry.news_sources.map((src: any, sIdx: number) => (
                            <li key={sIdx}>
                              <a
                                href={src.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hover:text-purple-400 underline decoration-neutral-700 underline-offset-2"
                              >
                                {src.title_pt || src.title}
                              </a>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </details>
                  )}

                  {/* Lição da IA */}
                  {entry.learned_lessons && (
                    <div className="mb-3 p-3 bg-neutral-900 border border-amber-500/30 rounded-xl text-xs text-amber-200/90 font-mono shadow-sm">
                      <strong className="text-amber-400 mb-1 flex items-center gap-1.5 uppercase tracking-wider text-[11px]">
                        <Cpu className="w-3.5 h-3.5 text-amber-400" />
                        Lição da IA (Memory Agent):
                      </strong>
                      <p className="font-sans leading-relaxed">{entry.learned_lessons}</p>
                    </div>
                  )}

                  {/* Diretrizes obedecidas */}
                  {entry.directives_applied && (
                    <div className="mb-3 p-2 bg-purple-950/40 border border-purple-800/40 rounded-lg text-xs font-mono text-purple-300 inline-block">
                      <strong>Diretriz Obedecida:</strong> {entry.directives_applied}
                    </div>
                  )}

                  {/* Trades efetuados no ciclo */}
                  {entry.trades && entry.trades.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                      {entry.trades.map((t: any, i: number) => {
                        const isBuy = (t.action || 'BUY').toUpperCase() === 'BUY'
                        const parts = (t.symbol || '').split('/')
                        const baseAsset = parts[0] || t.symbol || 'CRYPTO'
                        const quoteAsset = parts[1] || 'BRL'
                        const fromAsset = t.from_asset || (isBuy ? quoteAsset : baseAsset)
                        const toAsset = t.to_asset || (isBuy ? baseAsset : quoteAsset)

                        return (
                          <div
                            key={i}
                            className="p-3 rounded-xl border border-neutral-800 bg-neutral-950/70 transition-all font-mono text-xs"
                          >
                            <div className="flex justify-between items-center mb-2">
                              <div className="flex items-center gap-2">
                                <span className="px-2 py-0.5 text-[9px] font-bold rounded uppercase tracking-wider border bg-neutral-800 text-neutral-300 border-neutral-700">
                                  {isBuy ? 'COMPRA' : 'VENDA'}
                                </span>
                                <div className="flex items-center gap-1 text-[11px] font-bold text-neutral-300">
                                  <span className="text-neutral-400">{fromAsset}</span>
                                  <span className="text-neutral-600">➔</span>
                                  <span className="text-white">{toAsset}</span>
                                </div>
                              </div>
                              <span className="text-[11px] text-neutral-400 font-semibold">{t.symbol}</span>
                            </div>

                            <div className="grid grid-cols-2 gap-2 text-[11px] p-2 rounded-lg bg-neutral-950/60 border border-neutral-850">
                              <div>
                                <span className="text-[9px] text-neutral-500 uppercase block">
                                  {isBuy ? 'Pago' : 'Entregue'}
                                </span>
                                <span className="font-bold text-white block">
                                  {isBuy
                                    ? t.fiat_amount
                                      ? formatCurrency(t.fiat_amount)
                                      : '-'
                                    : t.crypto_qty
                                    ? `${t.crypto_qty.toFixed(6)} ${baseAsset}`
                                    : '-'}
                                </span>
                              </div>
                              <div>
                                <span className="text-[9px] text-neutral-500 uppercase block">
                                  {isBuy ? 'Recebido' : 'Retorno Fiat'}
                                </span>
                                <span className="font-bold text-white block">
                                  {isBuy
                                    ? t.crypto_qty
                                      ? `${t.crypto_qty.toFixed(6)} ${baseAsset}`
                                      : '-'
                                    : t.fiat_amount
                                    ? formatCurrency(t.fiat_amount)
                                    : '-'}
                                </span>
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  ) : null}
                </div>
              )
            })
          )}
        </div>
      </section>
    </div>
  </div>

      {/* SALA DE COMANDO FLUTUANTE (FAB + DRAWER) */}
      <FloatingCommandRoom />
    </div>
  )
}

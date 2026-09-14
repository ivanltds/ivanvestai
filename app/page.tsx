import { Redis } from '@upstash/redis'
import { revalidatePath } from 'next/cache'
import Link from 'next/link'
import PortfolioPieChart from './components/PortfolioPieChart'
import SniperDaytradePanel from './components/SniperDaytradePanel'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '',
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '',
})

export default async function DashboardPage() {
  // 1. Puxa os dados em paralelo
  const [
    openPositionsRaw, 
    sentimentRaw, 
    pnlHistoryRaw, 
    auditLogsRaw, 
    activeDirective,
    accountBalancesRaw,
    daytradeSessionRaw,
    daytradeTradesRaw,
  ] = await Promise.all([
    redis.get<any>('portfolio:open_positions'),
    redis.get<any>('dashboard:current_sentiment'),
    redis.lrange<any>('dashboard:pnl_history', 0, 10),
    redis.lrange<any>('dashboard:audit_logs', 0, 10),
    redis.get<string>('ai:user_directives'),
    redis.get<any>('portfolio:account_balances'),
    redis.get<any>('daytrade:session'),
    redis.lrange<any>('daytrade:trades', 0, 40),
  ])

  // Como o Python salva com urllib.parse.quote, precisamos de-codificar o URL (ex: %7B vira {) antes do JSON parse
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
  const sentiment = safeParse(sentimentRaw, { is_bullish: true, summary: "Nenhum dado" })
  const accountBalances = safeParse(accountBalancesRaw, {})
  const brlBalance = parseFloat(accountBalances.BRL || 0)
  const usdtBalance = parseFloat(accountBalances.USDT || 0)
  
  // Sessão e Micro-trades de Day Trade
  const daytradeSession = safeParse(daytradeSessionRaw, null)
  const daytradeTrades = (daytradeTradesRaw || []).map((t: any) => safeParse(t, {})).reverse()
  
  // Para arrays vindos do Upstash
  const auditLogs = (auditLogsRaw || []).map((log: any) => safeParse(log, {}))
  const currentPnl = pnlHistoryRaw && pnlHistoryRaw.length > 0 ? safeParse(pnlHistoryRaw[0], {}).value || 0 : 0
  const totalAllocated = Object.values(openPositions).length > 0
    ? Object.values(openPositions).reduce((acc: number, pos: any) => acc + (pos.total_invested || 0), 0)
    : currentPnl

  // Formatações
  const formatCurrency = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val)
  const formatDateTime = (val: any) => {
    if (!val) return ''
    const num = Number(val)
    if (!isNaN(num) && num > 0) {
      return new Date(num < 1e11 ? num * 1000 : num).toLocaleString('pt-BR')
    }
    return new Date(val).toLocaleString('pt-BR')
  }
  
  // Action para injetar diretriz
  async function submitDirective(formData: FormData) {
    'use server'
    const directive = formData.get('directive') as string
    const hours = parseInt(formData.get('hours') as string)
    
    if (directive && hours > 0) {
      const expirationSeconds = hours * 3600
      await redis.set('ai:user_directives', directive, { ex: expirationSeconds })
      revalidatePath('/')
    }
  }

  // Action para limpar diretriz
  async function clearDirective() {
    'use server'
    await redis.del('ai:user_directives')
    revalidatePath('/')
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-200 font-sans p-8 selection:bg-emerald-500/30">
      <header className="mb-12 border-b border-neutral-800 pb-6 flex justify-between items-end">
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight text-white mb-2">Ivanvest<span className="text-emerald-500">AI</span></h1>
          <p className="text-neutral-400">Terminal Quantitativo &amp; Diário de Bordo</p>
        </div>
        <div className="flex items-end gap-6 flex-wrap justify-end">
          {brlBalance > 0 && (
            <div className="text-right border-r border-neutral-800 pr-6">
              <div className="flex items-center justify-end gap-1.5 mb-1">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                <p className="text-xs text-amber-400 font-bold uppercase tracking-wider">Aporte BRL em Trânsito</p>
              </div>
              <p className="text-2xl font-bold font-mono text-white">{formatCurrency(brlBalance)}</p>
              <p className="text-[10px] text-neutral-400">Pronto p/ direcionar a ativos</p>
            </div>
          )}
          {usdtBalance > 0 && (
            <div className="text-right border-r border-neutral-800 pr-6">
              <div className="flex items-center justify-end gap-1.5 mb-1">
                <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                <p className="text-xs text-emerald-400 font-bold uppercase tracking-wider">Reserva em Dólar</p>
              </div>
              <p className="text-2xl font-bold font-mono text-white">${usdtBalance.toFixed(2)} <span className="text-xs text-neutral-400 font-sans">USDT</span></p>
              <p className="text-[10px] text-neutral-400">Hedge / Proteção Cambial</p>
            </div>
          )}
          <div className="text-right">
            <p className="text-sm text-neutral-500 mb-1">Total Alocado (Histórico)</p>
            <p className="text-3xl font-bold text-white">{formatCurrency(totalAllocated)}</p>
          </div>

          <Link href="/settings" className="px-4 py-2 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-sm font-semibold text-white transition-colors flex items-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Configurações
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* COLUNA ESQUERDA: Posições e Sentimento */}
        <div className="space-y-8">
          <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6 backdrop-blur-sm">
            <h2 className="text-xl font-bold text-white mb-4 flex items-center">
              <span className="w-2 h-2 rounded-full bg-amber-500 mr-2"></span>
              Posições Abertas
            </h2>
            
            {/* Gráfico de Pizza */}
            <PortfolioPieChart data={openPositions} />

            <div className="space-y-3">
              {Object.keys(openPositions).length === 0 && (
                <p className="text-neutral-500 italic">Carteira vazia no momento.</p>
              )}
              {Object.entries(openPositions).map(([symbol, data]: [string, any]) => {
                const currentPrice = data.current_price || data.avg_price || 0;
                const lastPrice = data.last_price || currentPrice;
                
                const pnlPercentage = data.avg_price > 0 ? ((currentPrice - data.avg_price) / data.avg_price) * 100 : 0;
                const isProfiting = pnlPercentage >= 0;
                
                // Setas indicando se subiu ou caiu desde a última execução
                const wentUp = currentPrice >= lastPrice;
                const [posCoin, posQuote] = symbol.includes('/') ? symbol.split('/') : [symbol, 'BRL'];

                return (
                  <div key={symbol} className="flex justify-between items-center p-3 rounded-lg bg-neutral-900 border border-neutral-800/50">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-bold text-white flex items-center gap-1.5">
                          {posCoin} 
                          <span className="text-xs" title={wentUp ? "Subiu desde a última execução" : "Caiu desde a última execução"}>
                            {wentUp ? '🟢 ⬆️' : '🔴 ⬇️'}
                          </span>
                        </p>
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-neutral-800/80 border border-neutral-700/50 text-neutral-400">
                          {posQuote} ➔ {posCoin}
                        </span>
                      </div>
                      <p className="text-xs text-neutral-500 mt-0.5">{data.total_coins.toFixed(6)} moedas</p>
                    </div>
                    <div className="text-right">
                      <p className={`font-mono text-sm ${isProfiting ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {formatCurrency(currentPrice * data.total_coins)}
                      </p>
                      <div className="text-xs text-neutral-500 flex flex-col gap-0.5 mt-1">
                        <p>PM: {formatCurrency(data.avg_price)}</p>
                        <p>Atual: {formatCurrency(currentPrice)} <span className={isProfiting ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>({pnlPercentage > 0 ? '+' : ''}{pnlPercentage.toFixed(2)}%)</span></p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6 backdrop-blur-sm">
            <h2 className="text-xl font-bold text-white mb-4 flex items-center">
              <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
              Sentimento de Mercado
            </h2>
            <div className={`p-4 rounded-xl border ${
              sentiment.is_bullish ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'
            }`}>
              <h3 className="font-bold text-lg mb-2">
                {sentiment.is_bullish ? '🐂 Bull Market (Greed)' : '🐻 Bear Market (Fear)'}
              </h3>
              <p className="text-sm opacity-90 leading-relaxed">{sentiment.summary}</p>
            </div>
          </section>

          {auditLogs.length > 0 && auditLogs[0].news_sources && auditLogs[0].news_sources.length > 0 && (
            <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6 backdrop-blur-sm">
              <h2 className="text-xl font-bold text-white mb-4 flex items-center">
                <span className="w-2 h-2 rounded-full bg-purple-500 mr-2 animate-pulse"></span>
                Live News Feed
              </h2>
              <div className="space-y-3 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
                {auditLogs[0].news_sources.slice(0, 20).map((src: any, idx: number) => (
                  <a 
                    key={idx} 
                    href={src.url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="block p-3 rounded-xl border border-neutral-800 bg-neutral-950/50 hover:bg-neutral-800 hover:border-neutral-700 transition-all group"
                  >
                    <p className="text-sm text-neutral-300 group-hover:text-purple-400 transition-colors line-clamp-2">
                      {src.title}
                    </p>
                    <div className="flex justify-between items-center mt-2">
                      <span className="text-[10px] text-neutral-500 font-mono uppercase">
                        {new URL(src.url).hostname.replace('www.', '')}
                      </span>
                      <svg className="w-3 h-3 text-neutral-600 group-hover:text-purple-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </div>
                  </a>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* COLUNA CENTRAL: O Diário de Bordo & Day Trade */}
        <div className="lg:col-span-2 space-y-8">
          {/* MODO SNIPER DAY TRADE (10 MIN) */}
          <SniperDaytradePanel />

          <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6 backdrop-blur-sm">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-white flex items-center">
                <span className="w-2 h-2 rounded-full bg-purple-500 mr-2"></span>
                Sala de Comando (User Overrides)
              </h2>
              {activeDirective && (
                <span className="px-3 py-1 bg-rose-500/20 text-rose-400 text-xs font-bold rounded-full border border-rose-500/30 animate-pulse">
                  Override Ativo
                </span>
              )}
            </div>
            
            {activeDirective ? (
              <div className="p-4 rounded-xl border bg-neutral-900 border-rose-500/50 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1 h-full bg-rose-500"></div>
                <p className="text-sm text-neutral-400 mb-1">A IA está sendo forçada a obedecer a seguinte diretriz:</p>
                <p className="text-lg font-mono text-white mb-4">&quot;{activeDirective}&quot;</p>
                <form action={clearDirective}>
                  <button type="submit" className="px-4 py-2 bg-neutral-800 hover:bg-rose-900/50 text-white text-sm font-bold rounded transition-colors">
                    Desativar Override Agora
                  </button>
                </form>
              </div>
            ) : (
              <form action={submitDirective} className="space-y-4">
                <div>
                  <label htmlFor="directive" className="block text-sm text-neutral-400 mb-2">Instrução Direta para a IA</label>
                  <input 
                    type="text" 
                    id="directive" 
                    name="directive" 
                    placeholder="Ex: Não compre memecoins hoje, foque apenas em Ethereum..."
                    className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-purple-500 transition-colors"
                    required 
                  />
                </div>
                <div className="flex gap-4">
                  <div className="flex-1">
                    <label htmlFor="hours" className="block text-sm text-neutral-400 mb-2">Expira em (Horas)</label>
                    <input 
                      type="number" 
                      id="hours" 
                      name="hours" 
                      defaultValue={24}
                      min={1}
                      max={720}
                      className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-purple-500 transition-colors"
                      required 
                    />
                  </div>
                  <div className="flex items-end">
                    <button type="submit" className="px-8 py-3 bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-lg transition-colors shadow-[0_0_20px_rgba(147,51,234,0.3)]">
                      Injetar Diretriz
                    </button>
                  </div>
                </div>
              </form>
            )}
          </section>

          <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6 backdrop-blur-sm">
            <h2 className="text-xl font-bold text-white mb-6 flex items-center">
              <span className="w-2 h-2 rounded-full bg-neutral-500 mr-2"></span>
              Diário de Bordo (Audit Log)
            </h2>
            <div className="space-y-4 max-h-[500px] overflow-y-auto pr-2 custom-scrollbar">
              {/* CARD EXCLUSIVO DE DAY TRADE SE HOUVER SESSÃO INICIADA OU CONCLUÍDA */}
              {daytradeSession && daytradeSession.started_at && (
                <div className={`p-4 rounded-xl border relative pl-6 transition-all ${
                  daytradeSession.status === 'running'
                    ? 'border-rose-500/50 bg-rose-950/20 shadow-[0_0_20px_rgba(244,63,94,0.15)]'
                    : 'border-neutral-700 bg-neutral-900/80'
                }`}>
                  <div className={`absolute left-2 top-4 w-3 h-3 rounded-full border-[3px] border-neutral-950 z-10 ${
                    daytradeSession.status === 'running' ? 'bg-rose-500 animate-ping' : 'bg-rose-400'
                  }`}></div>

                  <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      <p className="text-xs text-neutral-400 font-mono">
                        {formatDateTime(daytradeSession.started_at)}
                      </p>
                      <span className="px-2.5 py-0.5 bg-rose-500/20 text-rose-300 text-[10px] font-extrabold rounded-full border border-rose-500/40 uppercase tracking-wider">
                        ⚡ SNIPER DAY TRADE (10 MIN)
                      </span>
                      {daytradeSession.status === 'running' && (
                        <span className="px-2 py-0.5 bg-rose-600 text-white text-[10px] font-bold rounded animate-pulse">
                          AO VIVO
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-neutral-400">
                        Capital: <strong className="text-white">{daytradeSession.currency === 'BRL' ? `R$ ${daytradeSession.capital?.toFixed(2)}` : `$ ${daytradeSession.capital?.toFixed(2)} USDT`}</strong>
                      </span>
                      <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded ${
                        (daytradeSession.total_pnl_pct || 0) >= 0 ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/60' : 'bg-rose-950/80 text-rose-400 border border-rose-800/60'
                      }`}>
                        {(daytradeSession.total_pnl_pct || 0) >= 0 ? '+' : ''}{(daytradeSession.total_pnl_pct || 0).toFixed(2)}% PnL
                      </span>
                    </div>
                  </div>

                  <p className="text-sm text-neutral-200 mb-2">
                    {daytradeSession.status === 'running'
                      ? `Sessão ativa operando ${daytradeSession.symbol}. Monitorando Bandas de Bollinger, RSI-7 e VWAP.`
                      : `Sessão concluída em ${daytradeSession.finished_at ? formatDateTime(daytradeSession.finished_at) : '10 min'}. ${daytradeSession.reason || ''}`}
                  </p>

                  {/* TABELA DETALHADA DAS OPERAÇÕES DO DAY TRADE */}
                  {daytradeTrades.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-neutral-800/80">
                      <p className="text-xs font-bold uppercase tracking-wider text-neutral-400 mb-2">
                        Detalhamento das Operações ({daytradeTrades.length})
                      </p>
                      <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                        {daytradeTrades.map((t: any, tidx: number) => {
                          const isBuy = t.action === 'BUY'
                          return (
                            <div key={tidx} className="flex justify-between items-center p-2 rounded-lg bg-neutral-950/70 border border-neutral-800/60 text-xs font-mono">
                              <div className="flex items-center gap-2">
                                <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded ${
                                  isBuy ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                                }`}>
                                  {isBuy ? 'COMPRA' : 'VENDA'}
                                </span>
                                <span className="text-neutral-400">{t.timestamp ? new Date(t.timestamp).toLocaleTimeString('pt-BR') : ''}</span>
                                <span className="text-neutral-300 font-semibold">{t.qty?.toFixed(6)} BTC</span>
                              </div>
                              <div className="flex items-center gap-3">
                                <span className="text-neutral-400">@ {t.price?.toLocaleString('pt-BR', { style: 'currency', currency: t.currency || 'BRL' })}</span>
                                {t.pnl_pct !== undefined && (
                                  <span className={`font-bold ${t.pnl_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                    {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                                  </span>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {auditLogs.length === 0 && !daytradeSession && (
                <p className="text-neutral-500 italic">Nenhum log de execução encontrado.</p>
              )}
              {auditLogs.map((log: any, idx: number) => {
                const entry = log
                const date = new Date(entry.timestamp * 1000).toLocaleString('pt-BR')
                // Distinção explícita: apenas dry_run === true é simulação
                const isSimulation = entry.dry_run === true
                const isLive      = entry.dry_run === false
                
                return (
                  <div key={idx} className={`p-4 rounded-xl border relative pl-6 transition-all ${
                    isSimulation
                      ? 'border-amber-800/50 bg-amber-950/10'
                      : isLive
                        ? 'border-emerald-800/40 bg-emerald-950/10'
                        : 'border-neutral-800/50 bg-neutral-950/50'
                  }`}>
                    {/* Linha da timeline */}
                    <div className="absolute left-[11px] top-8 bottom-[-16px] w-[2px] bg-neutral-800 z-0"></div>
                    <div className={`absolute left-2 top-4 w-3 h-3 rounded-full border-[3px] border-neutral-950 z-10 ${
                      isSimulation ? 'bg-amber-400' : isLive ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]' : 'bg-neutral-600'
                    }`}></div>
                    
                    <div className="flex items-center gap-2 mb-2">
                      <p className="text-xs text-neutral-500 font-mono">{date}</p>
                      {isSimulation && (
                        <span className="px-2 py-0.5 bg-amber-500/20 text-amber-400 text-[10px] font-bold rounded-full border border-amber-500/40 tracking-widest">
                          🧪 SIMULAÇÃO
                        </span>
                      )}
                      {isLive && (
                        <span className="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 text-[10px] font-bold rounded-full border border-emerald-500/40 tracking-widest animate-pulse">
                          ⚡ REAL
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-neutral-300 mb-3">{entry.news_summary}</p>
                    

                    {entry.news_sources && entry.news_sources.length > 0 && (
                      <details className="mb-3 group bg-neutral-900/50 border border-neutral-800 rounded text-xs text-neutral-400">
                        <summary className="p-2 cursor-pointer font-bold text-neutral-300 hover:text-white transition-colors focus:outline-none">
                          Fontes Consultadas ({entry.news_sources.length})
                        </summary>
                        <div className="p-2 pt-0 border-t border-neutral-800/50 mt-1">
                          <ul className="list-disc pl-4 space-y-2 mt-2">
                            {entry.news_sources.map((src: any, sIdx: number) => (
                              <li key={sIdx}>
                                <a href={src.url} target="_blank" rel="noopener noreferrer" className="hover:text-purple-400 underline decoration-neutral-600 underline-offset-2">
                                  {src.title}
                                </a>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </details>
                    )}

                    {entry.learned_lessons && (
                      <div className="mb-3 p-3 bg-amber-950/20 border border-amber-500/20 rounded-lg text-sm text-amber-300/90 shadow-[inset_0_0_10px_rgba(245,158,11,0.05)]">
                        <strong className="text-amber-400 mb-1 flex items-center gap-2">
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                          </svg>
                          Lição da IA:
                        </strong> 
                        {entry.learned_lessons}
                      </div>
                    )}
                    
                    {entry.directives_applied && (
                      <div className="mb-3 p-2 bg-purple-900/20 border border-purple-500/20 rounded text-xs text-purple-300 inline-block">
                        <strong>Obedeceu:</strong> {entry.directives_applied}
                      </div>
                    )}
                    
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
                            <div key={i} className={`p-3.5 rounded-xl border relative overflow-hidden transition-all ${
                              !isBuy 
                                ? 'bg-rose-950/20 border-rose-900/40 hover:border-rose-800/60' 
                                : 'bg-emerald-950/20 border-emerald-900/40 hover:border-emerald-800/60'
                            }`}>
                              {/* Header: Badge de Ação e Fluxo da Operação (ex: BRL ➔ BTC) */}
                              <div className="flex justify-between items-center mb-3">
                                <div className="flex items-center gap-2">
                                  <span className={`px-2 py-0.5 text-[10px] font-bold rounded uppercase tracking-wider ${
                                    !isBuy 
                                      ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' 
                                      : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                  }`}>
                                    {isBuy ? 'COMPRA' : 'VENDA'}
                                  </span>

                                  {/* Fluxo de Conversão em destaque: O que foi usado -> O que foi comprado */}
                                  <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-md bg-neutral-900/90 border border-neutral-800 font-mono text-xs font-bold shadow-sm">
                                    <span className={isBuy ? 'text-amber-400' : 'text-purple-400'}>{fromAsset}</span>
                                    <span className="text-neutral-500 text-xs">➔</span>
                                    <span className={isBuy ? 'text-emerald-400' : 'text-amber-400'}>{toAsset}</span>
                                  </div>
                                </div>

                                <span className="font-mono text-xs text-neutral-400 font-semibold">{t.symbol}</span>
                              </div>

                              {/* Detalhamento: O que foi usado (pago) vs O que foi adquirido */}
                              <div className="grid grid-cols-2 gap-2 text-xs mb-2.5 p-2.5 rounded-lg bg-neutral-950/70 border border-neutral-800/60">
                                <div>
                                  <span className="text-[10px] text-neutral-500 uppercase block font-semibold mb-0.5">
                                    {isBuy ? 'Usado (Pago)' : 'Entregue (Venda)'}
                                  </span>
                                  <span className="font-mono font-bold text-white text-xs block">
                                    {isBuy 
                                      ? (t.fiat_amount ? formatCurrency(t.fiat_amount) : '-') 
                                      : (t.crypto_qty ? `${t.crypto_qty.toFixed(6)} ${baseAsset}` : '-')}
                                  </span>
                                  <span className="text-[10px] text-neutral-500 font-mono">
                                    {isBuy ? quoteAsset : baseAsset}
                                  </span>
                                </div>
                                <div>
                                  <span className="text-[10px] text-neutral-500 uppercase block font-semibold mb-0.5">
                                    {isBuy ? 'Comprado (Recebido)' : 'Recebido (Fiat)'}
                                  </span>
                                  <span className="font-mono font-bold text-emerald-400 text-xs block">
                                    {isBuy 
                                      ? (t.crypto_qty ? `${t.crypto_qty.toFixed(6)} ${baseAsset}` : '-') 
                                      : (t.fiat_amount ? formatCurrency(t.fiat_amount) : '-')}
                                  </span>
                                  <span className="text-[10px] text-neutral-500 font-mono">
                                    {isBuy ? baseAsset : quoteAsset}
                                  </span>
                                </div>
                              </div>

                              {/* Rodapé: Preço de Referência */}
                              <div className="flex justify-between items-center text-[11px] text-neutral-400 pt-1.5 border-t border-neutral-800/40">
                                <span className="text-neutral-500">Preço de Referência:</span>
                                <span className="font-mono text-neutral-300 font-medium">
                                  {t.price ? `${formatCurrency(t.price)} / ${baseAsset}` : '-'}
                                </span>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <span className="px-2 py-1 text-xs bg-neutral-800 text-neutral-400 rounded inline-block mt-3">
                        Nenhum trade executado.
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </section>

        </div>
      </div>
    </div>
  )
}

import { Redis } from '@upstash/redis'
import { revalidatePath } from 'next/cache'
import PortfolioPieChart from './components/PortfolioPieChart'

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
    activeDirective
  ] = await Promise.all([
    redis.get('portfolio:open_positions'),
    redis.get('dashboard:current_sentiment'),
    redis.lrange('dashboard:pnl_history', 0, 10),
    redis.lrange('dashboard:audit_logs', 0, 10),
    redis.get('ai:user_directives'),
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
  
  // Para arrays vindos do Upstash
  const auditLogs = (auditLogsRaw || []).map((log: any) => safeParse(log, {}))
  const currentPnl = pnlHistoryRaw && pnlHistoryRaw.length > 0 ? safeParse(pnlHistoryRaw[0], {}).value || 0 : 0

  // Formatações
  const formatCurrency = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val)
  
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
          <p className="text-neutral-400">Terminal Quantitativo & Diário de Bordo</p>
        </div>
        <div className="text-right">
          <p className="text-sm text-neutral-500 mb-1">Total Alocado (Histórico)</p>
          <p className="text-3xl font-bold text-white">{formatCurrency(currentPnl)}</p>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* COLUNA ESQUERDA: Posições e Sentimento */}
        <div className="space-y-8">
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
              {Object.entries(openPositions).map(([symbol, data]: [string, any]) => (
                <div key={symbol} className="flex justify-between items-center p-3 rounded-lg bg-neutral-900 border border-neutral-800/50">
                  <div>
                    <p className="font-bold text-white">{symbol}</p>
                    <p className="text-xs text-neutral-500">{data.total_coins.toFixed(6)} moedas</p>
                  </div>
                  <div className="text-right">
                    <p className="font-mono text-sm text-emerald-400">{formatCurrency(data.total_invested)}</p>
                    <p className="text-xs text-neutral-500">PM: {formatCurrency(data.avg_price)}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* COLUNA CENTRAL: O Diário de Bordo */}
        <div className="lg:col-span-2 space-y-8">
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
              {auditLogs.length === 0 && (
                <p className="text-neutral-500 italic">Nenhum log de execução encontrado.</p>
              )}
              {auditLogs.map((log: any, idx: number) => {
                // O objeto já foi parseado pelo safeParse no início do arquivo
                const entry = log
                const date = new Date(entry.timestamp * 1000).toLocaleString('pt-BR')
                
                return (
                  <div key={idx} className="p-4 rounded-xl border border-neutral-800/50 bg-neutral-950/50 relative pl-6">
                    {/* Linha da timeline */}
                    <div className="absolute left-[11px] top-8 bottom-[-16px] w-[2px] bg-neutral-800 z-0"></div>
                    <div className="absolute left-2 top-4 w-3 h-3 rounded-full bg-neutral-600 border-[3px] border-neutral-950 z-10"></div>
                    
                    <p className="text-xs text-neutral-500 mb-2 font-mono">{date}</p>
                    <p className="text-sm text-neutral-300 mb-3">{entry.news_summary}</p>
                    
                    {entry.directives_applied && (
                      <div className="mb-3 p-2 bg-purple-900/20 border border-purple-500/20 rounded text-xs text-purple-300 inline-block">
                        <strong>Obedeceu:</strong> {entry.directives_applied}
                      </div>
                    )}
                    
                    {entry.trades && entry.trades.length > 0 ? (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                        {entry.trades.map((t: any, i: number) => (
                          <div key={i} className={`p-3 rounded-lg border ${
                            t.action === 'SELL' ? 'bg-rose-950/30 border-rose-900/50' : 'bg-emerald-950/30 border-emerald-900/50'
                          }`}>
                            <div className="flex justify-between items-center mb-2">
                              <span className={`px-2 py-1 text-[10px] font-bold rounded uppercase tracking-wider ${
                                t.action === 'SELL' ? 'bg-rose-500/20 text-rose-400' : 'bg-emerald-500/20 text-emerald-400'
                              }`}>
                                {t.action || 'BUY'}
                              </span>
                              <span className="font-bold text-white text-sm">{t.symbol}</span>
                            </div>
                            <div className="flex justify-between text-xs text-neutral-400">
                              <span>Qtd: <strong className="text-neutral-200">{t.crypto_qty ? t.crypto_qty.toFixed(6) : '-'}</strong></span>
                              <span>Ref: <strong className="text-neutral-200">{t.price ? formatCurrency(t.price) : '-'}</strong></span>
                            </div>
                            <div className="mt-2 pt-2 border-t border-neutral-800/50 flex justify-between items-center">
                              <span className="text-[10px] text-neutral-500 uppercase">Volume Fiat</span>
                              <span className="font-mono text-sm font-bold text-white">
                                {t.fiat_amount ? formatCurrency(t.fiat_amount) : '-'}
                              </span>
                            </div>
                          </div>
                        ))}
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

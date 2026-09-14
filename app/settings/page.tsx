import { Redis } from '@upstash/redis'
import { revalidatePath } from 'next/cache'
import Link from 'next/link'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '',
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '',
})

const DEFAULTS = {
  dry_run: true,
  dca_amount_brl: 50,
  min_order_brl: 8,
  max_order_brl: 200,
  max_memecoin_pct: 20,
  min_assets: 5,
  min_stop_pct: 5,
  max_stop_pct: 25,
  auto_deploy_deposits: true,
  preferred_reserve: 'USDT',
}

export default async function SettingsPage() {
  // Carrega config atual
  const raw = await redis.get<any>('config:bot_settings')
  let config = { ...DEFAULTS }
  if (raw) {
    try {
      const parsed = typeof raw === 'string' ? JSON.parse(decodeURIComponent(raw)) : raw
      config = { ...DEFAULTS, ...parsed }
    } catch { /* usa defaults */ }
  }

  async function saveSettings(formData: FormData) {
    'use server'
    const newConfig = {
      dry_run: formData.get('dry_run') === 'true',
      dca_amount_brl: parseFloat(formData.get('dca_amount_brl') as string),
      min_order_brl: parseFloat(formData.get('min_order_brl') as string),
      max_order_brl: parseFloat(formData.get('max_order_brl') as string),
      max_memecoin_pct: parseInt(formData.get('max_memecoin_pct') as string),
      min_assets: parseInt(formData.get('min_assets') as string),
      min_stop_pct: parseInt(formData.get('min_stop_pct') as string),
      max_stop_pct: parseInt(formData.get('max_stop_pct') as string),
      auto_deploy_deposits: formData.get('auto_deploy_deposits') === 'true',
      preferred_reserve: (formData.get('preferred_reserve') as string) || 'USDT',
    }
    await redis.set('config:bot_settings', JSON.stringify(newConfig))
    revalidatePath('/settings')
    revalidatePath('/')
  }

  const isDryRun = config.dry_run

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-200 font-sans p-8">
      <header className="mb-10 border-b border-neutral-800 pb-6 flex justify-between items-end">
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight text-white mb-2">
            Ivanvest<span className="text-emerald-500">AI</span>
            <span className="ml-3 text-xl font-semibold text-neutral-400">/ Configurações</span>
          </h1>
          <p className="text-neutral-400">Parâmetros operacionais do Fundo Quantitativo</p>
        </div>
        <Link href="/" className="px-4 py-2 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-sm font-semibold text-white transition-colors">
          ← Dashboard
        </Link>
      </header>

      <form action={saveSettings} className="max-w-2xl mx-auto space-y-6">

        {/* MODO DE OPERAÇÃO */}
        <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6">
          <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${isDryRun ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'}`}></span>
            Modo de Operação
          </h2>

          <div className={`p-4 rounded-xl border mb-4 ${isDryRun ? 'bg-amber-950/20 border-amber-500/30' : 'bg-emerald-950/20 border-emerald-500/30'}`}>
            <p className={`text-sm font-semibold mb-1 ${isDryRun ? 'text-amber-400' : 'text-emerald-400'}`}>
              {isDryRun ? '🧪 Modo Simulação (Dry Run) Ativo' : '⚡ Modo Real (Live Trading) Ativo'}
            </p>
            <p className="text-xs text-neutral-400">
              {isDryRun
                ? 'O robô analisa o mercado e registra as decisões, mas NÃO executa ordens reais na Binance.'
                : 'ATENÇÃO: O robô está operando com dinheiro REAL. As ordens serão executadas na Binance.'}
            </p>
          </div>

          <div className="flex gap-3">
            <label className="flex-1">
              <input type="radio" name="dry_run" value="true" defaultChecked={isDryRun} className="sr-only peer" />
              <div className="peer-checked:border-amber-500 peer-checked:bg-amber-950/30 peer-checked:text-amber-400 border border-neutral-700 rounded-xl p-4 cursor-pointer text-center transition-all hover:border-neutral-600 text-neutral-400">
                <div className="text-2xl mb-1">🧪</div>
                <div className="font-bold text-sm">Simulação</div>
                <div className="text-xs opacity-70 mt-1">Sem risco</div>
              </div>
            </label>
            <label className="flex-1">
              <input type="radio" name="dry_run" value="false" defaultChecked={!isDryRun} className="sr-only peer" />
              <div className="peer-checked:border-emerald-500 peer-checked:bg-emerald-950/30 peer-checked:text-emerald-400 border border-neutral-700 rounded-xl p-4 cursor-pointer text-center transition-all hover:border-neutral-600 text-neutral-400">
                <div className="text-2xl mb-1">⚡</div>
                <div className="font-bold text-sm">Live Trading</div>
                <div className="text-xs opacity-70 mt-1">Dinheiro real</div>
              </div>
            </label>
          </div>
        </section>

        {/* VALORES DE OPERAÇÃO */}
        <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6">
          <h2 className="text-lg font-bold text-white mb-1">💰 Valores de Operação (BRL)</h2>
          <p className="text-xs text-neutral-500 mb-4">Mínimo da Binance: R$ 5,60 (1 USDT). Recomendamos R$ 8 de margem de segurança.</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Valor por Compra (DCA)</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">R$</span>
                <input type="number" name="dca_amount_brl" defaultValue={config.dca_amount_brl} min={8} max={config.max_order_brl} step={1}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg pl-9 pr-4 py-3 text-white focus:outline-none focus:border-emerald-500 transition-colors" />
              </div>
            </div>
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Mínimo por Ordem</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">R$</span>
                <input type="number" name="min_order_brl" defaultValue={config.min_order_brl} min={5.6} max={50} step={0.5}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg pl-9 pr-4 py-3 text-white focus:outline-none focus:border-emerald-500 transition-colors" />
              </div>
            </div>
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Máximo por Ordem</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">R$</span>
                <input type="number" name="max_order_brl" defaultValue={config.max_order_brl} min={10} max={10000} step={10}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg pl-9 pr-4 py-3 text-white focus:outline-none focus:border-emerald-500 transition-colors" />
              </div>
            </div>
          </div>
        </section>

        {/* GESTÃO DE RISCO */}
        <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6">
          <h2 className="text-lg font-bold text-white mb-4">🛡️ Gestão de Risco</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-neutral-400 mb-1">% Máx. em Memecoins</label>
              <div className="relative">
                <input type="number" name="max_memecoin_pct" defaultValue={config.max_memecoin_pct} min={0} max={50} step={5}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 pr-9 text-white focus:outline-none focus:border-purple-500 transition-colors" />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">%</span>
              </div>
            </div>
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Nº Mínimo de Ativos</label>
              <input type="number" name="min_assets" defaultValue={config.min_assets} min={1} max={20} step={1}
                className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-purple-500 transition-colors" />
            </div>
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Stop Loss Mínimo (ATR)</label>
              <div className="relative">
                <input type="number" name="min_stop_pct" defaultValue={config.min_stop_pct} min={1} max={config.max_stop_pct} step={1}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 pr-9 text-white focus:outline-none focus:border-rose-500 transition-colors" />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">%</span>
              </div>
            </div>
            <div>
              <label className="block text-sm text-neutral-400 mb-1">Stop Loss Máximo (ATR)</label>
              <div className="relative">
                <input type="number" name="max_stop_pct" defaultValue={config.max_stop_pct} min={config.min_stop_pct} max={50} step={1}
                  className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 pr-9 text-white focus:outline-none focus:border-rose-500 transition-colors" />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-500 text-sm">%</span>
              </div>
            </div>
          </div>
          <p className="text-xs text-neutral-600 mt-3">O Stop Loss dinâmico (ATR) é calculado por moeda. Estes limites garantem que nunca fique abaixo de {config.min_stop_pct}% nem acima de {config.max_stop_pct}%.</p>
        </section>

        {/* POLÍTICA CAMBIAL & ALOCAÇÃO DE APORTES */}
        <section className="bg-neutral-900/50 rounded-2xl border border-neutral-800 p-6">
          <h2 className="text-lg font-bold text-white mb-2 flex items-center gap-2">
            <span>💵</span> Política Cambial &amp; Aportes
          </h2>
          <p className="text-xs text-neutral-400 mb-4">
            O Real (BRL) perde poder de compra no tempo e é usado estritamente como rampa de entrada.
            O patrimônio deve ser mantido em Criptoativos e Dólar (USDT).
          </p>

          <div className="space-y-4">
            <div className="flex items-center justify-between p-3.5 rounded-xl border border-neutral-800 bg-neutral-950/60">
              <div>
                <p className="text-sm font-semibold text-white">Auto-direcionar Aportes em BRL</p>
                <p className="text-xs text-neutral-400">Ao detectar depósito em Reais, direciona o saldo imediatamente para compra dos ativos (sem deixar BRL ocioso).</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" name="auto_deploy_deposits" value="true" defaultChecked={config.auto_deploy_deposits !== false} className="sr-only peer" />
                <div className="w-11 h-6 bg-neutral-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-neutral-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600"></div>
              </label>
            </div>

            <div>
              <label className="block text-sm text-neutral-400 mb-1">Moeda de Reserva / Hedge Defensivo</label>
              <select name="preferred_reserve" defaultValue={config.preferred_reserve || 'USDT'}
                className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-emerald-500 transition-colors">
                <option value="USDT">Dólar Tether (USDT) - Moeda Forte de Proteção</option>
                <option value="USDC">USD Coin (USDC) - Dólar Auditado</option>
              </select>
              <p className="text-xs text-neutral-600 mt-1.5">Quando o robô realizar lucros ou proteger caixa, manterá as reservas na moeda escolhida em vez de BRL.</p>
            </div>
          </div>
        </section>

        <button type="submit"
          className="w-full py-4 bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-lg rounded-xl transition-colors shadow-[0_0_30px_rgba(16,185,129,0.2)] hover:shadow-[0_0_30px_rgba(16,185,129,0.4)]">
          Salvar Configurações
        </button>
      </form>
    </div>
  )
}

'use client'

import React, { useState, useEffect, useCallback } from 'react'
import DaytradeChart from './DaytradeChart'
import SniperChatFeed, { ChatMessage } from './SniperChatFeed'

interface ActivePosition {
  symbol: string
  entry_price: number
  current_price: number
  crypto_qty: number
  entry_cost: number
  highest_price: number
  trailing_active: boolean
  pnl_pct: number
}

interface SessionOutcome {
  status?: string
  initial_capital?: number
  final_capital?: number
  net_profit_fiat?: number
  net_pnl_fiat?: number
  pnl_pct?: number
  total_pnl_pct?: number
  total_trades?: number
  trades_count?: number
  winning_trades?: number
  losing_trades?: number
  win_rate_pct?: number
  duration_str?: string
  result_status?: 'PROFIT' | 'LOSS'
  source_asset?: string
  currency?: string
  finished_at?: string
}

interface SessionData {
  status: 'idle' | 'pending' | 'running' | 'completed' | 'cancelled'
  capital?: number
  currency?: string
  source_asset?: string
  symbol?: string
  requested_at?: string
  started_at?: string
  finished_at?: string
  current_price?: number
  in_position?: boolean
  entry_price?: number
  position_qty?: number
  position_pnl_pct?: number
  trades_count?: number
  total_pnl_pct?: number
  reason?: string
  in_grace_period?: boolean
  positions?: Record<string, ActivePosition>
  allocated_targets?: Array<{ symbol: string; capital: number; min_cost: number }>
  summary?: SessionOutcome
}

interface Snapshot {
  session_id?: string
  timestamp?: string
  seconds_elapsed?: number
  symbol?: string
  current_price?: number
  in_position?: boolean
  entry_price?: number
  position_qty?: number
  unrealized_pnl_pct?: number
  realized_pnl_pct?: number
  trades_count?: number
  state?: string
  rsi?: number
  vwap?: number
  bb_lower?: number
  bb_upper?: number
}

interface MicroTrade {
  type: string
  action: 'BUY' | 'SELL'
  timestamp: string
  time?: string
  symbol?: string
  pair?: string
  price: number
  buy_price?: number
  sell_price?: number
  qty: number
  crypto_qty?: number
  amount: number
  currency: string
  reason?: string
  exit_reason?: string
  pnl_pct?: number
  net_pnl_fiat?: number
}

interface DailySummary {
  date: string
  total_trades: number
  total_orders: number
  winning_trades: number
  losing_trades: number
  breakeven_trades: number
  win_rate_pct: number
  net_pnl_brl: number
  net_pnl_usdt: number
  total_volume_brl: number
  total_volume_usdt: number
  trades: MicroTrade[]
  closed_trades: MicroTrade[]
}

interface WalletAsset {
  asset: string
  name: string
  balance: number
  approxBrl: number
  approxUsd: number
  minCapitalUsd: number
  canTrade: boolean
}

interface BtcMacroRegime {
  healthy: boolean
  regime: string
  reason: string
}

interface SniperPolicy {
  risk_mode: string
  target_pct: number
  trailing_arm_pct: number
  trailing_buffer_pct: number
  min_stop_pct: number
  max_stop_pct: number
  max_spread_pct: number
  min_rvol: number
  btc_macro_filter: boolean
}

interface SniperAutoConfig {
  enabled: boolean
  interval_minutes: number
  capital: number
  currency: string
  source_asset: string
  last_run_timestamp?: number | null
  nextAutoTriggerSeconds?: number
}

export default function SniperDaytradePanel() {
  const [capital, setCapital] = useState<number>(15)
  const [currency, setCurrency] = useState<'BRL' | 'USDT'>('USDT')
  const [sourceAsset, setSourceAsset] = useState<string>('BTC')
  const [walletAssets, setWalletAssets] = useState<WalletAsset[]>([])
  const [session, setSession] = useState<SessionData | null>(null)
  const [estimatedWaitSeconds, setEstimatedWaitSeconds] = useState<number>(0)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [microtrades, setMicrotrades] = useState<MicroTrade[]>([])
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [daytradeHistory, setDaytradeHistory] = useState<SessionOutcome[]>([])
  const [loading, setLoading] = useState<boolean>(false)
  const [remainingSessionSeconds, setRemainingSessionSeconds] = useState<number>(600)
  const [graceSeconds, setGraceSeconds] = useState<number>(0)
  const [inGracePeriod, setInGracePeriod] = useState<boolean>(false)
  
  // Saldos reais e trava de segurança
  const [balances, setBalances] = useState<{ BRL: number; USDT: number; BTC?: number; ETH?: number }>({
    BRL: 0,
    USDT: 0,
    BTC: 0,
    ETH: 0,
  })
  const [isDryRun, setIsDryRun] = useState<boolean>(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'chat' | 'chart' | 'table'>('chat')

  // Balanço Diário e Modal de Relatório
  const [dailySummary, setDailySummary] = useState<DailySummary | null>(null)
  const [isDailyReportOpen, setIsDailyReportOpen] = useState<boolean>(false)
  const [dailyReportFilter, setDailyReportFilter] = useState<'all' | 'profit' | 'loss'>('all')
  const [dailyReportSearch, setDailyReportSearch] = useState<string>('')

  // Calibragem Adaptativa e Gatekeeper Macro BTC
  const [btcMacroRegime, setBtcMacroRegime] = useState<BtcMacroRegime | null>(null)
  const [sniperPolicy, setSniperPolicy] = useState<SniperPolicy | null>(null)

  // Configurações do Modo Autônomo (Disparo a cada 1 hora)
  const [autoConfig, setAutoConfig] = useState<SniperAutoConfig>({
    enabled: true,
    interval_minutes: 60,
    capital: 15,
    currency: 'USDT',
    source_asset: 'USDT',
    nextAutoTriggerSeconds: 0,
  })
  const [autoToggling, setAutoToggling] = useState<boolean>(false)
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false)

  // Polling dos dados da sessão a cada 3 segundos
  const fetchDaytradeState = useCallback(async () => {
    try {
      const res = await fetch('/api/daytrade', { cache: 'no-store' })
      if (!res.ok) return
      const data = await res.json()
      setSession(data.session)
      setSnapshots(data.snapshots || [])
      setMicrotrades(data.microtrades || [])

      if (data.dailySummary) {
        setDailySummary(data.dailySummary)
      }
      if (data.chatMessages) {
        setChatMessages(data.chatMessages)
      }
      if (data.daytradeHistory) {
        setDaytradeHistory(data.daytradeHistory)
      }
      if (data.balances) {
        setBalances(data.balances)
      }
      if (data.walletAssets && data.walletAssets.length > 0) {
        setWalletAssets(data.walletAssets)
      }
      if (data.botConfig) {
        setIsDryRun(data.botConfig.dry_run ?? false)
      }
      if (data.btcMacroRegime) {
        setBtcMacroRegime(data.btcMacroRegime)
      }
      if (data.sniperPolicy) {
        setSniperPolicy(data.sniperPolicy)
      }
      if (data.autoConfig) {
        setAutoConfig(data.autoConfig)
      }

      if (data.session?.status === 'pending') {
        setEstimatedWaitSeconds(data.estimatedWaitSeconds || 0)
      }
    } catch (e) {
      console.error('Erro ao buscar status do Day Trade:', e)
    }
  }, [])

  useEffect(() => {
    fetchDaytradeState()
    const interval = setInterval(fetchDaytradeState, 3000)
    return () => clearInterval(interval)
  }, [fetchDaytradeState])

  // Listener da tecla Esc para fechar modal do relatório
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsDailyReportOpen(false)
      }
    }
    if (isDailyReportOpen) {
      window.addEventListener('keydown', handleKeyDown)
    }
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isDailyReportOpen])

  // Ajusta a moeda padrão de acordo com o ativo selecionado
  const handleSelectSourceAsset = (asset: WalletAsset) => {
    setSourceAsset(asset.asset)
    if (asset.asset === 'BRL') {
      setCurrency('BRL')
      setCapital(Math.min(50, Math.max(10, Math.floor(asset.approxBrl))))
    } else {
      setCurrency('USDT')
      const maxUsd = Math.floor(asset.approxUsd)
      setCapital(Math.min(10, Math.max(1, maxUsd > 1 ? maxUsd : 1)))
    }
  }

  // Função auxiliar para converter qualquer formato de timestamp
  const parseStartedAtMs = (raw: any): number => {
    if (!raw) return 0
    if (typeof raw === 'number') {
      return raw < 1e11 ? raw * 1000 : raw
    }
    if (typeof raw === 'string') {
      const num = Number(raw)
      if (!isNaN(num) && raw.trim() !== '') {
        return num < 1e11 ? num * 1000 : num
      }
      return new Date(raw).getTime()
    }
    return 0
  }

  // Cronômetro regressivo de precisão do Day Trade (ativo APENAS após started_at)
  useEffect(() => {
    if (!session || session.status !== 'running' || !session.started_at) {
      return
    }

    const startedAtMs = parseStartedAtMs(session.started_at)
    if (!startedAtMs || isNaN(startedAtMs)) return

    const updateTimer = () => {
      const nowMs = Date.now()
      const elapsedSeconds = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000))

      if (elapsedSeconds < 600) {
        setRemainingSessionSeconds(600 - elapsedSeconds)
        setInGracePeriod(false)
        setGraceSeconds(0)
      } else {
        // Modo Hold Ilimitado: tempo estendido sem limite enquanto houver posições em andamento
        setRemainingSessionSeconds(0)
        setInGracePeriod(true)
        setGraceSeconds(elapsedSeconds - 600)
      }
    }

    updateTimer()
    const timer = setInterval(updateTimer, 1000)
    return () => clearInterval(timer)
  }, [session])

  // Submissão: Iniciar Modo Sniper com validações de trava
  const handleStart = async () => {
    if (isBlocked) return
    setLoading(true)
    setErrorMessage(null)
    try {
      const res = await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'request',
          capital,
          currency,
          source_asset: sourceAsset,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setErrorMessage(data.error || 'Falha ao solicitar sessão')
      } else {
        await fetchDaytradeState()
        setViewMode('chat') // Mostra o chat da IA imediatamente
      }
    } catch (e: any) {
      setErrorMessage(e?.message || 'Erro ao iniciar Daytrade')
    } finally {
      setLoading(false)
    }
  }

  // Cancelar solicitação pendente
  const handleCancel = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'cancel' }),
      })
      if (res.ok) {
        await fetchDaytradeState()
      }
    } catch (e) {
      console.error('Erro ao cancelar:', e)
    } finally {
      setLoading(false)
    }
  }

  // Alternar Flag do Modo Autônomo a cada 1 hora
  const handleToggleAuto = async (newEnabled: boolean) => {
    setAutoToggling(true)
    try {
      const res = await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'update_auto_config',
          enabled: newEnabled,
          capital: autoConfig.capital || 15,
          currency: 'USDT',
          source_asset: 'USDT',
          interval_minutes: 60,
        }),
      })
      const data = await res.json()
      if (data.autoConfig) {
        setAutoConfig(data.autoConfig)
      }
    } catch (e) {
      console.error('Erro ao alternar modo autônomo:', e)
    } finally {
      setAutoToggling(false)
    }
  }

  // Salvar ajustes das configurações autônomas
  const handleSaveAutoSettings = async (newCapital: number, intervalMinutes = 60) => {
    setAutoToggling(true)
    try {
      const res = await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'update_auto_config',
          enabled: autoConfig.enabled,
          capital: newCapital,
          currency: 'USDT',
          source_asset: 'USDT',
          interval_minutes: intervalMinutes,
        }),
      })
      const data = await res.json()
      if (data.autoConfig) {
        setAutoConfig(data.autoConfig)
        setIsSettingsOpen(false)
      }
    } catch (e) {
      console.error('Erro ao salvar configurações autônomas:', e)
    } finally {
      setAutoToggling(false)
    }
  }

  const formatTimer = (totalSec: number) => {
    const m = Math.floor(Math.max(0, totalSec) / 60)
    const s = Math.max(0, totalSec) % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const formatPrice = (val?: number, curr = 'USDT') => {
    if (!val || isNaN(val)) return curr === 'USDT' ? '$ 0,00' : 'R$ 0,00'
    const decimals = Math.abs(val) < 0.001 ? 8 : Math.abs(val) < 1 ? 4 : 2
    if (curr === 'USDT' || curr === 'USD') {
      return `$ ${val.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: decimals })} USDT`
    }
    return val.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: decimals })
  }

  const isPending = session?.status === 'pending'
  const isRunning = session?.status === 'running'
  const isCompleted = session?.status === 'completed'

  // Identifica o ativo selecionado e seus saldos
  const selectedAssetObj = walletAssets.find((w) => w.asset === sourceAsset) || {
    asset: sourceAsset,
    name: sourceAsset,
    balance: (balances as any)[sourceAsset] || 0,
    approxUsd: sourceAsset === 'USDT' ? balances.USDT : 0,
    approxBrl: sourceAsset === 'BRL' ? balances.BRL : 0,
    minCapitalUsd: 1.0,
    canTrade: true,
  }

  // Cálculos da trava de segurança de saldo e lote mínimo
  const availableBuyingPower = currency === 'BRL' ? selectedAssetObj.approxBrl : selectedAssetObj.approxUsd
  const minRequired = currency === 'BRL' ? 10.0 : 1.0 // Binance spot min $1 para memes (PEPE, DOGE)
  const isBelowMinimum = capital < minRequired
  const exceedsBalance = !isDryRun && capital > availableBuyingPower * 1.01
  const insufficientBalanceForMin = !isDryRun && availableBuyingPower < minRequired
  const isBlocked = isBelowMinimum || exceedsBalance || insufficientBalanceForMin

  // Posições ativas individuais do scanner
  const activePositionsMap = session?.positions || {}
  const activePositionsList = Object.values(activePositionsMap)

  // Resultado da última sessão (quanto ganhou ou perdeu)
  const lastOutcome: SessionOutcome | null = session?.summary || (daytradeHistory.length > 0 ? daytradeHistory[0] : null)

  // Filtro de trades para o Relatório Diário
  const filteredDailyTrades = (dailySummary?.trades || []).filter((t) => {
    if (dailyReportFilter === 'profit') {
      if (t.action !== 'SELL' || (t.net_pnl_fiat ?? 0) <= 0) return false
    }
    if (dailyReportFilter === 'loss') {
      if (t.action !== 'SELL' || (t.net_pnl_fiat ?? 0) >= 0) return false
    }
    if (dailyReportSearch.trim()) {
      const q = dailyReportSearch.trim().toUpperCase()
      const sym = (t.symbol || t.pair || '').toUpperCase()
      const curr = (t.currency || '').toUpperCase()
      const reason = (t.exit_reason || t.reason || '').toUpperCase()
      if (!sym.includes(q) && !curr.includes(q) && !reason.includes(q)) return false
    }
    return true
  })

  return (
    <section className="bg-neutral-900/60 rounded-2xl border border-rose-950/40 p-6 backdrop-blur-md relative overflow-hidden shadow-[0_0_40px_rgba(244,63,94,0.05)]">
      {/* Luz neon de fundo sutil */}
      <div className="absolute -top-24 -right-24 w-60 h-60 bg-rose-600/10 rounded-full blur-3xl pointer-events-none"></div>

      {/* Header do Painel */}
      <div className="flex justify-between items-center mb-5 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400 font-bold text-lg shadow-sm">
            ⚡
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-extrabold text-white tracking-tight">
                Sniper Day Trade{' '}
                <span className="text-rose-500 text-xs px-2 py-0.5 rounded-full bg-rose-950/60 border border-rose-800/60 uppercase tracking-widest font-mono">
                  SCANNER ASSÍNCRONO • MULTI-COIN
                </span>
              </h2>
            </div>
            <p className="text-xs text-neutral-400">
              Varredura algorítmica de altcoins voláteis com justificativas de decisão e aprendizado contínuo da IA
            </p>
          </div>
        </div>

        {/* Status Badge */}
        <div>
          {isRunning ? (
            <span className="px-3 py-1.5 bg-rose-500/20 text-rose-300 text-xs font-bold font-mono rounded-lg border border-rose-500/40 flex items-center gap-2 animate-pulse shadow-[0_0_15px_rgba(244,63,94,0.2)]">
              <span className="w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping"></span>
              SESSÃO EM ANDAMENTO ({activePositionsList.length} ATIVOS)
            </span>
          ) : isPending ? (
            <span className="px-3 py-1.5 bg-amber-500/20 text-amber-300 text-xs font-bold font-mono rounded-lg border border-amber-500/40 flex items-center gap-2 shadow-[0_0_15px_rgba(245,158,11,0.15)]">
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
              AGUARDANDO PRÓXIMO CICLO
            </span>
          ) : isCompleted ? (
            <span className="px-3 py-1.5 bg-neutral-800 text-neutral-300 text-xs font-bold font-mono rounded-lg border border-neutral-700">
              SESSÃO ANTERIOR CONCLUÍDA
            </span>
          ) : (
            <span className="px-3 py-1 bg-neutral-900 text-neutral-400 text-xs font-mono rounded border border-neutral-800">
              DESATIVADO
            </span>
          )}
        </div>
      </div>

      {/* CARD DE CONTROLE: SNIPER AUTÔNOMO A CADA 1 HORA (FLAG DE ATIVAÇÃO / CONFIGURAÇÕES) */}
      <div className={`p-4 rounded-xl border transition-all mb-4 ${
        autoConfig.enabled
          ? 'bg-gradient-to-r from-emerald-950/40 via-neutral-900/90 to-neutral-950 border-emerald-500/40 shadow-[0_0_25px_rgba(16,185,129,0.08)]'
          : 'bg-gradient-to-r from-neutral-950 via-neutral-900 to-neutral-950 border-neutral-800'
      }`}>
        <div className="flex justify-between items-center flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center font-bold text-lg border ${
              autoConfig.enabled 
                ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40 shadow-sm'
                : 'bg-neutral-800 text-neutral-400 border-neutral-700'
            }`}>
              {autoConfig.enabled ? '⏱️' : '⏸️'}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-extrabold text-white tracking-wide">
                  Sniper Autônomo a cada 1 Hora
                </span>
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-bold uppercase border ${
                  autoConfig.enabled
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                    : 'bg-neutral-800 text-neutral-400 border-neutral-700'
                }`}>
                  {autoConfig.enabled ? 'Sempre Ativo' : 'Desativado pelo Usuário'}
                </span>
              </div>
              <p className="text-xs text-neutral-400 mt-0.5">
                {autoConfig.enabled ? (
                  <>
                    Disparo programado a cada <strong>1 hora (60 min)</strong> com <strong>${autoConfig.capital || 15} {autoConfig.currency || 'USDT'}</strong>.{' '}
                    <span className="text-emerald-400 font-mono font-bold">
                      {autoConfig.nextAutoTriggerSeconds && autoConfig.nextAutoTriggerSeconds > 0
                        ? `Próximo disparo em ~${Math.ceil(autoConfig.nextAutoTriggerSeconds / 60)} min`
                        : 'Pronto para disparar no próximo ciclo'}
                    </span>
                  </>
                ) : (
                  <span className="text-neutral-500">
                    O robô está pausado. Para reativar a rotina de 1 hora, clique no botão ao lado.
                  </span>
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setIsSettingsOpen(!isSettingsOpen)}
              className="px-3 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800/90 hover:bg-neutral-700 text-neutral-300 text-xs font-semibold flex items-center gap-1.5 transition-colors"
            >
              <span>⚙️</span>
              <span>{isSettingsOpen ? 'Fechar' : 'Configurações'}</span>
            </button>

            {/* Toggle Switch */}
            <button
              type="button"
              disabled={autoToggling}
              onClick={() => handleToggleAuto(!autoConfig.enabled)}
              className={`relative inline-flex h-7 w-14 items-center rounded-full transition-colors focus:outline-none cursor-pointer ${
                autoConfig.enabled ? 'bg-emerald-600' : 'bg-neutral-700'
              }`}
              title={autoConfig.enabled ? "Clique para desabilitar o modo autônomo" : "Clique para reativar o modo autônomo a cada 1 hora"}
            >
              <span
                className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                  autoConfig.enabled ? 'translate-x-8' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        {/* Painel de Configurações Aberto na mesma tela */}
        {isSettingsOpen && (
          <div className="mt-4 pt-3 border-t border-neutral-800/80 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div className="p-2.5 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <label className="text-neutral-400 font-bold block mb-1">Capital por Disparo Horário:</label>
              <div className="flex items-center gap-2">
                <span className="text-neutral-400 font-mono text-sm">$</span>
                <input
                  type="number"
                  min="5"
                  max="1000"
                  step="5"
                  value={autoConfig.capital || 15}
                  onChange={(e) => {
                    const newCap = parseFloat(e.target.value) || 15
                    setAutoConfig(prev => ({ ...prev, capital: newCap }))
                  }}
                  className="px-2.5 py-1 bg-neutral-900 border border-neutral-700 rounded-md text-white font-mono font-bold w-20 focus:outline-none focus:border-emerald-500"
                />
                <span className="text-neutral-400 font-mono text-xs">USDT</span>
                <button
                  type="button"
                  onClick={() => handleSaveAutoSettings(autoConfig.capital, autoConfig.interval_minutes)}
                  className="ml-auto px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded-md text-xs transition-colors"
                >
                  Salvar
                </button>
              </div>
            </div>

            <div className="p-2.5 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <label className="text-neutral-400 font-bold block mb-1">Frequência Automática:</label>
              <span className="text-neutral-200 font-mono font-bold block py-1">
                A cada 60 minutos (1 Hora)
              </span>
            </div>

            <div className="p-2.5 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <label className="text-neutral-400 font-bold block mb-1">Mercado & Ativo Base:</label>
              <span className="text-emerald-400 font-mono font-bold block py-1">
                Pares USDT (Alocação 100% no Ativo #1)
              </span>
            </div>
          </div>
        )}
      </div>

      {/* BARRA DE POLÍTICA ADAPTATIVA DA IA & GATEKEEPER MACRO BTC */}
      <div className="flex flex-wrap items-center gap-2 mb-5 p-2.5 rounded-xl bg-neutral-950/80 border border-neutral-800/80 text-xs">
        {/* BTC Macro Gatekeeper */}
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-neutral-900 border border-neutral-800">
          <span className="text-neutral-400 font-bold uppercase text-[10px] tracking-wider">Gatekeeper BTC:</span>
          {btcMacroRegime?.regime === 'ALTA' ? (
            <span className="text-emerald-400 font-extrabold flex items-center gap-1" title={btcMacroRegime.reason}>
              <span className="w-2 h-2 rounded-full bg-emerald-400"></span> ALTA (15m Favorável)
            </span>
          ) : btcMacroRegime?.regime === 'QUEDA' ? (
            <span className="text-rose-400 font-extrabold flex items-center gap-1" title={btcMacroRegime.reason}>
              <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse"></span> QUEDA (Entradas Travadas)
            </span>
          ) : (
            <span className="text-amber-300 font-extrabold flex items-center gap-1" title={btcMacroRegime?.reason || 'Consolidação'}>
              <span className="w-2 h-2 rounded-full bg-amber-400"></span> CONSOLIDAÇÃO (Neutro)
            </span>
          )}
        </div>

        {/* Parâmetros Calibrados */}
        <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-neutral-900 border border-neutral-800 font-mono text-[11px] text-neutral-300 flex-wrap">
          <span className="text-neutral-400 font-bold uppercase text-[10px]">Meta:</span>
          <span className="text-emerald-400 font-bold">+{sniperPolicy?.target_pct ? sniperPolicy.target_pct.toFixed(2) : '2.00'}%</span>
          <span className="text-neutral-600">|</span>
          <span className="text-neutral-400 font-bold uppercase text-[10px]">Stop ATR:</span>
          <span className="text-rose-400 font-bold">-{sniperPolicy?.min_stop_pct ? sniperPolicy.min_stop_pct.toFixed(2) : '1.20'}% a -{sniperPolicy?.max_stop_pct ? sniperPolicy.max_stop_pct.toFixed(2) : '2.00'}%</span>
          <span className="text-neutral-600">|</span>
          <span className="text-neutral-400 font-bold uppercase text-[10px]">Alocação:</span>
          <span className="text-cyan-400 font-bold">100% no Ativo #1</span>
        </div>

        <div className="ml-auto text-[10px] text-neutral-500 font-mono hidden sm:block">
          Spread Máx: &lt; {sniperPolicy?.max_spread_pct || 0.08}% • Foco: USDT
        </div>
      </div>

      {/* CARD DE BALANÇO DO DIA (PERDAS E GANHOS) - CLICÁVEL PARA EXPANDIR RELATÓRIO */}
      {dailySummary && (
        <div
          onClick={() => setIsDailyReportOpen(true)}
          className="p-4 sm:p-5 rounded-xl border border-neutral-800/90 bg-gradient-to-br from-neutral-900/95 via-neutral-950 to-neutral-900/70 hover:border-rose-500/60 hover:shadow-[0_0_30px_rgba(244,63,94,0.15)] cursor-pointer transition-all mb-5 group relative overflow-hidden"
          title="Clique para ver o relatório completo de operações do dia"
        >
          {/* Brilho decorativo no hover */}
          <div className="absolute top-0 right-0 w-64 h-64 bg-rose-500/5 rounded-full blur-3xl group-hover:bg-rose-500/10 transition-all pointer-events-none" />

          <div className="flex justify-between items-center flex-wrap gap-3 mb-3 relative z-10">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-neutral-800/90 border border-neutral-700/60 flex items-center justify-center text-base shadow-inner group-hover:scale-105 transition-transform">
                📊
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-extrabold font-mono uppercase tracking-wider text-white group-hover:text-rose-300 transition-colors">
                    Balanço Diário Sniper
                  </h3>
                  <span className="px-2 py-0.5 rounded-full bg-neutral-800 text-[10px] font-mono text-neutral-300 border border-neutral-700">
                    {dailySummary.date}
                  </span>
                </div>
                <p className="text-[11px] text-neutral-400">
                  Consolidado de ganhos e perdas das operações de hoje • Clique para expandir
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-bold text-rose-400 group-hover:text-rose-300 flex items-center gap-1.5 bg-rose-950/40 px-3 py-1.5 rounded-lg border border-rose-900/60 transition-all shadow-sm">
                <span>Ver Relatório Completo</span>
                <span className="group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">↗</span>
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 relative z-10">
            {/* PnL Líquido BRL */}
            <div className="p-3 rounded-lg bg-neutral-950/70 border border-neutral-800/80">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Resultado em BRL
              </span>
              <div className="flex items-baseline gap-1.5">
                <span
                  className={`text-lg sm:text-xl font-extrabold font-mono ${
                    dailySummary.net_pnl_brl > 0
                      ? 'text-emerald-400'
                      : dailySummary.net_pnl_brl < 0
                      ? 'text-rose-400'
                      : 'text-neutral-300'
                  }`}
                >
                  {dailySummary.net_pnl_brl > 0 ? '+' : ''}
                  {dailySummary.net_pnl_brl.toLocaleString('pt-BR', {
                    style: 'currency',
                    currency: 'BRL',
                  })}
                </span>
              </div>
              <span className="text-[10px] text-neutral-500 font-mono block mt-0.5 truncate">
                Vol: {dailySummary.total_volume_brl.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
              </span>
            </div>

            {/* PnL Líquido USDT */}
            <div className="p-3 rounded-lg bg-neutral-950/70 border border-neutral-800/80">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Resultado em USDT
              </span>
              <div className="flex items-baseline gap-1.5">
                <span
                  className={`text-lg sm:text-xl font-extrabold font-mono ${
                    dailySummary.net_pnl_usdt > 0
                      ? 'text-emerald-400'
                      : dailySummary.net_pnl_usdt < 0
                      ? 'text-rose-400'
                      : 'text-neutral-300'
                  }`}
                >
                  {dailySummary.net_pnl_usdt > 0 ? '+' : ''}
                  ${dailySummary.net_pnl_usdt.toFixed(2)} USDT
                </span>
              </div>
              <span className="text-[10px] text-neutral-500 font-mono block mt-0.5 truncate">
                Vol: ${dailySummary.total_volume_usdt.toFixed(2)}
              </span>
            </div>

            {/* Taxa de Acerto (Win Rate) */}
            <div className="p-3 rounded-lg bg-neutral-950/70 border border-neutral-800/80">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Assertividade (Win Rate)
              </span>
              <div className="flex items-baseline gap-1.5">
                <span
                  className={`text-lg sm:text-xl font-extrabold font-mono ${
                    dailySummary.win_rate_pct >= 50
                      ? 'text-emerald-400'
                      : dailySummary.win_rate_pct > 0
                      ? 'text-amber-400'
                      : 'text-neutral-400'
                  }`}
                >
                  {dailySummary.win_rate_pct.toFixed(1)}%
                </span>
              </div>
              {/* Barra de progresso visual */}
              <div className="w-full bg-neutral-800 h-1.5 rounded-full overflow-hidden mt-1.5">
                <div
                  className={`h-full transition-all duration-500 ${
                    dailySummary.win_rate_pct >= 50
                      ? 'bg-emerald-500'
                      : dailySummary.win_rate_pct > 0
                      ? 'bg-amber-500'
                      : 'bg-neutral-600'
                  }`}
                  style={{ width: `${Math.min(100, Math.max(0, dailySummary.win_rate_pct))}%` }}
                />
              </div>
            </div>

            {/* Total de Operações */}
            <div className="p-3 rounded-lg bg-neutral-950/70 border border-neutral-800/80">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Operações do Dia
              </span>
              <div className="flex items-baseline gap-1.5">
                <span className="text-lg sm:text-xl font-extrabold font-mono text-white">
                  {dailySummary.total_trades}
                </span>
                <span className="text-[10px] text-neutral-400 font-mono">
                  posições ({dailySummary.total_orders} ordens)
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1 text-[10px] font-mono">
                <span className="text-emerald-400 font-bold">✓ {dailySummary.winning_trades}W</span>
                <span className="text-neutral-500">•</span>
                <span className="text-rose-400 font-bold">✕ {dailySummary.losing_trades}L</span>
                {dailySummary.breakeven_trades > 0 && (
                  <>
                    <span className="text-neutral-500">•</span>
                    <span className="text-amber-400">{dailySummary.breakeven_trades}E</span>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* CARD DE RESULTADO PÓS-TRADE (QUANTO GANHOU OU PERDEU) */}
      {lastOutcome && !isRunning && (
        <div
          className={`p-5 rounded-xl border relative overflow-hidden mb-5 transition-all ${
            (lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0
              ? 'bg-gradient-to-br from-emerald-950/40 via-neutral-950 to-neutral-900/90 border-emerald-500/50 shadow-[0_0_30px_rgba(16,185,129,0.1)]'
              : 'bg-gradient-to-br from-rose-950/40 via-neutral-950 to-neutral-900/90 border-rose-500/50 shadow-[0_0_30px_rgba(244,63,94,0.1)]'
          }`}
        >
          <div className="flex justify-between items-start flex-wrap gap-3 mb-3">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xl">
                  {(lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0 ? '🏆' : '🛡️'}
                </span>
                <h3 className="text-sm font-extrabold font-mono uppercase tracking-wider text-white">
                  {(lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0
                    ? 'Resultado da Sessão: Lucro Realizado!'
                    : 'Resultado da Sessão: Proteção Acionada'}
                </h3>
              </div>
              <p className="text-xs text-neutral-400">
                Sessão finalizada • Capital e retorno recompostos na carteira em{' '}
                <strong className="text-white font-mono">{lastOutcome.source_asset || 'USDT'}</strong>
              </p>
            </div>
            <span
              className={`px-3 py-1 rounded-full text-xs font-mono font-extrabold border ${
                (lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0
                  ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                  : 'bg-rose-500/20 text-rose-300 border-rose-500/40'
              }`}
            >
              {(lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0 ? 'WIN (+)' : 'LOSS (-)'}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                {(lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0 ? 'Ganho Líquido' : 'Perda Líquida'}
              </span>
              <span
                className={`text-xl font-extrabold font-mono ${
                  (lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0
                    ? 'text-emerald-400'
                    : 'text-rose-400'
                }`}
              >
                {(lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0) >= 0 ? '+' : ''}
                {formatPrice(
                  lastOutcome.net_profit_fiat || lastOutcome.net_pnl_fiat || 0,
                  lastOutcome.currency || 'USDT'
                )}
              </span>
              <span className="text-[11px] text-neutral-500 font-mono block mt-0.5">
                ({(lastOutcome.pnl_pct || lastOutcome.total_pnl_pct || 0) >= 0 ? '+' : ''}
                {(lastOutcome.pnl_pct || lastOutcome.total_pnl_pct || 0).toFixed(2)}%)
              </span>
            </div>

            <div className="p-3 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Capital Inicial ➔ Final
              </span>
              <span className="text-sm font-bold font-mono text-white">
                {formatPrice(lastOutcome.initial_capital || 0, lastOutcome.currency || 'USDT')}
              </span>
              <span className="text-[11px] text-neutral-400 font-mono block mt-0.5">
                ➔ Final: {formatPrice(lastOutcome.final_capital || 0, lastOutcome.currency || 'USDT')}
              </span>
            </div>

            <div className="p-3 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Estatísticas de Trades
              </span>
              <span className="text-sm font-bold font-mono text-white">
                {lastOutcome.total_trades || lastOutcome.trades_count || 0} operações
              </span>
              <span className="text-[11px] text-emerald-400 font-mono block mt-0.5">
                Taxa de Acerto: {lastOutcome.win_rate_pct || 0}%
              </span>
            </div>

            <div className="p-3 rounded-lg bg-neutral-950/80 border border-neutral-800">
              <span className="text-[10px] text-neutral-400 uppercase font-bold block mb-0.5">
                Ativo de Liquidez
              </span>
              <span className="text-sm font-bold font-mono text-amber-400">
                {lastOutcome.source_asset || 'USDT'}
              </span>
              <span className="text-[11px] text-neutral-400 font-mono block mt-0.5">
                Saldo na carteira
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ÁREA DE CONTROLE (QUANDO OCIOSO OU COMPLETADO) */}
      {!isRunning && !isPending && (
        <div className="bg-neutral-950/60 border border-neutral-800/80 rounded-xl p-5 mb-5 space-y-5">
          {/* SELETOR UNIVERSAL DE ATIVO DE ORIGEM */}
          <div className="space-y-2">
            <div className="flex justify-between items-center flex-wrap gap-2">
              <label className="text-xs font-bold uppercase tracking-wider text-neutral-300 flex items-center gap-2">
                <span>🏦 1. Escolha o Ativo de Origem na Carteira</span>
              </label>
              <span className="text-[11px] text-neutral-400 font-mono">
                Poder de compra: <strong className="text-white">{currency === 'BRL' ? `R$ ${availableBuyingPower.toFixed(2)}` : `$ ${availableBuyingPower.toFixed(2)} USD`}</strong>
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              {walletAssets.map((wa) => {
                const isSelected = sourceAsset === wa.asset
                return (
                  <button
                    key={wa.asset}
                    type="button"
                    onClick={() => handleSelectSourceAsset(wa)}
                    className={`p-3 rounded-xl border text-left transition-all ${
                      isSelected
                        ? 'bg-rose-950/40 border-rose-500 shadow-[0_0_15px_rgba(244,63,94,0.15)] ring-1 ring-rose-500'
                        : 'bg-neutral-900/60 border-neutral-800 hover:border-neutral-700 hover:bg-neutral-900'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-extrabold text-sm text-white font-mono">{wa.asset}</span>
                      {isSelected && (
                        <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse"></span>
                      )}
                    </div>
                    <div className="text-[11px] text-neutral-400 mt-1 font-mono truncate">
                      {wa.balance < 0.01 ? wa.balance.toFixed(6) : wa.balance.toFixed(4)} {wa.asset}
                    </div>
                    <div className="text-xs font-bold text-emerald-400 mt-0.5 font-mono">
                      ≈ ${wa.approxUsd.toFixed(2)} USD
                    </div>
                  </button>
                )
              })}
            </div>
            <p className="text-[11px] text-neutral-500">
              💡 <em>O robô usará liquidez instantânea de <strong>{sourceAsset}</strong> para caçar e operar as melhores altcoins e devolverá o lucro em <strong>{sourceAsset}</strong> ao final.</em>
            </p>
          </div>

          {/* CONFIGURAÇÃO DO CAPITAL A SER ALOCADO */}
          <div className="pt-3 border-t border-neutral-800/80 flex justify-between items-center flex-wrap gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-neutral-300 block">
                2. Capital Alocado para o Scanner
              </label>

              <div className="flex items-center gap-2">
                <div className="relative">
                  <span className="absolute left-3 top-2.5 text-xs text-neutral-400 font-mono">
                    {currency === 'BRL' ? 'R$' : '$'}
                  </span>
                  <input
                    type="number"
                    value={capital}
                    onChange={(e) => setCapital(parseFloat(e.target.value) || 0)}
                    min={minRequired}
                    step={1}
                    className="pl-9 pr-3 py-2 bg-neutral-900 border border-neutral-700 text-white font-mono font-bold text-base rounded-lg w-32 focus:outline-none focus:border-rose-500"
                  />
                </div>

                {/* Presets Dinâmicos */}
                <div className="flex gap-1.5">
                  {(currency === 'BRL'
                    ? [10, 25, 50, 100]
                    : [
                        15,
                        25,
                        50,
                        100,
                        Math.max(1, Math.floor(availableBuyingPower)),
                      ]
                  )
                    .filter((v, idx, arr) => arr.indexOf(v) === idx && v > 0)
                    .map((val) => (
                      <button
                        key={val}
                        onClick={() => setCapital(val)}
                        type="button"
                        className={`px-2.5 py-1.5 text-xs font-mono font-semibold rounded-md border transition-all ${
                          capital === val
                            ? 'bg-rose-500/20 text-rose-300 border-rose-500/50'
                            : 'bg-neutral-900 text-neutral-400 border-neutral-800 hover:border-neutral-700'
                        }`}
                      >
                        {currency === 'BRL' ? `R$${val}` : `$${val}`}
                      </button>
                    ))}
                </div>

                {/* Seletor de Unidade */}
                <div className="flex rounded-lg border border-neutral-800 bg-neutral-900 p-0.5 ml-2">
                  <button
                    type="button"
                    onClick={() => setCurrency('USDT')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${
                      currency === 'USDT' ? 'bg-neutral-800 text-emerald-400 shadow' : 'text-neutral-500 hover:text-neutral-300'
                    }`}
                  >
                    USDT
                  </button>
                  <button
                    type="button"
                    onClick={() => setCurrency('BRL')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${
                      currency === 'BRL' ? 'bg-neutral-800 text-white shadow' : 'text-neutral-500 hover:text-neutral-300'
                    }`}
                  >
                    BRL
                  </button>
                </div>
              </div>
            </div>

            {/* Botão de Disparo com Trava de Segurança */}
            <div className="flex items-end">
              <button
                onClick={handleStart}
                disabled={loading || isBlocked}
                className={`px-6 py-3 font-extrabold text-sm rounded-xl transition-all flex items-center gap-2.5 ${
                  isBlocked
                    ? 'bg-neutral-800 text-neutral-500 cursor-not-allowed border border-neutral-700'
                    : 'bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white shadow-[0_0_20px_rgba(225,29,72,0.3)] cursor-pointer'
                }`}
              >
                <span>{isBlocked ? '🔒' : '⚡'}</span>
                <span>{isBlocked ? 'OPERAÇÃO TRAVADA' : 'ATIVAR SCANNER SNIPER (10 MIN)'}</span>
              </button>
            </div>
          </div>

          {/* BANNERS DE TRAVA DE SEGURANÇA E FEEDBACK */}
          {insufficientBalanceForMin && (
            <div className="p-3.5 rounded-xl bg-rose-950/30 border border-rose-500/40 text-xs text-rose-300 flex items-start gap-2.5">
              <span className="text-base">🚫</span>
              <div>
                <strong className="text-rose-400 block font-bold mb-0.5">Saldo Insuficiente em {sourceAsset}</strong>
                <span>
                  Você possui aprox. {currency === 'BRL' ? `R$ ${availableBuyingPower.toFixed(2)}` : `$${availableBuyingPower.toFixed(2)} USD`} em {sourceAsset}. O valor mínimo exigido na corretora é de <strong>{currency === 'BRL' ? 'R$ 10,00' : '$1.00 USDT'}</strong>. Selecione outro ativo com saldo ou ative o Modo Simulação.
                </span>
              </div>
            </div>
          )}

          {exceedsBalance && !insufficientBalanceForMin && (
            <div className="p-3.5 rounded-xl bg-amber-950/30 border border-amber-500/40 text-xs text-amber-300 flex items-start gap-2.5">
              <span className="text-base">⚠️</span>
              <div>
                <strong className="text-amber-400 block font-bold mb-0.5">Trava de Segurança: Capital Excede o Saldo do Ativo</strong>
                <span>
                  O valor selecionado ({currency === 'BRL' ? `R$ ${capital.toFixed(2)}` : `$${capital.toFixed(2)}`}) excede o saldo livre de {sourceAsset} ({currency === 'BRL' ? `R$ ${availableBuyingPower.toFixed(2)}` : `$${availableBuyingPower.toFixed(2)} USD`}).
                </span>
              </div>
            </div>
          )}

          {isBelowMinimum && (
            <div className="p-2.5 rounded-lg bg-amber-950/20 border border-amber-500/30 text-xs text-amber-300">
              ⚠️ O valor mínimo para operações é de <strong>{currency === 'BRL' ? 'R$ 10,00' : '$1.00 USDT'}</strong>.
            </div>
          )}

          {isDryRun && (
            <div className="p-2.5 rounded-lg bg-indigo-950/30 border border-indigo-500/30 text-xs text-indigo-300 flex items-center gap-2">
              <span>🧪</span>
              <span><strong>Modo Simulação (Dry Run) Ativo:</strong> As operações serão simuladas com cotações reais da Binance sem debitar saldo da conta.</span>
            </div>
          )}

          {errorMessage && (
            <div className="p-3 rounded-lg bg-rose-950/40 border border-rose-500/50 text-xs text-rose-300">
              ❌ {errorMessage}
            </div>
          )}
        </div>
      )}

      {/* STATUS: PENDENTE / AGUARDANDO CICLO */}
      {isPending && (
        <div className="bg-amber-950/20 border border-amber-500/30 rounded-xl p-5 mb-5 relative overflow-hidden">
          <div className="flex justify-between items-start flex-wrap gap-4">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-amber-400 animate-ping"></span>
                <h3 className="text-base font-bold text-amber-300">
                  Sessão do Scanner Sniper Programada!
                </h3>
              </div>
              <p className="text-sm text-neutral-300">
                Capital Alocado:{' '}
                <strong className="text-white font-mono">
                  {session?.currency === 'BRL' ? `R$ ${session?.capital?.toFixed(2)}` : `$ ${session?.capital?.toFixed(2)} USDT`}
                </strong>{' '}
                utilizando liquidez de <strong className="text-rose-400 font-mono">{session?.source_asset || sourceAsset}</strong>.
              </p>
              <div className="p-3 bg-neutral-900/80 rounded-lg border border-neutral-800 inline-block">
                <p className="text-xs text-neutral-400">
                  Prazo estimado para disparo da varredura e abertura das ordens:
                </p>
                <p className="text-lg font-mono font-bold text-amber-400">
                  ~ {Math.ceil(estimatedWaitSeconds / 60)} min ({estimatedWaitSeconds}s)
                </p>
                <p className="text-[11px] text-neutral-500 mt-0.5">
                  O robô escaneará as altcoins mais voláteis e distribuirá as entradas automaticamente.
                </p>
              </div>
            </div>

            <button
              onClick={handleCancel}
              disabled={loading}
              className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white text-xs font-bold rounded-lg border border-neutral-700 transition-colors"
            >
              Cancelar Solicitação
            </button>
          </div>
        </div>
      )}

      {/* STATUS: RUNNING (CRONÔMETRO DE 10 MIN AO VIVO + MULTI-POSIÇÕES) */}
      {isRunning && (
        <div className="bg-gradient-to-br from-rose-950/40 via-neutral-950/80 to-neutral-900/60 border border-rose-500/50 rounded-xl p-5 mb-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-center">
            {/* Relógio Regressivo Principal / Contador de Hold Ilimitado */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-rose-500/30 text-center shadow-[inset_0_0_20px_rgba(244,63,94,0.1)]">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                {inGracePeriod ? 'Hold Ilimitado' : 'Tempo Restante'}
              </span>
              <span
                className={`text-4xl font-extrabold font-mono tracking-tight ${
                  inGracePeriod ? 'text-amber-400 animate-pulse' : 'text-rose-400'
                }`}
              >
                {inGracePeriod ? `+${formatTimer(graceSeconds)}` : formatTimer(remainingSessionSeconds)}
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                {inGracePeriod ? 'Aguardando Meta ou Stop Loss' : 'Janela inicial: 10:00 min'}
              </span>
            </div>

            {/* Ativo Fonte e Liquidez Flash */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Origem & Modo
              </span>
              <div className="flex items-baseline gap-2">
                <span className="text-lg font-bold font-mono text-white">
                  {session?.source_asset || 'BTC'}
                </span>
                <span className="text-xs text-neutral-500 font-mono">
                  → {currency}
                </span>
              </div>
              <span className="text-[10px] text-neutral-500 block mt-1">
                {isDryRun ? 'Simulação' : 'Mercado Real'}
              </span>
            </div>

            {/* PnL Geral Não-Realizado */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Retorno Não Realizado
              </span>
              <div className="flex items-baseline gap-2">
                <span
                  className={`text-2xl font-black font-mono tracking-tight ${
                    (session?.total_pnl_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {(session?.total_pnl_pct || 0) >= 0 ? '+' : ''}
                  {(session?.total_pnl_pct || 0).toFixed(2)}%
                </span>
              </div>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Capital Alocado: {capital} {currency}
              </span>
            </div>

            {/* Posições Ativas Scanner */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Posições Abertas
              </span>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-black font-mono text-white">
                  {activePositionsList.length}
                </span>
                <span className="text-xs text-neutral-500 font-mono">
                  ativos simultâneos
                </span>
              </div>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Gestão independente tick-a-tick
              </span>
            </div>
          </div>

          {/* Cards Individuais de Cada Posição Ativa */}
          {activePositionsList.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-neutral-800/60">
              <span className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider block">
                Monitoramento das Cestas Ativas:
              </span>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {activePositionsList.map((pos) => {
                  const pnl = pos.pnl_pct || 0
                  const isPosProfit = pnl >= 0
                  return (
                    <div
                      key={pos.symbol}
                      className="p-3 bg-neutral-900/80 rounded-lg border border-neutral-800 flex items-center justify-between font-mono"
                    >
                      <div>
                        <span className="text-xs font-bold text-white block">{pos.symbol}</span>
                        <span className="text-[10px] text-neutral-400">
                          Entrada: {formatPrice(pos.entry_price, currency)}
                        </span>
                      </div>
                      <div className="text-right">
                        <span
                          className={`text-xs font-bold ${
                            isPosProfit ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {isPosProfit ? '+' : ''}{pnl.toFixed(2)}%
                        </span>
                        <span className="text-[10px] text-neutral-500 block">
                          Atual: {formatPrice(pos.current_price, currency)}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Banner de Hold Ilimitado se aplicável */}
          {inGracePeriod && (
            <div className="p-3 bg-amber-950/40 border border-amber-500/40 rounded-lg text-xs text-amber-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">🛡️</span>
                <span>
                  <strong>Modo Hold Ilimitado Ativo:</strong> A janela inicial de 10 min se encerrou com posições abertas. O robô aguarda pacientemente ativos estagnados ou em alta lenta até alcançarem a <strong>Linha de Meta</strong>. Venda disparada apenas na Meta ou no Stop Loss de proteção.
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* SEÇÃO DINÂMICA: CHAT DA IA, GRÁFICO E TABELA DE STATUS */}
      <div className="mt-4 pt-4 border-t border-neutral-800/80 space-y-3">
        <div className="flex justify-between items-center mb-1 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse"></span>
            <h4 className="text-xs font-bold uppercase tracking-wider text-neutral-300">
              Painel de Observações & Inteligência do Day Trade
            </h4>
          </div>

          {/* Alternador de Visualização: Chat, Gráfico, Tabela */}
          <div className="flex rounded-lg border border-neutral-800 bg-neutral-900 p-0.5 text-[11px] font-mono">
            <button
              type="button"
              onClick={() => setViewMode('chat')}
              className={`px-3 py-1 rounded-md font-bold transition-all flex items-center gap-1.5 ${
                viewMode === 'chat'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                  : 'text-neutral-400 hover:text-white'
              }`}
            >
              <span>💬 Chat da IA</span>
              {chatMessages.length > 0 && (
                <span className="px-1.5 py-0.2 rounded-full bg-neutral-800 text-[10px] text-neutral-300 font-bold">
                  {chatMessages.length}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => setViewMode('chart')}
              className={`px-3 py-1 rounded-md font-bold transition-all ${
                viewMode === 'chart'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                  : 'text-neutral-400 hover:text-white'
              }`}
            >
              📊 Gráfico
            </button>
            <button
              type="button"
              onClick={() => setViewMode('table')}
              className={`px-3 py-1 rounded-md font-bold transition-all ${
                viewMode === 'table'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                  : 'text-neutral-400 hover:text-white'
              }`}
            >
              📋 Tabela ({snapshots.length})
            </button>
          </div>
        </div>

        {/* MODO 1: CHAT AO VIVO DA IA COM JUSTIFICATIVAS */}
        {viewMode === 'chat' && (
          <SniperChatFeed messages={chatMessages} isRunning={isRunning} />
        )}

        {/* MODO 2: GRÁFICO DE LINHA DINÂMICO */}
        {viewMode === 'chart' && (
          <DaytradeChart
            snapshots={snapshots}
            entryPrice={session?.entry_price}
            inPosition={session?.in_position}
            currency={currency}
            sessionPositions={session?.positions}
            allocatedTargets={session?.allocated_targets}
            microtrades={microtrades}
          />
        )}

        {/* MODO 3: TABELA DE STATUS */}
        {viewMode === 'table' && (
          <div className="space-y-2 max-h-56 overflow-y-auto pr-1 custom-scrollbar">
            {snapshots.length === 0 ? (
              <div className="p-8 text-center text-neutral-500 text-xs font-mono">
                Nenhum snapshot registrado ainda na sessão atual.
              </div>
            ) : (
              snapshots.map((snap, idx) => {
                const isProfit = (snap.unrealized_pnl_pct || 0) >= 0
                return (
                  <div
                    key={idx}
                    className="flex justify-between items-center p-2.5 rounded-lg bg-neutral-950/60 border border-neutral-800/60 text-xs font-mono hover:border-neutral-700 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-neutral-500 text-[11px]">
                        {snap.timestamp ? new Date(snap.timestamp).toLocaleTimeString('pt-BR') : `T+${snap.seconds_elapsed}s`}
                      </span>
                      <span
                        className={`px-2 py-0.5 text-[10px] font-bold rounded ${
                          snap.in_position
                            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                            : 'bg-neutral-800 text-neutral-400'
                        }`}
                      >
                        {snap.symbol || (snap.in_position ? 'POSICIONADO' : 'VARRENDO MERCADO')}
                      </span>
                      <span className="text-white font-semibold">
                        {formatPrice(snap.current_price, currency)}
                      </span>
                    </div>

                    <div className="flex items-center gap-4">
                      {snap.rsi && (
                        <span className="text-neutral-400 text-[11px]">
                          RSI: <strong className="text-neutral-200">{snap.rsi.toFixed(1)}</strong>
                        </span>
                      )}
                      <span
                        className={`font-bold ${
                          snap.in_position ? (isProfit ? 'text-emerald-400' : 'text-rose-400') : 'text-neutral-500'
                        }`}
                      >
                        PnL: {snap.in_position ? `${isProfit ? '+' : ''}${(snap.unrealized_pnl_pct || 0).toFixed(2)}%` : '0.00%'}
                      </span>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        )}
      </div>

      {/* HISTÓRICO DE MICRO-TRADES EXECUTADOS */}
      {microtrades.length > 0 && (
        <div className="mt-4 pt-4 border-t border-neutral-800/80 space-y-2">
          <h4 className="text-xs font-bold uppercase tracking-wider text-neutral-400 flex items-center gap-2">
            <span>🏁 Execuções Recentes do Sniper ({microtrades.length})</span>
          </h4>
          <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1 custom-scrollbar">
            {microtrades.map((mt, idx) => {
              const isBuy = mt.action === 'BUY'
              const isProfit = (mt.pnl_pct || 0) >= 0
              return (
                <div
                  key={idx}
                  className="flex justify-between items-center p-2.5 rounded-lg bg-neutral-950/40 border border-neutral-850 text-xs font-mono"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        isBuy
                          ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                          : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                      }`}
                    >
                      {mt.action}
                    </span>
                    <span className="font-bold text-white">{mt.symbol || mt.pair || 'ALT/USDT'}</span>
                    <span className="text-neutral-400">@ {formatPrice(mt.price, mt.currency)}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {mt.reason && (
                      <span className="text-[10px] text-neutral-500 truncate max-w-[160px]">
                        {mt.reason}
                      </span>
                    )}
                    {!isBuy && mt.pnl_pct !== undefined && (
                      <span className={`font-bold ${isProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {isProfit ? '+' : ''}{mt.pnl_pct.toFixed(2)}%
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* MODAL DE RELATÓRIO DETALHADO DO DIA */}
      {isDailyReportOpen && dailySummary && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-3 sm:p-6 overflow-y-auto"
          onClick={() => setIsDailyReportOpen(false)}
        >
          <div
            className="bg-neutral-950 border border-neutral-800 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col shadow-[0_0_60px_rgba(0,0,0,0.9)] overflow-hidden animate-in fade-in zoom-in-95 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header do Modal */}
            <div className="px-6 py-4 border-b border-neutral-800/80 flex items-center justify-between bg-neutral-900/70">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-lg shadow-inner">
                  📑
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-extrabold text-white font-mono">
                      Relatório Diário de Operações Sniper
                    </h3>
                    <span className="px-2.5 py-0.5 rounded-full bg-neutral-800 text-xs font-mono text-neutral-300 border border-neutral-700">
                      {dailySummary.date}
                    </span>
                  </div>
                  <p className="text-xs text-neutral-400">
                    Histórico consolidado de todas as ordens e posições do Sniper Trading hoje
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsDailyReportOpen(false)}
                className="w-8 h-8 rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-400 hover:text-white hover:bg-neutral-800 flex items-center justify-center transition-colors text-base"
                title="Fechar (Esc)"
              >
                ✕
              </button>
            </div>

            {/* KPI Bar Resumo dentro do Modal */}
            <div className="p-4 border-b border-neutral-800/60 bg-neutral-950/80 grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              <div className="p-2.5 rounded-lg bg-neutral-900/80 border border-neutral-800">
                <span className="text-[10px] text-neutral-400 uppercase font-bold block">P&L BRL</span>
                <span className={`text-base font-extrabold font-mono ${dailySummary.net_pnl_brl > 0 ? 'text-emerald-400' : dailySummary.net_pnl_brl < 0 ? 'text-rose-400' : 'text-neutral-300'}`}>
                  {dailySummary.net_pnl_brl > 0 ? '+' : ''}
                  {dailySummary.net_pnl_brl.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                </span>
                <span className="text-[10px] text-neutral-500 font-mono block mt-0.5">
                  Vol: {dailySummary.total_volume_brl.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                </span>
              </div>
              <div className="p-2.5 rounded-lg bg-neutral-900/80 border border-neutral-800">
                <span className="text-[10px] text-neutral-400 uppercase font-bold block">P&L USDT</span>
                <span className={`text-base font-extrabold font-mono ${dailySummary.net_pnl_usdt > 0 ? 'text-emerald-400' : dailySummary.net_pnl_usdt < 0 ? 'text-rose-400' : 'text-neutral-300'}`}>
                  {dailySummary.net_pnl_usdt > 0 ? '+' : ''}
                  ${dailySummary.net_pnl_usdt.toFixed(2)} USDT
                </span>
                <span className="text-[10px] text-neutral-500 font-mono block mt-0.5">
                  Vol: ${dailySummary.total_volume_usdt.toFixed(2)}
                </span>
              </div>
              <div className="p-2.5 rounded-lg bg-neutral-900/80 border border-neutral-800">
                <span className="text-[10px] text-neutral-400 uppercase font-bold block">Taxa de Acerto</span>
                <span className={`text-base font-extrabold font-mono ${dailySummary.win_rate_pct >= 50 ? 'text-emerald-400' : dailySummary.win_rate_pct > 0 ? 'text-amber-400' : 'text-neutral-400'}`}>
                  {dailySummary.win_rate_pct.toFixed(1)}%
                </span>
                <span className="text-[10px] text-neutral-500 font-mono block mt-0.5">
                  {dailySummary.winning_trades} vitórias / {dailySummary.losing_trades} derrotas
                </span>
              </div>
              <div className="p-2.5 rounded-lg bg-neutral-900/80 border border-neutral-800">
                <span className="text-[10px] text-neutral-400 uppercase font-bold block">Total de Ordens</span>
                <span className="text-base font-extrabold font-mono text-white">
                  {dailySummary.total_trades}
                  <span className="text-xs font-normal text-neutral-400 ml-1">posições</span>
                </span>
                <span className="text-[10px] text-neutral-500 font-mono block mt-0.5">
                  {dailySummary.total_orders} ordens executadas
                </span>
              </div>
            </div>

            {/* Barra de Filtros e Busca */}
            <div className="px-6 py-3 border-b border-neutral-800/60 bg-neutral-900/40 flex justify-between items-center flex-wrap gap-3">
              <div className="flex gap-1.5 bg-neutral-900 p-1 rounded-lg border border-neutral-800">
                <button
                  type="button"
                  onClick={() => setDailyReportFilter('all')}
                  className={`px-3 py-1 text-xs font-mono font-bold rounded-md transition-all ${
                    dailyReportFilter === 'all'
                      ? 'bg-neutral-800 text-white shadow'
                      : 'text-neutral-400 hover:text-neutral-200'
                  }`}
                >
                  Todas ({dailySummary.trades.length})
                </button>
                <button
                  type="button"
                  onClick={() => setDailyReportFilter('profit')}
                  className={`px-3 py-1 text-xs font-mono font-bold rounded-md transition-all ${
                    dailyReportFilter === 'profit'
                      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 shadow'
                      : 'text-neutral-400 hover:text-emerald-400'
                  }`}
                >
                  Lucros 🟢 ({dailySummary.winning_trades})
                </button>
                <button
                  type="button"
                  onClick={() => setDailyReportFilter('loss')}
                  className={`px-3 py-1 text-xs font-mono font-bold rounded-md transition-all ${
                    dailyReportFilter === 'loss'
                      ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30 shadow'
                      : 'text-neutral-400 hover:text-rose-400'
                  }`}
                >
                  Prejuízos / Stops 🔴 ({dailySummary.losing_trades})
                </button>
              </div>

              {/* Input de Busca */}
              <div className="relative">
                <input
                  type="text"
                  placeholder="Buscar moeda (ex: XRP)..."
                  value={dailyReportSearch}
                  onChange={(e) => setDailyReportSearch(e.target.value)}
                  className="px-3 py-1.5 bg-neutral-900 border border-neutral-800 text-xs font-mono text-white rounded-lg focus:outline-none focus:border-rose-500 w-48 sm:w-56 placeholder:text-neutral-600"
                />
                {dailyReportSearch && (
                  <button
                    type="button"
                    onClick={() => setDailyReportSearch('')}
                    className="absolute right-2 top-1.5 text-xs text-neutral-400 hover:text-white"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>

            {/* Lista com Rolagem das Operações */}
            <div className="p-4 sm:p-6 overflow-y-auto flex-1 divide-y divide-neutral-800/50 space-y-2 max-h-[50vh] custom-scrollbar">
              {filteredDailyTrades.length === 0 ? (
                <div className="py-16 text-center text-neutral-500 font-mono text-xs">
                  Nenhuma operação encontrada com os filtros aplicados.
                </div>
              ) : (
                filteredDailyTrades.map((t, idx) => {
                  const isBuy = t.action === 'BUY'
                  const pnl = t.net_pnl_fiat ?? 0
                  const isProfit = pnl > 0
                  const isLoss = pnl < 0
                  const timeFormatted = t.time || (t.timestamp ? new Date(t.timestamp).toLocaleTimeString('pt-BR') : '--:--')

                  return (
                    <div
                      key={`${t.timestamp}_${idx}`}
                      className="pt-3 pb-3 px-3.5 rounded-xl hover:bg-neutral-900/60 transition-colors flex justify-between items-center flex-wrap gap-3"
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-[11px] font-mono text-neutral-500 w-16">
                          {timeFormatted}
                        </span>
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-mono font-extrabold border ${
                            isBuy
                              ? 'bg-cyan-950/40 text-cyan-300 border-cyan-800/50'
                              : isProfit
                              ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/50'
                              : isLoss
                              ? 'bg-rose-950/40 text-rose-300 border-rose-800/50'
                              : 'bg-neutral-800 text-neutral-300 border-neutral-700'
                          }`}
                        >
                          {isBuy ? 'COMPRA' : 'VENDA'}
                        </span>
                        <div>
                          <span className="font-extrabold text-sm text-white font-mono">
                            {t.symbol || t.pair}
                          </span>
                          <span className="text-xs text-neutral-400 font-mono ml-2">
                            {isBuy
                              ? `@ ${formatPrice(t.price, t.currency)}`
                              : `${formatPrice(t.buy_price, t.currency)} ➔ ${formatPrice(t.sell_price || t.price, t.currency)}`}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-4 text-right">
                        <div>
                          <span className="text-xs text-neutral-300 font-mono block">
                            Vol: {formatPrice(t.amount, t.currency)}
                          </span>
                          <span className="text-[10px] text-neutral-500 font-mono block truncate max-w-[220px]" title={t.exit_reason || t.reason}>
                            {t.exit_reason || t.reason || (isBuy ? 'Entrada Scanner' : 'Encerramento')}
                          </span>
                        </div>

                        {!isBuy && (
                          <div className="min-w-[95px] text-right">
                            <span
                              className={`text-sm font-extrabold font-mono block ${
                                isProfit ? 'text-emerald-400' : isLoss ? 'text-rose-400' : 'text-neutral-300'
                              }`}
                            >
                              {isProfit ? '+' : ''}
                              {formatPrice(pnl, t.currency)}
                            </span>
                            <span
                              className={`text-[10px] font-mono block ${
                                (t.pnl_pct || 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'
                              }`}
                            >
                              ({(t.pnl_pct || 0) >= 0 ? '+' : ''}{(t.pnl_pct || 0).toFixed(2)}%)
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* Footer do Modal */}
            <div className="px-6 py-3.5 border-t border-neutral-800/80 bg-neutral-900/60 flex justify-between items-center">
              <p className="text-[11px] text-neutral-500 hidden sm:block">
                💡 <em>Todas as posições respeitam a Linha de Meta e Trailing Stop configurados para cobrir taxas da Binance.</em>
              </p>
              <button
                type="button"
                onClick={() => setIsDailyReportOpen(false)}
                className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-white font-mono text-xs font-bold rounded-lg transition-colors ml-auto shadow"
              >
                Fechar Relatório
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

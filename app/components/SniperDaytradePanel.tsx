'use client'

import React, { useState, useEffect, useCallback } from 'react'
import DaytradeChart from './DaytradeChart'

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
  symbol?: string
  pair?: string
  price: number
  qty: number
  amount: number
  currency: string
  reason?: string
  exit_reason?: string
  pnl_pct?: number
  net_pnl_fiat?: number
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

export default function SniperDaytradePanel() {
  const [capital, setCapital] = useState<number>(10)
  const [currency, setCurrency] = useState<'BRL' | 'USDT'>('USDT')
  const [sourceAsset, setSourceAsset] = useState<string>('BTC')
  const [walletAssets, setWalletAssets] = useState<WalletAsset[]>([])
  const [session, setSession] = useState<SessionData | null>(null)
  const [estimatedWaitSeconds, setEstimatedWaitSeconds] = useState<number>(0)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [microtrades, setMicrotrades] = useState<MicroTrade[]>([])
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
  const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart')

  // Polling dos dados da sessão a cada 3 segundos
  const fetchDaytradeState = useCallback(async () => {
    try {
      const res = await fetch('/api/daytrade', { cache: 'no-store' })
      if (!res.ok) return
      const data = await res.json()
      setSession(data.session)
      setSnapshots(data.snapshots || [])
      setMicrotrades(data.microtrades || [])

      if (data.balances) {
        setBalances(data.balances)
      }
      if (data.walletAssets && data.walletAssets.length > 0) {
        setWalletAssets(data.walletAssets)
      }
      if (data.botConfig) {
        setIsDryRun(data.botConfig.dry_run ?? false)
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
      } else if (elapsedSeconds < 720) {
        setRemainingSessionSeconds(0)
        setInGracePeriod(true)
        setGraceSeconds(720 - elapsedSeconds)
      } else {
        setRemainingSessionSeconds(0)
        setGraceSeconds(0)
        setInGracePeriod(false)
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

  const formatTimer = (totalSec: number) => {
    const m = Math.floor(Math.max(0, totalSec) / 60)
    const s = Math.max(0, totalSec) % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const formatPrice = (val?: number, curr = 'USDT') => {
    if (!val || isNaN(val)) return curr === 'USDT' ? '$ 0,00' : 'R$ 0,00'
    const decimals = val < 0.001 ? 8 : val < 1 ? 4 : 2
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
              Varredura algorítmica de altcoins voláteis (PEPE, NEAR, DOGE, SUI) com distribuição de capital e trailing stop individual
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
                        Math.max(1, Math.min(2, Math.floor(availableBuyingPower))),
                        Math.max(1, Math.min(5, Math.floor(availableBuyingPower))),
                        Math.max(1, Math.min(10, Math.floor(availableBuyingPower))),
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
            {/* Relógio Regressivo Principal */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-rose-500/30 text-center shadow-[inset_0_0_20px_rgba(244,63,94,0.1)]">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                {inGracePeriod ? 'Tolerância Anti-Loss' : 'Tempo Restante'}
              </span>
              <span
                className={`text-4xl font-extrabold font-mono tracking-tight ${
                  inGracePeriod ? 'text-amber-400 animate-pulse' : 'text-rose-400'
                }`}
              >
                {inGracePeriod ? formatTimer(graceSeconds) : formatTimer(remainingSessionSeconds)}
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                {inGracePeriod ? 'Aguardando recuperação (+2m max)' : 'Janela total: 10:00 min'}
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
                <span className="text-xs text-rose-400 font-mono font-bold">
                  (Flash Liquidity)
                </span>
              </div>
              <p className="text-xs text-neutral-400 font-mono mt-1">
                Capital: {session?.currency === 'BRL' ? `R$ ${session?.capital?.toFixed(2)}` : `$${session?.capital?.toFixed(2)} USDT`}
              </p>
            </div>

            {/* PnL Geral da Sessão */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Retorno Consolidado
              </span>
              <span
                className={`text-2xl font-bold font-mono ${
                  (session?.total_pnl_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {(session?.total_pnl_pct || 0) >= 0 ? '+' : ''}
                {(session?.total_pnl_pct || 0).toFixed(2)}%
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Trailing Stop: +0.50% | Take Profit: +0.70%
              </span>
            </div>

            {/* Operações Concluídas */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Micro-Trades Realizados
              </span>
              <span className="text-2xl font-bold font-mono text-white">
                {session?.trades_count || 0}
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Ativos ativos: {activePositionsList.length}
              </span>
            </div>
          </div>

          {/* MULTI-ASSET CARDS: Exibe cada posição acompanhada de forma individual */}
          {activePositionsList.length > 0 && (
            <div className="space-y-2 pt-2">
              <h4 className="text-xs font-bold uppercase tracking-wider text-neutral-300 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                Posições Acompanhadas Individualmente pelo Scanner
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                {activePositionsList.map((pos) => {
                  const isProfit = (pos.pnl_pct || 0) >= 0
                  return (
                    <div
                      key={pos.symbol}
                      className="p-3 rounded-xl bg-neutral-950/80 border border-neutral-800 hover:border-neutral-700 transition-all space-y-1.5"
                    >
                      <div className="flex justify-between items-center">
                        <span className="font-extrabold text-sm text-white font-mono">{pos.symbol}</span>
                        <span
                          className={`text-xs font-bold font-mono px-2 py-0.5 rounded ${
                            isProfit
                              ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/50'
                              : 'bg-rose-950/80 text-rose-400 border border-rose-800/50'
                          }`}
                        >
                          {isProfit ? '+' : ''}{(pos.pnl_pct || 0).toFixed(2)}%
                        </span>
                      </div>
                      <div className="text-xs text-neutral-400 font-mono flex justify-between">
                        <span>Entrada: {formatPrice(pos.entry_price, session?.currency)}</span>
                        <span className="text-white">Atual: {formatPrice(pos.current_price, session?.currency)}</span>
                      </div>
                      <div className="text-[10px] text-neutral-500 flex justify-between items-center pt-1 border-t border-neutral-900">
                        <span>Alocado: ${(pos.entry_cost || 0).toFixed(2)}</span>
                        <span>{pos.trailing_active ? '⚡ Trailing Ativo' : '🎯 Alvo +0.7%'}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Banner de Tolerância se aplicável */}
          {inGracePeriod && (
            <div className="p-3 bg-amber-950/40 border border-amber-500/40 rounded-lg text-xs text-amber-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">🛡️</span>
                <span>
                  <strong>Regra de Proteção Ativa:</strong> Os 10 minutos se esgotaram com posições abertas. O robô está aguardando até 2 minutos adicionais para sair no breakeven ou com lucro antes de encerrar.
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* GRÁFICO E FEED DE STATUS A CADA 30 SEGUNDOS */}
      {snapshots.length > 0 && (
        <div className="mt-4 pt-4 border-t border-neutral-800/80 space-y-3">
          <div className="flex justify-between items-center mb-1 flex-wrap gap-2">
            <h4 className="text-xs font-bold uppercase tracking-wider text-neutral-400 flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-pulse"></span>
              Histórico de Status (30s) — {snapshots.length} registros
            </h4>

            {/* Alternador Gráfico vs Tabela */}
            <div className="flex rounded-lg border border-neutral-800 bg-neutral-900 p-0.5 text-[11px] font-mono">
              <button
                type="button"
                onClick={() => setViewMode('chart')}
                className={`px-2.5 py-1 rounded-md font-bold transition-all ${
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
                className={`px-2.5 py-1 rounded-md font-bold transition-all ${
                  viewMode === 'table'
                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                    : 'text-neutral-400 hover:text-white'
                }`}
              >
                📋 Tabela
              </button>
            </div>
          </div>

          {/* MODO 1: GRÁFICO DE LINHA DINÂMICO */}
          {viewMode === 'chart' && (
            <DaytradeChart
              snapshots={snapshots}
              entryPrice={session?.entry_price}
              inPosition={session?.in_position}
              currency={currency}
            />
          )}

          {/* MODO 2: TABELA DE STATUS */}
          {viewMode === 'table' && (
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1 custom-scrollbar">
              {snapshots.map((snap, idx) => {
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
              })}
            </div>
          )}
        </div>
      )}

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
    </section>
  )
}

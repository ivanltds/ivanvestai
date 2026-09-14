'use client'

import React, { useState, useEffect, useCallback } from 'react'

interface SessionData {
  status: 'idle' | 'pending' | 'running' | 'completed' | 'cancelled'
  capital?: number
  currency?: string
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
  in_recovery_grace?: boolean
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
  price: number
  qty: number
  amount: number
  currency: string
  reason?: string
  pnl_pct?: number
}

export default function SniperDaytradePanel() {
  const [capital, setCapital] = useState<number>(50)
  const [currency, setCurrency] = useState<'BRL' | 'USDT'>('BRL')
  const [session, setSession] = useState<SessionData | null>(null)
  const [estimatedWaitSeconds, setEstimatedWaitSeconds] = useState<number>(0)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [microtrades, setMicrotrades] = useState<MicroTrade[]>([])
  const [loading, setLoading] = useState<boolean>(false)
  const [remainingSessionSeconds, setRemainingSessionSeconds] = useState<number>(600)
  const [graceSeconds, setGraceSeconds] = useState<number>(0)
  const [inGracePeriod, setInGracePeriod] = useState<boolean>(false)

  // Polling dos dados da sessão a cada 3 segundos
  const fetchDaytradeState = useCallback(async () => {
    try {
      const res = await fetch('/api/daytrade', { cache: 'no-store' })
      if (!res.ok) return
      const data = await res.json()
      setSession(data.session)
      setSnapshots(data.snapshots || [])
      setMicrotrades(data.microtrades || [])

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

  // Cronômetro regressivo de precisão do Day Trade (ativo APENAS após started_at)
  useEffect(() => {
    if (!session || session.status !== 'running' || !session.started_at) {
      return
    }

    const timer = setInterval(() => {
      const startedAtMs = new Date(session.started_at!).getTime()
      const nowMs = Date.now()
      const elapsedSeconds = Math.floor((nowMs - startedAtMs) / 1000)

      if (elapsedSeconds < 600) {
        setRemainingSessionSeconds(600 - elapsedSeconds)
        setInGracePeriod(false)
        setGraceSeconds(0)
      } else if (elapsedSeconds < 720) {
        // Tolerância de 2 minutos para recuperação de loss
        setRemainingSessionSeconds(0)
        setInGracePeriod(true)
        setGraceSeconds(720 - elapsedSeconds)
      } else {
        // Hard stop atingido
        setRemainingSessionSeconds(0)
        setGraceSeconds(0)
        setInGracePeriod(false)
      }
    }, 1000)

    return () => clearInterval(timer)
  }, [session])

  // Submissão: Iniciar Modo Sniper
  const handleStart = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'request', capital, currency }),
      })
      if (res.ok) {
        await fetchDaytradeState()
      }
    } catch (e) {
      console.error('Erro ao iniciar Daytrade:', e)
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

  const isPending = session?.status === 'pending'
  const isRunning = session?.status === 'running'
  const isCompleted = session?.status === 'completed'

  return (
    <section className="bg-neutral-900/60 rounded-2xl border border-rose-950/40 p-6 backdrop-blur-md relative overflow-hidden shadow-[0_0_40px_rgba(244,63,94,0.05)]">
      {/* Luz neon de fundo sutil indicando Alto Risco */}
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
                Sniper Day Trade <span className="text-rose-500 text-xs px-2 py-0.5 rounded-full bg-rose-950/60 border border-rose-800/60 uppercase tracking-widest font-mono">10 MIN • ALTO RISCO</span>
              </h2>
            </div>
            <p className="text-xs text-neutral-400">Micro-scalping com Bandas de Bollinger, RSI-7 e VWAP em ticks de 3s</p>
          </div>
        </div>

        {/* Status Badge */}
        <div>
          {isRunning ? (
            <span className="px-3 py-1.5 bg-rose-500/20 text-rose-300 text-xs font-bold font-mono rounded-lg border border-rose-500/40 flex items-center gap-2 animate-pulse shadow-[0_0_15px_rgba(244,63,94,0.2)]">
              <span className="w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping"></span>
              SESSÃO EM ANDAMENTO
            </span>
          ) : isPending ? (
            <span className="px-3 py-1.5 bg-amber-500/20 text-amber-300 text-xs font-bold font-mono rounded-lg border border-amber-500/40 flex items-center gap-2 shadow-[0_0_15px_rgba(245,158,11,0.15)]">
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
              AGUARDANDO CICLO DOS AGENTES
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
        <div className="bg-neutral-950/60 border border-neutral-800/80 rounded-xl p-5 mb-5 space-y-4">
          <div className="flex justify-between items-center flex-wrap gap-4">
            <div className="space-y-1">
              <label className="text-xs font-bold uppercase tracking-wider text-neutral-400 block">
                Capital Alocado para a Sessão
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
                    min={currency === 'BRL' ? 10 : 5}
                    step={5}
                    className="pl-9 pr-3 py-2 bg-neutral-900 border border-neutral-700 text-white font-mono font-bold text-base rounded-lg w-32 focus:outline-none focus:border-rose-500"
                  />
                </div>

                {/* Presets */}
                <div className="flex gap-1.5">
                  {(currency === 'BRL' ? [30, 50, 100] : [5, 10, 20]).map((val) => (
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

                {/* Seletor de Moeda */}
                <div className="flex rounded-lg border border-neutral-800 bg-neutral-900 p-0.5 ml-2">
                  <button
                    type="button"
                    onClick={() => setCurrency('BRL')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${
                      currency === 'BRL' ? 'bg-neutral-800 text-white shadow' : 'text-neutral-500 hover:text-neutral-300'
                    }`}
                  >
                    BRL
                  </button>
                  <button
                    type="button"
                    onClick={() => setCurrency('USDT')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${
                      currency === 'USDT' ? 'bg-neutral-800 text-emerald-400 shadow' : 'text-neutral-500 hover:text-neutral-300'
                    }`}
                  >
                    USDT
                  </button>
                </div>
              </div>
            </div>

            {/* Botão de Disparo */}
            <div className="flex items-end">
              <button
                onClick={handleStart}
                disabled={loading || capital <= 0}
                className="px-6 py-3 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-extrabold text-sm rounded-xl transition-all shadow-[0_0_20px_rgba(225,29,72,0.3)] flex items-center gap-2.5 cursor-pointer disabled:opacity-50"
              >
                <span>⚡</span>
                <span>ATIVAR SESSÃO SNIPER (10 MIN)</span>
              </button>
            </div>
          </div>
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
                  Solicitação de Day Trade Programada!
                </h3>
              </div>
              <p className="text-sm text-neutral-300">
                Capital Configurado:{' '}
                <strong className="text-white font-mono">
                  {currency === 'BRL' ? `R$ ${session?.capital?.toFixed(2)}` : `$ ${session?.capital?.toFixed(2)} USDT`}
                </strong>{' '}
                no par <strong className="text-white font-mono">{session?.symbol}</strong>.
              </p>
              <div className="p-3 bg-neutral-900/80 rounded-lg border border-neutral-800 inline-block">
                <p className="text-xs text-neutral-400">
                  Prazo estimado para o agente assumir a operação:
                </p>
                <p className="text-lg font-mono font-bold text-amber-400">
                  ~ {Math.ceil(estimatedWaitSeconds / 60)} min ({estimatedWaitSeconds}s)
                </p>
                <p className="text-[11px] text-neutral-500 mt-0.5">
                  Sincronizado automaticamente com a próxima varredura de mercado dos agentes.
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

      {/* STATUS: RUNNING (CRONÔMETRO DE 10 MIN AO VIVO) */}
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

            {/* Posição Aberta Atual */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Posição do Sniper
              </span>
              <div className="flex items-baseline gap-2">
                <span
                  className={`text-lg font-bold font-mono ${
                    session?.in_position ? 'text-emerald-400' : 'text-neutral-400'
                  }`}
                >
                  {session?.in_position ? 'COMPRADO (BTC)' : 'LIQUIDEZ LIVRE'}
                </span>
              </div>
              {session?.in_position && session.entry_price && (
                <p className="text-xs text-neutral-400 font-mono mt-1">
                  Entrada: {session.entry_price.toLocaleString('pt-BR', { style: 'currency', currency: session.currency || 'BRL' })}
                </p>
              )}
            </div>

            {/* PnL Não Realizado */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                PnL da Posição Aberta
              </span>
              <span
                className={`text-2xl font-bold font-mono ${
                  (session?.position_pnl_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {(session?.position_pnl_pct || 0) >= 0 ? '+' : ''}
                {(session?.position_pnl_pct || 0).toFixed(2)}%
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Trailing Stop: +0.50% | Take Profit: +0.70%
              </span>
            </div>

            {/* Operações Concluídas */}
            <div className="md:col-span-1 p-4 rounded-xl bg-neutral-950/90 border border-neutral-800">
              <span className="text-[10px] uppercase tracking-wider text-neutral-400 font-bold block mb-1">
                Micro-Trades Feitos
              </span>
              <span className="text-2xl font-bold font-mono text-white">
                {session?.trades_count || 0}
              </span>
              <span className="text-[10px] text-neutral-500 block mt-1">
                Retorno acumulado: {(session?.total_pnl_pct || 0) >= 0 ? '+' : ''}{(session?.total_pnl_pct || 0).toFixed(2)}%
              </span>
            </div>
          </div>

          {/* Banner de Tolerância se aplicável */}
          {inGracePeriod && (
            <div className="p-3 bg-amber-950/40 border border-amber-500/40 rounded-lg text-xs text-amber-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">🛡️</span>
                <span>
                  <strong>Regra de Proteção Ativa:</strong> Os 10 minutos se esgotaram com a posição em ligeiro prejuízo. O robô está aguardando até 2 minutos adicionais para sair no breakeven ou com lucro antes de encerrar.
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* FEED DE STATUS A CADA 30 SEGUNDOS */}
      {snapshots.length > 0 && (
        <div className="mt-4 pt-4 border-t border-neutral-800/80">
          <div className="flex justify-between items-center mb-3">
            <h4 className="text-xs font-bold uppercase tracking-wider text-neutral-400 flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-pulse"></span>
              Histórico de Status a cada 30 segundos ({snapshots.length} registros)
            </h4>
            <span className="text-[10px] text-neutral-500 font-mono">Atualização automática</span>
          </div>

          <div className="space-y-2 max-h-48 overflow-y-auto pr-1 custom-scrollbar">
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
                      {snap.in_position ? 'POSICIONADO' : 'AGUARDANDO GATILHO'}
                    </span>
                    <span className="text-white font-semibold">
                      {snap.current_price?.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
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
        </div>
      )}
    </section>
  )
}

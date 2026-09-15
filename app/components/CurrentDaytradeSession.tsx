'use client'

import React, { useState, useEffect } from 'react'
import {
  Zap,
  Clock,
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  Play,
  Square,
  Target,
  Shield,
  Activity,
  BarChart2,
  Bot,
} from 'lucide-react'
import DaytradeChart from './DaytradeChart'
import SniperChatFeed from './SniperChatFeed'
import Tooltip from './Tooltip'

export default function CurrentDaytradeSession() {
  const [sessionData, setSessionData] = useState<any>(null)
  const [snapshots, setSnapshots] = useState<any[]>([])
  const [chatMessages, setChatMessages] = useState<any[]>([])
  const [microtrades, setMicrotrades] = useState<any[]>([])
  const [btcMacro, setBtcMacro] = useState<any>(null)
  const [autoConfig, setAutoConfig] = useState<any>(null)
  const [isExpanded, setIsExpanded] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [timerLeft, setTimerLeft] = useState<number | null>(null)

  const fetchSession = async () => {
    try {
      const res = await fetch('/api/daytrade')
      if (res.ok) {
        const json = await res.json()
        setSessionData(json.session)
        setSnapshots(json.snapshots || [])
        setChatMessages(json.chatMessages || [])
        setMicrotrades(json.microtrades || [])
        setBtcMacro(json.btcMacroRegime || null)
        setAutoConfig(json.autoConfig || null)

        if (json.session?.status === 'running' && json.session?.timer_seconds_left !== undefined) {
          setTimerLeft(json.session.timer_seconds_left)
        }
      }
    } catch (e) {
      console.error('Erro ao buscar sessão daytrade:', e)
    }
  }

  useEffect(() => {
    fetchSession()
    const timer = setInterval(fetchSession, 3000)
    return () => clearInterval(timer)
  }, [])

  // Decremento local do timer da sessão a cada segundo
  useEffect(() => {
    if (timerLeft === null || timerLeft <= 0) return
    const interval = setInterval(() => {
      setTimerLeft((prev) => (prev && prev > 1 ? prev - 1 : 0))
    }, 1000)
    return () => clearInterval(interval)
  }, [timerLeft])

  const handleManualTrigger = async () => {
    if (isSubmitting) return
    setIsSubmitting(true)
    try {
      const capital = autoConfig?.capital || 15.0
      const currency = autoConfig?.currency || 'USDT'
      const sourceAsset = autoConfig?.source_asset || 'USDT'

      await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'trigger',
          capital,
          currency,
          source_asset: sourceAsset,
        }),
      })
      await fetchSession()
    } catch (e) {
      console.error('Erro ao disparar sessão manual:', e)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleStopSession = async () => {
    if (isSubmitting) return
    setIsSubmitting(true)
    try {
      await fetch('/api/daytrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' }),
      })
      await fetchSession()
    } catch (e) {
      console.error('Erro ao encerrar sessão:', e)
    } finally {
      setIsSubmitting(false)
    }
  }

  const isRunning = sessionData?.status === 'running'
  const isPending = sessionData?.status === 'pending'
  const isFinished = sessionData?.status === 'finished'

  // Timer formatado
  const formatTimer = (sec: number | null) => {
    if (sec === null || sec < 0) return '00:00'
    const m = Math.floor(sec / 60)
    const s = Math.floor(sec % 60)
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  // PnL Consolidado
  const pnlPct = sessionData?.total_pnl_pct || 0
  const isProfit = pnlPct >= 0
  const sessionCurrency = sessionData?.currency || 'USDT'
  const capital = sessionData?.capital || 0
  const estimatedProfitUsd = (capital * pnlPct) / 100
  const estimatedProfitBrl = estimatedProfitUsd * 5.2

  // Posições ativas
  const activePositions: Record<string, any> = sessionData?.positions || {}
  const hasActivePositions = Object.keys(activePositions).length > 0

  return (
    <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md transition-all">
      {/* 1. HEADER DA SESSÃO DAYTRADE */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-neutral-800/80">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-xl border bg-neutral-800 border-neutral-700 text-neutral-300">
            <Zap className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold tracking-tight text-white font-mono uppercase">
                {isRunning
                  ? 'Sessão de Trading Ativa'
                  : isPending
                  ? 'Iniciando Sessão...'
                  : 'Sessão de Trading Automático'}
              </h2>
              {isRunning && (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-neutral-800 text-neutral-200 border border-neutral-700">
                  Ao Vivo
                </span>
              )}
            </div>
            <p className="text-[11px] text-neutral-400">
              O robô compra e vende automaticamente buscando lucros em movimentos rápidos de preço
            </p>
          </div>
        </div>

        {/* CONTROLES E BADGES DO TOPO */}
        <div className="flex items-center gap-2.5 flex-wrap">
          {/* GATEKEEPER BTC */}
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-mono ${
              btcMacro?.healthy !== false
                ? 'bg-neutral-900 border-neutral-800 text-neutral-300'
                : 'bg-rose-950/40 border-rose-800/50 text-rose-300'
            }`}
          >
            {btcMacro?.healthy !== false ? (
              <ShieldCheck className="w-3.5 h-3.5 text-neutral-400" />
            ) : (
              <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
            )}
            <span className="text-[11px]">BTC: {btcMacro?.regime || 'OK'}</span>
            <Tooltip
              position="bottom"
              text={`Filtro de mercado: o robô só opera quando o Bitcoin (ativo mais importante do mercado cripto) está em condições favoráveis. Isso reduz o risco de entrar em momentos ruins.${btcMacro?.reason ? ' Motivo: ' + btcMacro.reason : ''}`}
            />
          </div>

          {/* TIMER DA SESSÃO */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-neutral-950/80 border border-neutral-800 text-neutral-300 text-xs font-mono">
            <Clock className="w-3.5 h-3.5 text-neutral-400" />
            {isRunning ? (
              <span className="text-white font-bold">{formatTimer(timerLeft)} / 10:00</span>
            ) : autoConfig?.nextAutoTriggerSeconds ? (
              <span className="text-neutral-400">
                Próxima sessão em: {formatTimer(autoConfig.nextAutoTriggerSeconds)}
              </span>
            ) : (
              <span className="text-neutral-400">Aguardando</span>
            )}
            <Tooltip
              position="bottom"
              text="Contador da sessão atual (cada sessão dura até 10 minutos). Quando não há sessão ativa, mostra o tempo até a próxima execução automática."
            />
          </div>

          {/* BOTÃO DE CONTROLE (DISPARO / PARADA) */}
          {isRunning ? (
            <button
              type="button"
              onClick={handleStopSession}
              disabled={isSubmitting}
              className="flex items-center gap-1 px-3 py-1 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-neutral-300 border border-neutral-700 text-xs font-mono transition-colors"
            >
              <Square className="w-3 h-3 text-neutral-400" />
              <span>Encerrar</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={handleManualTrigger}
              disabled={isSubmitting || isPending}
              className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-white border border-neutral-700/80 text-xs font-mono font-semibold transition-all hover:border-neutral-500"
            >
              <Play className="w-3 h-3 text-white fill-white" />
              <span>Disparar Agora</span>
            </button>
          )}
        </div>
      </div>

      {/* 2. RESULTADO CONSOLIDADO DA SESSÃO */}
      <div className="py-4 border-b border-neutral-800/80">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-400 mb-1 flex items-center gap-1">
              Resultado da Sessão Atual
              <Tooltip
                position="right"
                text="Ganho ou perda acumulada nesta sessão, em percentual e valores estimados. Uma sessão pode ter múltiplas operações e dura até 10 minutos."
              />
            </p>
            <div className="flex items-baseline gap-3">
              <span
                className={`text-2xl font-mono font-bold ${
                  isProfit ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {pnlPct > 0 ? '+' : ''}
                {pnlPct.toFixed(2)}%
              </span>
              <span className="text-xs font-mono text-neutral-400">
                ({estimatedProfitUsd > 0 ? '+' : ''}$ {estimatedProfitUsd.toFixed(2)} USDT /{' '}
                {estimatedProfitBrl > 0 ? '+' : ''}R$ {estimatedProfitBrl.toFixed(2)})
              </span>
            </div>
          </div>

          <div className="flex items-center gap-4 text-xs font-mono text-right">
            <div>
              <p className="text-[10px] text-neutral-500 uppercase flex items-center gap-1 justify-end">
                Capital Alocado
                <Tooltip
                  position="left"
                  text="Valor em dinheiro que o robô está usando para operar nesta sessão. Este valor é reservado da sua carteira e devolvido (com lucro ou prejuízo) ao final da sessão."
                />
              </p>
              <p className="text-white font-bold">
                {sessionCurrency === 'BRL' ? `R$ ${capital.toFixed(2)}` : `$ ${capital.toFixed(2)} USDT`}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-neutral-500 uppercase">Status</p>
              <p className="text-neutral-300">
                {isRunning ? 'Em Execução' : isPending ? 'Preparando' : 'Aguardando'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 3. CARDS DE POSIÇÕES ABERTAS */}
      <div className="py-4">
        <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-400 mb-3 flex items-center gap-1.5">
          <Activity className="w-3 h-3" />
          Posições Abertas
          <Tooltip
            position="right"
            text="Uma posição aberta significa que o robô já comprou uma criptomoeda e ainda não vendeu. Ele está monitorando o preço para vender no melhor momento."
          />
        </p>

        {!hasActivePositions ? (
          <div className="p-5 rounded-xl bg-neutral-950/60 border border-neutral-800/80 text-center text-xs font-mono text-neutral-500">
            {isRunning
              ? 'O robô está analisando o mercado. Aguardando o melhor momento para entrar em uma operação...'
              : 'Nenhuma operação aberta no momento. O robô está aguardando o próximo ciclo automático.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(activePositions).map(([sym, pos]: [string, any]) => {
              const curPrice = pos.current_price || pos.entry_price || 0
              const entPrice = pos.entry_price || curPrice
              const posPnl = entPrice > 0 ? ((curPrice - entPrice) / entPrice) * 100 : 0
              const isPosProfit = posPnl >= 0
              const targetPrice = pos.target_price || entPrice * 1.02
              const stopPrice = pos.stop_price || entPrice * 0.988

              return (
                <div
                  key={sym}
                  className="p-4 rounded-xl bg-neutral-950/80 border border-neutral-800 hover:border-neutral-700 transition-all space-y-2.5 font-mono text-xs"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-white text-sm">{sym}</span>
                      <span
                        className={`flex items-center gap-0.5 text-[11px] font-bold px-1.5 py-0.5 rounded ${
                          isPosProfit
                            ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/60'
                            : 'bg-rose-950/80 text-rose-400 border border-rose-800/60'
                        }`}
                      >
                        {isPosProfit ? (
                          <ArrowUpRight className="w-3 h-3" />
                        ) : (
                          <ArrowDownRight className="w-3 h-3" />
                        )}
                        {posPnl > 0 ? '+' : ''}
                        {posPnl.toFixed(2)}%
                      </span>
                    </div>

                    <span className="text-[10px] text-neutral-500">
                      Atual: ${curPrice.toFixed(4)}
                    </span>
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-[11px] pt-2 border-t border-neutral-850">
                    <div>
                      <p className="text-[9px] text-neutral-500 uppercase flex items-center gap-0.5">
                        Preço de Entrada
                        <Tooltip
                          position="top"
                          text="Preço pelo qual o robô comprou esta moeda."
                          size="xs"
                        />
                      </p>
                      <p className="text-neutral-300 font-semibold">${entPrice.toFixed(4)}</p>
                    </div>
                    <div>
                      <p className="text-[9px] text-neutral-500 uppercase flex items-center gap-0.5">
                        Meta de Lucro
                        <Tooltip
                          position="top"
                          text="Preço-alvo para o robô vender e realizar o lucro. Ao atingir este valor, a posição é encerrada automaticamente."
                          size="xs"
                        />
                      </p>
                      <p className="text-emerald-400 font-semibold">${targetPrice.toFixed(4)}</p>
                    </div>
                    <div>
                      <p className="text-[9px] text-neutral-500 uppercase flex items-center gap-0.5">
                        Stop Loss
                        <Tooltip
                          position="top"
                          text="Preço de segurança: se o preço cair até aqui, o robô vende imediatamente para limitar o prejuízo. É uma proteção automática."
                          size="xs"
                        />
                      </p>
                      <p className="text-rose-400 font-semibold">${stopPrice.toFixed(4)}</p>
                    </div>
                  </div>

                  {pos.trailing_armed && (
                    <div className="flex items-center gap-1.5 text-[10px] text-amber-300 bg-amber-950/30 px-2 py-1 rounded border border-amber-800/40">
                      <Shield className="w-3 h-3 text-amber-400" />
                      <span>Proteção de lucro ativa (Trailing Stop)</span>
                      <Tooltip
                        position="left"
                        text="O Trailing Stop protege o lucro já obtido: conforme o preço sobe, o stop de segurança acompanha automaticamente. Se o preço reverter, a venda é acionada preservando o ganho."
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 4. EXPANSOR DISCRETO (GRÁFICO + CHAT) */}
      <div className="pt-2 border-t border-neutral-800/80">
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full py-2 flex items-center justify-center gap-2 text-xs font-mono text-neutral-400 hover:text-white transition-colors group"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="w-3.5 h-3.5 text-neutral-400 group-hover:text-white transition-transform" />
              <span>Recolher gráfico e decisões da IA</span>
            </>
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5 text-neutral-400 group-hover:text-white transition-transform" />
              <span>Ver gráfico de preços e decisões da IA</span>
            </>
          )}
        </button>

        {isExpanded && (
          <div className="mt-4 grid grid-cols-1 lg:grid-cols-12 gap-4 animate-in fade-in duration-300">
            {/* SUB-COLUNA 1 (65%): GRÁFICO TÉCNICO */}
            <div className="lg:col-span-8 bg-neutral-950/70 rounded-xl border border-neutral-800/80 p-3 overflow-hidden">
              <div className="flex items-center gap-1.5 mb-2 text-xs font-mono text-neutral-400 px-1">
                <BarChart2 className="w-3.5 h-3.5 text-neutral-300" />
                <span>Gráfico Técnico e Indicadores Intradiários</span>
              </div>
              <DaytradeChart
                snapshots={snapshots}
                sessionPositions={sessionData?.positions}
                currency={sessionCurrency}
                microtrades={microtrades}
              />
            </div>

            {/* SUB-COLUNA 2 (35%): CHAT DA IA */}
            <div className="lg:col-span-4">
              <SniperChatFeed messages={chatMessages} isRunning={isRunning} />
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

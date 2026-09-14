'use client'

import React, { useState, useEffect } from 'react'
import {
  Activity,
  Maximize2,
  TrendingUp,
  TrendingDown,
  X,
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  CheckCircle2,
  AlertTriangle,
  MinusCircle,
  RefreshCw,
} from 'lucide-react'

export interface DailySummaryData {
  totalTrades: number
  totalClosed: number
  winningTrades: number
  losingTrades: number
  breakevenTrades: number
  winRatePct: number
  netPnlBrl: number
  netPnlUsdt: number
  totalVolumeBrl: number
  totalVolumeUsdt: number
  todayDate: string
}

export interface DailyTradeItem {
  timestamp: string
  symbol: string
  action: string
  price: number
  currency: string
  net_pnl_fiat?: number
  pnl_pct?: number
  amount?: number
  qty?: number
  crypto_qty?: number
  reason?: string
}

interface DailyTradingSummaryCardProps {
  initialSummary?: DailySummaryData | null
  initialTrades?: DailyTradeItem[]
}

export default function DailyTradingSummaryCard({
  initialSummary,
  initialTrades = [],
}: DailyTradingSummaryCardProps) {
  const [summary, setSummary] = useState<DailySummaryData | null>(initialSummary || null)
  const [trades, setTrades] = useState<DailyTradeItem[]>(initialTrades)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [modalTab, setModalTab] = useState<'all' | 'wins' | 'losses'>('all')
  const [loading, setLoading] = useState(false)

  const fetchData = async () => {
    try {
      const res = await fetch('/api/daytrade')
      if (res.ok) {
        const json = await res.json()
        if (json.dailySummary) setSummary(json.dailySummary)
        if (json.dailyTrades) setTrades(json.dailyTrades)
      }
    } catch (e) {
      console.error('Erro ao atualizar balanço diário:', e)
    }
  }

  useEffect(() => {
    fetchData()
    const timer = setInterval(fetchData, 12000)
    return () => clearInterval(timer)
  }, [])

  const netUsdt = summary?.netPnlUsdt ?? 0
  const netBrl = summary?.netPnlBrl ?? 0
  const winRate = summary?.winRatePct ?? 0
  const totalClosed = summary?.totalClosed ?? 0
  const wins = summary?.winningTrades ?? 0
  const losses = summary?.losingTrades ?? 0
  const breakeven = summary?.breakevenTrades ?? 0

  const sellTrades = trades.filter((t) => t.action === 'SELL')
  const filteredModalTrades = (modalTab === 'all'
    ? trades
    : modalTab === 'wins'
    ? trades.filter((t) => (t.net_pnl_fiat ?? 0) > 0)
    : trades.filter((t) => (t.net_pnl_fiat ?? 0) < 0)
  )

  const formatBrl = (val: number) =>
    new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val)

  const formatUsdt = (val: number) =>
    `$ ${val.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`

  return (
    <>
      {/* CARD PRINCIPAL */}
      <section className="bg-neutral-900/60 rounded-2xl border border-neutral-800/80 p-5 backdrop-blur-md transition-all hover:border-neutral-700/80 group">
        <div className="flex items-center justify-between pb-3 border-b border-neutral-800/60 mb-4">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-300">
              <Activity className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-xs font-bold tracking-wider uppercase text-white font-mono flex items-center gap-2">
                Balanço Diário Sniper
                <span className="text-[10px] text-neutral-500 font-sans font-normal">
                  {summary?.todayDate || 'Hoje'}
                </span>
              </h3>
              <p className="text-[11px] text-neutral-400">Consolidado das operações de trading intradiário</p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setIsModalOpen(true)}
            className="flex items-center gap-1 text-[11px] font-mono text-neutral-400 hover:text-white px-2 py-1 rounded-md bg-neutral-800/50 hover:bg-neutral-800 border border-neutral-700/50 transition-colors"
            title="Abrir relatório detalhado com ordens fechadas"
          >
            <Maximize2 className="w-3 h-3" />
            <span className="hidden sm:inline">Relatório</span>
          </button>
        </div>

        {/* METRICAS PRINCIPAIS */}
        <div className="grid grid-cols-3 gap-3">
          {/* NET USDT */}
          <div className="p-3 rounded-xl bg-neutral-950/70 border border-neutral-800/80">
            <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-400 mb-1 flex items-center gap-1">
              Líquido USDT
              <TrendingUp className="w-3 h-3 text-neutral-400" />
            </p>
            <p
              className={`text-base font-mono font-bold ${
                netUsdt > 0 ? 'text-emerald-400' : netUsdt < 0 ? 'text-rose-400' : 'text-neutral-300'
              }`}
            >
              {netUsdt > 0 ? '+' : ''}
              {formatUsdt(netUsdt)}
            </p>
            <p className="text-[10px] text-neutral-500 mt-1">
              Vol: {formatUsdt(summary?.totalVolumeUsdt ?? 0)}
            </p>
          </div>

          {/* NET BRL */}
          <div className="p-3 rounded-xl bg-neutral-950/70 border border-neutral-800/80">
            <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-400 mb-1 flex items-center gap-1">
              Líquido BRL
              <TrendingUp className="w-3 h-3 text-neutral-400" />
            </p>
            <p
              className={`text-base font-mono font-bold ${
                netBrl > 0 ? 'text-emerald-400' : netBrl < 0 ? 'text-rose-400' : 'text-neutral-300'
              }`}
            >
              {netBrl > 0 ? '+' : ''}
              {formatBrl(netBrl)}
            </p>
            <p className="text-[10px] text-neutral-500 mt-1">
              Vol: {formatBrl(summary?.totalVolumeBrl ?? 0)}
            </p>
          </div>

          {/* WIN RATE */}
          <div className="p-3 rounded-xl bg-neutral-950/70 border border-neutral-800/80">
            <div className="flex items-center justify-between mb-1">
              <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-400">Win Rate</p>
              <span className="text-[10px] font-mono font-bold text-white">{winRate.toFixed(1)}%</span>
            </div>
            {/* Barra de Progresso */}
            <div className="w-full h-1.5 bg-neutral-800 rounded-full overflow-hidden mb-1.5">
              <div
                className="h-full bg-neutral-300 rounded-full transition-all duration-500"
                style={{ width: `${Math.min(100, Math.max(0, winRate))}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-[10px] font-mono text-neutral-400">
              <span className="text-emerald-400 font-bold">{wins}W</span>
              <span className="text-rose-400 font-bold">{losses}L</span>
              <span className="text-neutral-500">{breakeven}E</span>
              <span className="text-neutral-500">({totalClosed} tot)</span>
            </div>
          </div>
        </div>
      </section>

      {/* MODAL DE RELATÓRIO COMPLETO */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-neutral-950 border border-neutral-800 rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden">
            {/* Header Modal */}
            <div className="p-5 border-b border-neutral-800 flex items-center justify-between bg-neutral-900/50">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-300">
                  <Activity className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white font-mono flex items-center gap-2">
                    Relatório Detalhado de Trades
                    <span className="text-xs text-neutral-400 font-sans font-normal">
                      ({summary?.todayDate || 'Hoje'})
                    </span>
                  </h3>
                  <p className="text-xs text-neutral-400">
                    Histórico consolidado de entradas e saídas intradiárias
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Sub-Header com Resumo */}
            <div className="grid grid-cols-3 gap-3 p-4 bg-neutral-900/30 border-b border-neutral-800/80 text-xs font-mono">
              <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800/60">
                <p className="text-neutral-500 text-[10px] uppercase">Líquido USDT</p>
                <p className={`font-bold text-sm ${netUsdt >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {netUsdt >= 0 ? '+' : ''}
                  {formatUsdt(netUsdt)}
                </p>
              </div>
              <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800/60">
                <p className="text-neutral-500 text-[10px] uppercase">Líquido BRL</p>
                <p className={`font-bold text-sm ${netBrl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {netBrl >= 0 ? '+' : ''}
                  {formatBrl(netBrl)}
                </p>
              </div>
              <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800/60">
                <p className="text-neutral-500 text-[10px] uppercase">Assertividade</p>
                <p className="font-bold text-sm text-white">
                  {winRate.toFixed(1)}% <span className="text-xs font-normal text-neutral-400">({wins}W / {losses}L)</span>
                </p>
              </div>
            </div>

            {/* Abas de Filtro */}
            <div className="flex gap-2 p-3 border-b border-neutral-800 text-xs font-mono bg-neutral-950">
              <button
                type="button"
                onClick={() => setModalTab('all')}
                className={`px-3 py-1.5 rounded-lg transition-all ${
                  modalTab === 'all'
                    ? 'bg-neutral-800 text-white font-bold border border-neutral-700'
                    : 'text-neutral-400 hover:text-white'
                }`}
              >
                Todas ({trades.length})
              </button>
              <button
                type="button"
                onClick={() => setModalTab('wins')}
                className={`px-3 py-1.5 rounded-lg transition-all ${
                  modalTab === 'wins'
                    ? 'bg-emerald-950/60 text-emerald-300 font-bold border border-emerald-800/60'
                    : 'text-neutral-400 hover:text-white'
                }`}
              >
                Lucros ({trades.filter((t) => (t.net_pnl_fiat ?? 0) > 0).length})
              </button>
              <button
                type="button"
                onClick={() => setModalTab('losses')}
                className={`px-3 py-1.5 rounded-lg transition-all ${
                  modalTab === 'losses'
                    ? 'bg-rose-950/60 text-rose-300 font-bold border border-rose-800/60'
                    : 'text-neutral-400 hover:text-white'
                }`}
              >
                Prejuízos ({trades.filter((t) => (t.net_pnl_fiat ?? 0) < 0).length})
              </button>
            </div>

            {/* Lista de Ordens */}
            <div className="p-4 overflow-y-auto space-y-2.5 flex-1 custom-scrollbar">
              {filteredModalTrades.length === 0 ? (
                <div className="py-12 text-center text-neutral-500 text-xs font-mono">
                  Nenhum registro encontrado para este filtro hoje.
                </div>
              ) : (
                filteredModalTrades.map((t, idx) => {
                  const isBuy = t.action === 'BUY'
                  const isWin = (t.net_pnl_fiat ?? 0) > 0
                  const isLoss = (t.net_pnl_fiat ?? 0) < 0
                  const timeFormatted = t.timestamp
                    ? new Date(t.timestamp).toLocaleTimeString('pt-BR')
                    : '--:--'

                  return (
                    <div
                      key={idx}
                      className="p-3 rounded-xl bg-neutral-900/60 border border-neutral-800 hover:border-neutral-700 transition-all flex items-center justify-between text-xs font-mono"
                    >
                      <div className="flex items-center gap-3">
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold border bg-neutral-800 text-neutral-300 border-neutral-700">
                          {isBuy ? 'COMPRA' : 'VENDA'}
                        </span>
                        <div>
                          <p className="font-bold text-white flex items-center gap-1.5">
                            {t.symbol}
                            <span className="text-[10px] font-normal text-neutral-500 flex items-center gap-1">
                              <Clock className="w-2.5 h-2.5" />
                              {timeFormatted}
                            </span>
                          </p>
                          {t.reason && (
                            <p className="text-[10px] text-neutral-400 line-clamp-1 max-w-xs">{t.reason}</p>
                          )}
                        </div>
                      </div>

                      <div className="text-right">
                        <p className="text-neutral-300">
                          Preço: {t.currency === 'USDT' ? `$ ${t.price?.toFixed(4)}` : `R$ ${t.price?.toFixed(2)}`}
                        </p>
                        {t.net_pnl_fiat !== undefined && !isBuy && (
                          <div className="flex items-center justify-end gap-1.5 mt-0.5">
                            <span
                              className={`font-bold ${
                                isWin ? 'text-emerald-400' : isLoss ? 'text-rose-400' : 'text-neutral-400'
                              }`}
                            >
                              {isWin ? '+' : ''}
                              {t.currency === 'USDT'
                                ? `$ ${t.net_pnl_fiat.toFixed(2)} USDT`
                                : `R$ ${t.net_pnl_fiat.toFixed(2)}`}
                            </span>
                            {t.pnl_pct !== undefined && (
                              <span
                                className={`text-[10px] px-1 rounded ${
                                  isWin ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'
                                }`}
                              >
                                {t.pnl_pct > 0 ? '+' : ''}
                                {t.pnl_pct.toFixed(2)}%
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* Rodapé do Modal */}
            <div className="p-4 border-t border-neutral-800 bg-neutral-900/40 flex justify-end">
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="px-4 py-2 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-white text-xs font-mono transition-colors"
              >
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

'use client'

import React, { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'

interface Snapshot {
  timestamp?: string
  seconds_elapsed?: number
  symbol?: string
  current_price?: number
  entry_price?: number
  in_position?: boolean
  unrealized_pnl_pct?: number
  rsi?: number
}

interface DaytradeChartProps {
  snapshots: Snapshot[]
  entryPrice?: number | null
  inPosition?: boolean
  currency?: string
}

export default function DaytradeChart({
  snapshots,
  entryPrice,
  inPosition,
  currency = 'BRL',
}: DaytradeChartProps) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return (
      <div className="h-64 w-full flex items-center justify-center bg-neutral-950/40 rounded-xl border border-neutral-800">
        <div className="w-8 h-8 rounded-full border-2 border-rose-500 border-t-transparent animate-spin"></div>
      </div>
    )
  }

  if (!snapshots || snapshots.length === 0) {
    return (
      <div className="h-64 w-full flex flex-col items-center justify-center bg-neutral-950/40 rounded-xl border border-neutral-800 text-neutral-500 text-xs gap-2">
        <span className="text-xl">📈</span>
        <span>Aguardando os primeiros snapshots de 30s da sessão...</span>
      </div>
    )
  }

  // Ordena cronologicamente do mais antigo para o mais recente
  const sortedSnapshots = [...snapshots].sort((a, b) => {
    const timeA = a.timestamp ? new Date(a.timestamp).getTime() : (a.seconds_elapsed || 0)
    const timeB = b.timestamp ? new Date(b.timestamp).getTime() : (b.seconds_elapsed || 0)
    return timeA - timeB
  })

  // Identifica preço de entrada prioritário
  const activeEntryPrice =
    entryPrice || sortedSnapshots.find((s) => s.in_position && s.entry_price)?.entry_price || null

  const latestPrice = sortedSnapshots[sortedSnapshots.length - 1]?.current_price || 0
  const isPos = inPosition || sortedSnapshots[sortedSnapshots.length - 1]?.in_position

  // Regra do Usuário: Se estiver em loss fica vermelha, e lucro verde
  let chartColor = '#38bdf8' // Padrão neutro / ciano quando não posicionado
  let isLoss = false
  if (isPos && activeEntryPrice && latestPrice > 0) {
    if (latestPrice >= activeEntryPrice) {
      chartColor = '#10b981' // Verde para Lucro (Emerald)
      isLoss = false
    } else {
      chartColor = '#f43f5e' // Vermelho para Loss (Rose)
      isLoss = true
    }
  }

  // Formatação dos pontos do gráfico
  const chartData = sortedSnapshots.map((s, idx) => {
    let timeStr = `T+${idx * 30}s`
    if (s.timestamp) {
      const d = new Date(s.timestamp)
      if (!isNaN(d.getTime())) {
        timeStr = d.toLocaleTimeString('pt-BR', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      }
    }

    return {
      time: timeStr,
      price: s.current_price,
      entryPrice: activeEntryPrice,
      inPosition: s.in_position,
      pnlPct: s.unrealized_pnl_pct,
      rsi: s.rsi,
    }
  })

  // Cálculo do domínio dinâmico do eixo Y para destacar pequenas oscilações
  const allPrices = sortedSnapshots.map((s) => s.current_price || 0).filter((p) => p > 0)
  if (activeEntryPrice) allPrices.push(activeEntryPrice)
  const minPrice = allPrices.length > 0 ? Math.min(...allPrices) : 0
  const maxPrice = allPrices.length > 0 ? Math.max(...allPrices) : 0
  const padding = (maxPrice - minPrice) * 0.15 || minPrice * 0.001
  const yDomain = minPrice > 1
    ? [Math.floor(minPrice - padding), Math.ceil(maxPrice + padding)]
    : [Math.max(0, minPrice - padding), maxPrice + padding]

  const formatCurrency = (val: number) => {
    if (!val || isNaN(val)) return currency === 'USDT' ? '$ 0,00' : 'R$ 0,00'
    const decimals = val < 0.001 ? 8 : val < 1 ? 4 : 2
    if (currency === 'USDT' || currency === 'USD') {
      return `$ ${val.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: decimals })} USDT`
    }
    return val.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: decimals })
  }

  const activeSymbol = sortedSnapshots[sortedSnapshots.length - 1]?.symbol || 'ALT/USDT'

  return (
    <div className="space-y-3">
      {/* Top Bar do Gráfico com Legendas e Status */}
      <div className="flex justify-between items-center flex-wrap gap-2 px-1">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: chartColor }}
            ></span>
            <span className="text-xs font-mono font-bold text-white">
              {activeSymbol}: {formatCurrency(latestPrice)}
            </span>
          </div>

          {activeEntryPrice && (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-950/40 border border-amber-500/40 text-xs font-mono text-amber-300">
              <span className="w-2 h-0.5 bg-amber-400"></span>
              <span>Linha de Compra: {formatCurrency(activeEntryPrice)}</span>
            </div>
          )}
        </div>

        <div>
          {isPos ? (
            <span
              className={`text-xs font-mono font-extrabold px-2.5 py-1 rounded-md border ${
                !isLoss
                  ? 'bg-emerald-950/80 text-emerald-400 border-emerald-500/50 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                  : 'bg-rose-950/80 text-rose-400 border-rose-500/50 shadow-[0_0_10px_rgba(244,63,94,0.2)] animate-pulse'
              }`}
            >
              {!isLoss ? '🟢 EM LUCRO' : '🔴 EM LOSS'} (
              {activeEntryPrice
                ? `${(((latestPrice - activeEntryPrice) / activeEntryPrice) * 100).toFixed(2)}%`
                : '0.00%'}
              )
            </span>
          ) : (
            <span className="text-xs font-mono text-neutral-400 bg-neutral-900 px-2 py-0.5 rounded border border-neutral-800">
              Aguardando Posição
            </span>
          )}
        </div>
      </div>

      {/* Gráfico de Linha Recharts */}
      <div className="w-full h-64 bg-neutral-950/80 rounded-xl border border-neutral-800/80 p-3 pt-4">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={chartColor} stopOpacity={0.25} />
                <stop offset="95%" stopColor={chartColor} stopOpacity={0.0} />
              </linearGradient>
            </defs>

            <XAxis
              dataKey="time"
              stroke="#525252"
              tick={{ fill: '#737373', fontSize: 10, fontFamily: 'monospace' }}
              tickLine={false}
            />
            <YAxis
              domain={yDomain}
              stroke="#525252"
              tick={{ fill: '#737373', fontSize: 10, fontFamily: 'monospace' }}
              tickLine={false}
              tickFormatter={(v) => Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 0 })}
              width={70}
            />

            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload || !payload.length) return null
                const data = payload[0].payload
                const pnl = data.pnlPct ?? (activeEntryPrice ? ((data.price - activeEntryPrice) / activeEntryPrice) * 100 : 0)
                const isItemProfit = pnl >= 0
                return (
                  <div className="p-3 bg-neutral-900/95 border border-neutral-700 rounded-lg shadow-xl text-xs font-mono space-y-1.5 backdrop-blur-sm">
                    <p className="text-neutral-400 font-semibold">{data.time}</p>
                    <p className="text-white font-bold text-sm">
                      Preço: {formatCurrency(data.price)}
                    </p>
                    {activeEntryPrice && (
                      <p className="text-amber-400">
                        Entrada: {formatCurrency(activeEntryPrice)}
                      </p>
                    )}
                    {data.inPosition && (
                      <p className={`font-bold ${isItemProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
                        PnL: {isItemProfit ? '+' : ''}{pnl.toFixed(2)}%
                      </p>
                    )}
                    {data.rsi && (
                      <p className="text-neutral-400">
                        RSI-7: <strong className="text-neutral-200">{data.rsi.toFixed(1)}</strong>
                      </p>
                    )}
                  </div>
                )
              }}
            />

            {/* Linha de Compra (Referência) */}
            {activeEntryPrice && (
              <ReferenceLine
                y={activeEntryPrice}
                stroke="#f59e0b"
                strokeDasharray="4 4"
                strokeWidth={1.5}
                label={{
                  value: 'COMPRA',
                  fill: '#f59e0b',
                  fontSize: 10,
                  position: 'insideTopRight',
                }}
              />
            )}

            {/* Área Sombreada */}
            <Area
              type="monotone"
              dataKey="price"
              stroke="none"
              fill="url(#colorPrice)"
            />

            {/* Linha Principal de Cotação (Verde em Lucro, Vermelha em Loss) */}
            <Line
              type="monotone"
              dataKey="price"
              stroke={chartColor}
              strokeWidth={2.5}
              dot={{ r: 2.5, fill: chartColor }}
              activeDot={{ r: 5, stroke: '#fff', strokeWidth: 1.5, fill: chartColor }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

'use client'

import React, { useState, useEffect, useMemo } from 'react'
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

export interface PositionData {
  symbol: string
  current_price: number
  entry_price: number
  breakeven_price?: number
  target_price?: number
  pnl_pct?: number
  is_above_target?: boolean
}

export interface Snapshot {
  timestamp?: string
  seconds_elapsed?: number
  symbol?: string
  current_price?: number
  entry_price?: number
  breakeven_price?: number
  target_price?: number
  in_position?: boolean
  unrealized_pnl_pct?: number
  rsi?: number
  positions_data?: Record<string, PositionData>
}

interface DaytradeChartProps {
  snapshots: Snapshot[]
  entryPrice?: number | null
  inPosition?: boolean
  currency?: string
}

export default function DaytradeChart({
  snapshots,
  entryPrice: propEntryPrice,
  inPosition,
  currency = 'USDT',
}: DaytradeChartProps) {
  const [mounted, setMounted] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  useEffect(() => {
    setMounted(true)
  }, [])

  // Ordena cronologicamente do mais antigo para o mais recente
  const sortedSnapshots = useMemo(() => {
    if (!snapshots || snapshots.length === 0) return []
    return [...snapshots].sort((a, b) => {
      const timeA = a.timestamp ? new Date(a.timestamp).getTime() : (a.seconds_elapsed || 0)
      const timeB = b.timestamp ? new Date(b.timestamp).getTime() : (b.seconds_elapsed || 0)
      return timeA - timeB
    })
  }, [snapshots])

  // Identifica todos os símbolos únicos presentes nos snapshots
  const availableSymbols = useMemo(() => {
    const syms = new Set<string>()
    sortedSnapshots.forEach((s) => {
      if (s.positions_data) {
        Object.keys(s.positions_data).forEach((sym) => syms.add(sym))
      }
      if (s.symbol && s.symbol !== 'MULTI') {
        syms.add(s.symbol)
      }
    })
    return Array.from(syms)
  }, [sortedSnapshots])

  // Ativo atualmente selecionado para inspeção
  const activeSymbol = selectedSymbol && availableSymbols.includes(selectedSymbol)
    ? selectedSymbol
    : (availableSymbols[0] || sortedSnapshots[sortedSnapshots.length - 1]?.symbol || 'ALT/USDT')

  // Extrai preços e linhas de referência determinísticas para o ativo selecionado
  const { activeEntryPrice, activeBreakevenPrice, activeTargetPrice, latestPrice } = useMemo(() => {
    let entryP: number | null = propEntryPrice || null
    let latestP = 0

    for (let i = sortedSnapshots.length - 1; i >= 0; i--) {
      const s = sortedSnapshots[i]
      if (s.positions_data && s.positions_data[activeSymbol]) {
        const pData = s.positions_data[activeSymbol]
        if (!latestP) latestP = pData.current_price
        if (!entryP) entryP = pData.entry_price
      } else if (s.symbol === activeSymbol) {
        if (!latestP) latestP = s.current_price || 0
        if (!entryP && s.entry_price) entryP = s.entry_price
      }
    }

    if (!latestP && sortedSnapshots.length > 0) {
      latestP = sortedSnapshots[sortedSnapshots.length - 1]?.current_price || 0
    }

    // Cálculos estritamente determinísticos de taxas da Binance Spot
    // Taxa: 0.1% compra + 0.1% venda = 0.2002% total round-trip
    const breakevenP = entryP ? Number((entryP * 1.002002).toFixed(8)) : null
    // Meta de Lucro Real: +0.70% bruto = +0.50% líquido garantido no bolso
    const targetP = entryP ? Number((entryP * 1.0070).toFixed(8)) : null

    return {
      activeEntryPrice: entryP,
      activeBreakevenPrice: breakevenP,
      activeTargetPrice: targetP,
      latestPrice: latestP,
    }
  }, [sortedSnapshots, activeSymbol, propEntryPrice])

  if (!mounted) {
    return (
      <div className="h-72 w-full flex items-center justify-center bg-neutral-950/40 rounded-xl border border-neutral-800">
        <div className="w-8 h-8 rounded-full border-2 border-rose-500 border-t-transparent animate-spin"></div>
      </div>
    )
  }

  if (!snapshots || snapshots.length === 0) {
    return (
      <div className="h-72 w-full flex flex-col items-center justify-center bg-neutral-950/40 rounded-xl border border-neutral-800 text-neutral-500 text-xs gap-2">
        <span className="text-xl">📈</span>
        <span>Aguardando os primeiros snapshots de 30s da sessão...</span>
      </div>
    )
  }

  // DIRETRIZ DO INVESTIDOR:
  // "estar em loss ou lucro e a linha ser vermelha ou verde tbm deve se basear na linha de meta da posição."
  const isPos = inPosition || sortedSnapshots[sortedSnapshots.length - 1]?.in_position
  const isAboveTarget = activeTargetPrice ? latestPrice >= activeTargetPrice : false

  let chartColor = '#38bdf8' // Padrão neutro quando não posicionado
  if (isPos && activeTargetPrice) {
    chartColor = isAboveTarget ? '#10b981' : '#f43f5e'
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

    let p = s.current_price || 0
    let pnl = s.unrealized_pnl_pct || 0

    if (s.positions_data && s.positions_data[activeSymbol]) {
      p = s.positions_data[activeSymbol].current_price
      pnl = s.positions_data[activeSymbol].pnl_pct || 0
    }

    return {
      time: timeStr,
      price: p,
      entryPrice: activeEntryPrice,
      breakevenPrice: activeBreakevenPrice,
      targetPrice: activeTargetPrice,
      pnlPct: pnl,
      rsi: s.rsi,
    }
  })

  // Domínio dinâmico do eixo Y considerando entrada, taxas, meta e preços
  const allPrices = chartData.map((d) => d.price).filter((p) => p && p > 0)
  if (activeEntryPrice) allPrices.push(activeEntryPrice)
  if (activeBreakevenPrice) allPrices.push(activeBreakevenPrice)
  if (activeTargetPrice) allPrices.push(activeTargetPrice)

  const minPrice = allPrices.length > 0 ? Math.min(...allPrices) : 0
  const maxPrice = allPrices.length > 0 ? Math.max(...allPrices) : 0
  const padding = (maxPrice - minPrice) * 0.20 || minPrice * 0.001
  const yDomain = minPrice > 1
    ? [Math.floor(minPrice - padding), Math.ceil(maxPrice + padding)]
    : [Math.max(0, minPrice - padding), maxPrice + padding]

  const formatCurrency = (val?: number | null) => {
    if (!val || isNaN(val)) return currency === 'USDT' ? '$ 0,00' : 'R$ 0,00'
    const decimals = Math.abs(val) < 0.001 ? 8 : Math.abs(val) < 1 ? 4 : 2
    if (currency === 'USDT' || currency === 'USD') {
      return `$ ${val.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: decimals })} USDT`
    }
    return val.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: decimals })
  }

  return (
    <div className="space-y-3">
      {/* Top Bar do Gráfico com Filtro na Legenda e Status baseado na Meta */}
      <div className="flex justify-between items-center flex-wrap gap-2 px-1">
        <div className="flex items-center gap-2 flex-wrap">
          {/* FILTRO DE POSIÇÕES NA LEGENDA */}
          {availableSymbols.length > 1 && (
            <div className="flex rounded-lg border border-neutral-800 bg-neutral-900 p-0.5 text-xs font-mono mr-2">
              {availableSymbols.map((sym) => {
                const isSel = sym === activeSymbol
                return (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => setSelectedSymbol(sym)}
                    className={`px-2.5 py-1 rounded-md font-bold transition-all flex items-center gap-1.5 ${
                      isSel
                        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-sm'
                        : 'text-neutral-400 hover:text-white'
                    }`}
                  >
                    <span
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: isSel ? chartColor : '#737373' }}
                    ></span>
                    <span>{sym}</span>
                  </button>
                )
              })}
            </div>
          )}

          {/* Cotação Atual */}
          <div className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: chartColor }}
            ></span>
            <span className="text-xs font-mono font-bold text-white">
              {activeSymbol}: {formatCurrency(latestPrice)}
            </span>
          </div>
        </div>

        {/* Status de Lucro Baseado Estritamente na Linha de Meta */}
        <div>
          {isPos ? (
            <span
              className={`text-xs font-mono font-extrabold px-3 py-1 rounded-md border flex items-center gap-1.5 ${
                isAboveTarget
                  ? 'bg-emerald-950/90 text-emerald-400 border-emerald-500/50 shadow-[0_0_15px_rgba(16,185,129,0.3)]'
                  : 'bg-rose-950/90 text-rose-400 border-rose-500/50 shadow-[0_0_15px_rgba(244,63,94,0.2)] animate-pulse'
              }`}
            >
              <span>{isAboveTarget ? '🟢 META ATINGIDA' : '🔴 ABAIXO DA META'}</span>
              <span>
                (
                {activeTargetPrice && latestPrice
                  ? `${(((latestPrice - activeTargetPrice) / activeTargetPrice) * 100).toFixed(2)}% vs Meta`
                  : '0.00%'}
                )
              </span>
            </span>
          ) : (
            <span className="text-xs font-mono text-neutral-400 bg-neutral-900 px-2 py-0.5 rounded border border-neutral-800">
              Aguardando Posição
            </span>
          )}
        </div>
      </div>

      {/* LEGENDA DETERMINÍSTICA DAS TRÊS LINHAS */}
      <div className="flex items-center gap-3 px-2 py-1.5 rounded-lg bg-neutral-950/60 border border-neutral-850 text-[11px] font-mono flex-wrap">
        {/* Linha de Compra */}
        {activeEntryPrice && (
          <div className="flex items-center gap-1.5 text-amber-400">
            <span className="w-2.5 h-0.5 bg-amber-400"></span>
            <span>Linha de Compra: <strong>{formatCurrency(activeEntryPrice)}</strong></span>
          </div>
        )}

        {/* Linha de Taxas (Breakeven 0.20%) */}
        {activeBreakevenPrice && (
          <div className="flex items-center gap-1.5 text-neutral-300">
            <span className="w-2.5 h-0.5 bg-neutral-300 border-dashed border-t"></span>
            <span>Linha de Taxas (+0.20%): <strong>{formatCurrency(activeBreakevenPrice)}</strong></span>
          </div>
        )}

        {/* Linha de Meta (Lucro Real) */}
        {activeTargetPrice && (
          <div className="flex items-center gap-1.5 text-emerald-400">
            <span className="w-2.5 h-0.5 bg-emerald-400 border-dashed border-t-2"></span>
            <span>Linha de Meta (+0.70%): <strong>{formatCurrency(activeTargetPrice)}</strong></span>
          </div>
        )}
      </div>

      {/* Gráfico de Linha Recharts com Três Linhas de Referência */}
      <div className="w-full h-64 bg-neutral-950/80 rounded-xl border border-neutral-800/80 p-3 pt-4">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 10, right: 25, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="colorPriceDynamic" x1="0" y1="0" x2="0" y2="1">
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
              tickFormatter={(v) => {
                const n = Number(v)
                return n < 0.001 ? n.toFixed(6) : n < 1 ? n.toFixed(3) : n.toLocaleString('pt-BR', { maximumFractionDigits: 1 })
              }}
              width={75}
            />

            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload || !payload.length) return null
                const data = payload[0].payload
                const distToTarget = activeTargetPrice && data.price
                  ? ((data.price - activeTargetPrice) / activeTargetPrice) * 100
                  : 0
                const isTargetAchieved = distToTarget >= 0

                return (
                  <div className="p-3 bg-neutral-900/95 border border-neutral-700 rounded-lg shadow-xl text-xs font-mono space-y-1.5 backdrop-blur-sm">
                    <p className="text-neutral-400 font-semibold">{data.time}</p>
                    <p className="text-white font-bold text-sm">
                      {activeSymbol}: {formatCurrency(data.price)}
                    </p>
                    {activeEntryPrice && (
                      <p className="text-amber-400 text-[11px]">
                        Compra: {formatCurrency(activeEntryPrice)}
                      </p>
                    )}
                    {activeBreakevenPrice && (
                      <p className="text-neutral-300 text-[11px]">
                        Taxas (Breakeven): {formatCurrency(activeBreakevenPrice)}
                      </p>
                    )}
                    {activeTargetPrice && (
                      <p className="text-emerald-400 font-bold text-[11px]">
                        Meta (Lucro Real): {formatCurrency(activeTargetPrice)}
                      </p>
                    )}
                    <div className="pt-1 border-t border-neutral-800">
                      <p
                        className={`font-bold ${
                          isTargetAchieved ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        Status: {isTargetAchieved ? '🟢 Meta Atingida' : '🔴 Abaixo da Meta'} ({distToTarget >= 0 ? '+' : ''}{distToTarget.toFixed(2)}%)
                      </p>
                    </div>
                  </div>
                )
              }}
            />

            {/* 1. LINHA DE COMPRA (Amarela tracejada) */}
            {activeEntryPrice && (
              <ReferenceLine
                y={activeEntryPrice}
                stroke="#f59e0b"
                strokeDasharray="3 3"
                strokeWidth={1.5}
                label={{
                  value: 'COMPRA',
                  fill: '#f59e0b',
                  fontSize: 9,
                  position: 'insideTopRight',
                }}
              />
            )}

            {/* 2. LINHA DE TAXAS (Breakeven 0.20% - Branca/Cinza tracejada) */}
            {activeBreakevenPrice && (
              <ReferenceLine
                y={activeBreakevenPrice}
                stroke="#a3a3a3"
                strokeDasharray="4 4"
                strokeWidth={1.5}
                label={{
                  value: 'TAXAS (BREAKEVEN)',
                  fill: '#a3a3a3',
                  fontSize: 9,
                  position: 'insideTopRight',
                }}
              />
            )}

            {/* 3. LINHA DE META (Lucro Real - Verde tracejada) */}
            {activeTargetPrice && (
              <ReferenceLine
                y={activeTargetPrice}
                stroke="#10b981"
                strokeDasharray="5 3"
                strokeWidth={2}
                label={{
                  value: 'META (LUCRO REAL)',
                  fill: '#10b981',
                  fontSize: 9,
                  position: 'insideTopRight',
                }}
              />
            )}

            {/* Área Sombreada com a cor da Meta */}
            <Area
              type="monotone"
              dataKey="price"
              stroke="none"
              fill="url(#colorPriceDynamic)"
            />

            {/* LINHA PRINCIPAL: Verde na/acima da Meta, Vermelha abaixo da Meta */}
            <Line
              type="monotone"
              dataKey="price"
              stroke={chartColor}
              strokeWidth={2.5}
              dot={{ r: 2, fill: chartColor }}
              activeDot={{ r: 5, stroke: '#fff', strokeWidth: 1.5, fill: chartColor }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

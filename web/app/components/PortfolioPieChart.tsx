'use client'

import React, { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'

const COLORS = ['#10b981', '#3b82f6', '#8b5cf6', '#f59e0b', '#ec4899', '#06b6d4', '#84cc16']

export default function PortfolioPieChart({ data }: { data: any }) {
  const [mounted, setMounted] = useState(false)

  // Previne erros de hydration no Next.js com recharts
  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) return <div className="h-48 w-full flex items-center justify-center"><div className="w-8 h-8 rounded-full border-2 border-emerald-500 border-t-transparent animate-spin"></div></div>

  // Transforma o objeto de posições do Redis num array pro Recharts
  const chartData = Object.entries(data).map(([symbol, info]: [string, any]) => ({
    name: symbol,
    value: info.total_invested
  })).sort((a, b) => b.value - a.value)

  if (chartData.length === 0) {
    return (
      <div className="h-48 w-full flex items-center justify-center text-neutral-500 italic text-sm">
        Nenhum dado para exibir no gráfico.
      </div>
    )
  }

  const formatCurrency = (val: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(val)

  return (
    <div className="w-full h-56 relative -mt-4 mb-4">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={75}
            paddingAngle={5}
            dataKey="value"
            stroke="none"
          >
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip 
            formatter={(value: any) => formatCurrency(Number(value))}
            contentStyle={{ backgroundColor: '#171717', border: '1px solid #262626', borderRadius: '8px' }}
            itemStyle={{ color: '#e5e5e5' }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 text-center pointer-events-none">
        <p className="text-[10px] text-neutral-500 uppercase tracking-widest">Ativos</p>
        <p className="text-xl font-bold text-white">{chartData.length}</p>
      </div>
    </div>
  )
}

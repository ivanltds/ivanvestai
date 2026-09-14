'use client'

import React, { useState, useEffect, useRef } from 'react'
import {
  Bot,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
  Search,
  Shield,
  Award,
  MessageSquare,
  Target,
  Terminal,
} from 'lucide-react'

export interface ChatMessage {
  id?: string
  timestamp?: string
  elapsed_str?: string
  type?: string
  symbol?: string
  tag?: string
  message?: string
  sender?: string
}

interface SniperChatFeedProps {
  messages: ChatMessage[]
  isRunning?: boolean
}

export default function SniperChatFeed({ messages, isRunning }: SniperChatFeedProps) {
  const [filter, setFilter] = useState<'all' | 'decisions' | 'holds'>('all')
  const containerRef = useRef<HTMLDivElement>(null)
  const prevCountRef = useRef<number>(0)

  useEffect(() => {
    // Rola ESTRITAMENTE o container interno do chat sem jamais afetar o scroll da janela/página
    if (containerRef.current && messages.length > prevCountRef.current) {
      const el = containerRef.current
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
      if (isNearBottom || prevCountRef.current === 0) {
        el.scrollTop = el.scrollHeight
      }
    }
    prevCountRef.current = messages.length
  }, [messages])

  const filteredMessages = messages.filter((m) => {
    if (filter === 'decisions') {
      return ['COMPRA', 'VENDA', 'RESULTADO'].includes(m.tag || '')
    }
    if (filter === 'holds') {
      return ['MANTER', 'PROTEÇÃO'].includes(m.tag || '')
    }
    return true
  })

  const getBadgeStyle = (tag?: string) => {
    switch (tag) {
      case 'COMPRA':
        return 'bg-emerald-950/70 text-emerald-400 border-emerald-700/50'
      case 'VENDA':
        return 'bg-rose-950/70 text-rose-300 border-rose-700/50'
      case 'MANTER':
        return 'bg-sky-950/70 text-sky-300 border-sky-700/50'
      case 'SCANNER':
        return 'bg-purple-950/70 text-purple-300 border-purple-700/50'
      case 'PROTEÇÃO':
        return 'bg-amber-950/70 text-amber-300 border-amber-700/50'
      case 'RESULTADO':
        return 'bg-emerald-950/90 text-emerald-200 border-emerald-500/60 font-bold'
      default:
        return 'bg-neutral-800 text-neutral-300 border-neutral-700'
    }
  }

  const renderIcon = (tag?: string) => {
    switch (tag) {
      case 'COMPRA':
        return <ArrowUpRight className="w-3 h-3 text-emerald-400" />
      case 'VENDA':
        return <ArrowDownRight className="w-3 h-3 text-rose-400" />
      case 'MANTER':
        return <Clock className="w-3 h-3 text-sky-400" />
      case 'SCANNER':
        return <Search className="w-3 h-3 text-purple-400" />
      case 'PROTEÇÃO':
        return <Shield className="w-3 h-3 text-amber-400" />
      case 'RESULTADO':
        return <Award className="w-3 h-3 text-emerald-400" />
      default:
        return <MessageSquare className="w-3 h-3 text-neutral-400" />
    }
  }

  return (
    <div className="bg-neutral-950/70 border border-neutral-800/80 rounded-xl p-4 flex flex-col h-80">
      {/* Header do Chat */}
      <div className="flex justify-between items-center pb-3 border-b border-neutral-800/80 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-neutral-900 border border-neutral-800 text-purple-400">
            <Bot className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-xs font-bold text-white font-mono flex items-center gap-2">
              Feed &amp; Decisões da IA
              {isRunning && (
                <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-sans font-normal">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping"></span>
                  ao vivo
                </span>
              )}
            </h4>
            <p className="text-[10px] text-neutral-500">
              Observações e justificativas em tempo real
            </p>
          </div>
        </div>

        {/* Filtros de Mensagens */}
        <div className="flex gap-1 bg-neutral-900 p-0.5 rounded-lg border border-neutral-800 text-[11px] font-mono">
          <button
            type="button"
            onClick={() => setFilter('all')}
            className={`px-2 py-0.5 rounded transition-all ${
              filter === 'all' ? 'bg-neutral-800 text-white font-bold' : 'text-neutral-500 hover:text-neutral-300'
            }`}
          >
            Todas ({messages.length})
          </button>
          <button
            type="button"
            onClick={() => setFilter('decisions')}
            className={`px-2 py-0.5 rounded transition-all ${
              filter === 'decisions' ? 'bg-neutral-800 text-rose-400 font-bold' : 'text-neutral-500 hover:text-neutral-300'
            }`}
          >
            Trades
          </button>
          <button
            type="button"
            onClick={() => setFilter('holds')}
            className={`px-2 py-0.5 rounded transition-all ${
              filter === 'holds' ? 'bg-neutral-800 text-sky-400 font-bold' : 'text-neutral-500 hover:text-neutral-300'
            }`}
          >
            Manutenção
          </button>
        </div>
      </div>

      {/* Área de Rolagem das Mensagens */}
      <div ref={containerRef} className="flex-1 overflow-y-auto py-3 space-y-2.5 custom-scrollbar pr-1">
        {filteredMessages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-neutral-500 text-xs gap-2">
            <Target className="w-6 h-6 text-neutral-600" />
            <span>Aguardando os primeiros pensamentos do robô na sessão...</span>
          </div>
        ) : (
          filteredMessages.map((msg, idx) => {
            const isOutcome = msg.tag === 'RESULTADO'
            return (
              <div
                key={msg.id || idx}
                className={`p-2.5 rounded-xl border text-xs font-mono transition-all ${
                  isOutcome
                    ? 'bg-gradient-to-r from-emerald-950/40 via-neutral-900 to-emerald-950/30 border-emerald-500/50 shadow-sm'
                    : 'bg-neutral-900/60 border-neutral-800/80 hover:border-neutral-700'
                }`}
              >
                <div className="flex justify-between items-center mb-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold border flex items-center gap-1 ${getBadgeStyle(
                        msg.tag
                      )}`}
                    >
                      {renderIcon(msg.tag)}
                      <span>{msg.tag || 'SISTEMA'}</span>
                    </span>
                    {msg.symbol && msg.symbol !== 'SESSÃO' && msg.symbol !== 'FINAL' && (
                      <span className="text-white font-extrabold text-[11px]">{msg.symbol}</span>
                    )}
                  </div>
                  <span className="text-[10px] text-neutral-500">
                    {msg.elapsed_str ? `T+${msg.elapsed_str}` : ''}
                    {msg.timestamp && ` (${new Date(msg.timestamp).toLocaleTimeString('pt-BR')})`}
                  </span>
                </div>
                <p
                  className={`text-xs leading-relaxed ${
                    isOutcome ? 'text-emerald-300 font-semibold' : 'text-neutral-300'
                  }`}
                >
                  {msg.message}
                </p>
              </div>
            )
          })
        )}
      </div>

      {/* Rodapé: Indicador de Atividade ao Vivo */}
      {isRunning && (
        <div className="pt-2 border-t border-neutral-800/80 flex items-center gap-2 text-[11px] text-neutral-400 font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping"></span>
          <span>Sniper AI analisando ticks e avaliando saídas dinâmicas...</span>
        </div>
      )}
    </div>
  )
}

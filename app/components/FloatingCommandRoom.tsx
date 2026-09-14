'use client'

import React, { useState, useEffect } from 'react'
import {
  MessageSquare,
  X,
  Send,
  Terminal,
  RotateCcw,
  Clock,
  CheckCircle2,
  AlertCircle,
  Shield,
  Bot,
} from 'lucide-react'

export interface DirectiveHistoryItem {
  id?: string
  type: 'set' | 'revoked'
  text: string
  hours?: number
  timestamp: string
}

export default function FloatingCommandRoom() {
  const [isOpen, setIsOpen] = useState(false)
  const [activeDirective, setActiveDirective] = useState<string | null>(null)
  const [ttlSeconds, setTtlSeconds] = useState<number | null>(null)
  const [history, setHistory] = useState<DirectiveHistoryItem[]>([])
  const [inputText, setInputText] = useState('')
  const [durationHours, setDurationHours] = useState(24)
  const [loading, setLoading] = useState(false)
  const [feedbackMsg, setFeedbackMsg] = useState<string | null>(null)

  const fetchDirectives = async () => {
    try {
      const res = await fetch('/api/directives')
      if (res.ok) {
        const data = await res.json()
        setActiveDirective(data.activeDirective)
        setTtlSeconds(data.ttl)
        if (Array.isArray(data.history)) {
          setHistory(data.history)
        }
      }
    } catch (e) {
      console.error('Erro ao buscar diretrizes:', e)
    }
  }

  useEffect(() => {
    fetchDirectives()
    const timer = setInterval(fetchDirectives, 15000)
    return () => clearInterval(timer)
  }, [])

  // Timer local para decrementar o TTL
  useEffect(() => {
    if (!ttlSeconds || ttlSeconds <= 0) return
    const timer = setInterval(() => {
      setTtlSeconds((prev) => (prev && prev > 1 ? prev - 1 : null))
    }, 1000)
    return () => clearInterval(timer)
  }, [ttlSeconds])

  const formatTtl = (sec: number | null) => {
    if (!sec || sec <= 0) return 'Expirando'
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    if (h > 0) return `${h}h ${m}m restantes`
    return `${m}m ${sec % 60}s restantes`
  }

  const handleRevoke = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/directives', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'revoke' }),
      })
      if (res.ok) {
        setFeedbackMsg('Instrução revogada com sucesso! IA liberada.')
        await fetchDirectives()
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
      setTimeout(() => setFeedbackMsg(null), 4000)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputText.trim() || loading) return

    setLoading(true)
    try {
      const res = await fetch('/api/directives', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'set',
          directive: inputText.trim(),
          hours: durationHours,
        }),
      })
      if (res.ok) {
        setInputText('')
        setFeedbackMsg('Diretriz injetada com sucesso nos agentes!')
        await fetchDirectives()
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
      setTimeout(() => setFeedbackMsg(null), 4000)
    }
  }

  return (
    <>
      {/* BOTÃO FLUTUANTE (FAB) */}
      <div className="fixed bottom-6 right-6 z-50">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className={`flex items-center gap-2.5 px-4 py-3 rounded-full border transition-all duration-300 backdrop-blur-md shadow-2xl ${
            activeDirective
              ? 'bg-neutral-900/90 border-amber-500/60 text-white shadow-[0_0_25px_rgba(245,158,11,0.25)] hover:border-amber-400'
              : 'bg-neutral-900/90 border-neutral-700/80 text-neutral-200 hover:text-white hover:border-purple-500 shadow-[0_0_25px_rgba(147,51,234,0.2)]'
          }`}
          title="Abrir Sala de Comando & Diretrizes"
        >
          <div className="relative">
            <MessageSquare className="w-5 h-5 text-purple-400" />
            {activeDirective && (
              <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping"></span>
            )}
          </div>
          <span className="font-mono text-xs font-bold tracking-tight">Sala de Comando</span>
          {activeDirective ? (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
              1 Ativa
            </span>
          ) : (
            <span className="text-[10px] text-neutral-500 font-mono">IA Livre</span>
          )}
        </button>
      </div>

      {/* JANELA DE COMANDO FLUTUANTE */}
      {isOpen && (
        <div className="fixed bottom-20 right-6 z-50 w-[95vw] sm:w-[480px] max-h-[80vh] flex flex-col bg-neutral-950/95 border border-neutral-800/90 rounded-2xl shadow-2xl backdrop-blur-xl animate-in fade-in slide-in-from-bottom-5 duration-200 overflow-hidden">
          {/* Header */}
          <div className="p-4 border-b border-neutral-800/80 flex items-center justify-between bg-neutral-900/40">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400">
                <Terminal className="w-4 h-4" />
              </div>
              <div>
                <h3 className="text-xs font-bold text-white font-mono uppercase tracking-wider">
                  Sala de Comando da IA
                </h3>
                <p className="text-[10px] text-neutral-400">
                  Diretrizes em linguagem natural obedecidas por todos os agentes
                </p>
              </div>
            </div>

            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Feedback Toast */}
          {feedbackMsg && (
            <div className="px-4 py-2 bg-emerald-950/80 border-b border-emerald-800/60 text-emerald-300 text-xs font-mono flex items-center gap-2">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
              <span>{feedbackMsg}</span>
            </div>
          )}

          {/* Área de Conteúdo */}
          <div className="p-4 space-y-4 overflow-y-auto flex-1 custom-scrollbar max-h-[50vh]">
            {/* CARD DE DIRETRIZ ATIVA */}
            {activeDirective ? (
              <div className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-950/15 relative overflow-hidden">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="flex items-center gap-1.5 text-[10px] font-mono font-bold text-amber-300 uppercase tracking-wider">
                    <AlertCircle className="w-3 h-3 text-amber-400" />
                    Diretriz Ativa no Fundo
                  </span>
                  {ttlSeconds !== null && (
                    <span className="flex items-center gap-1 text-[10px] font-mono text-neutral-400">
                      <Clock className="w-3 h-3 text-neutral-500" />
                      {formatTtl(ttlSeconds)}
                    </span>
                  )}
                </div>
                <p className="text-sm font-mono text-white leading-relaxed mb-3">&quot;{activeDirective}&quot;</p>

                {/* BOTÃO DE MENSAGEM PRONTA: REVOGAR */}
                <button
                  type="button"
                  onClick={handleRevoke}
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-850 border border-neutral-700/80 text-xs font-mono text-neutral-200 hover:text-white transition-all group"
                >
                  <RotateCcw className="w-3.5 h-3.5 text-neutral-400 group-hover:rotate-[-45deg] transition-transform" />
                  <span>Revogar Instrução &amp; Liberar IA</span>
                </button>
              </div>
            ) : (
              <div className="p-3 rounded-xl border border-neutral-800/80 bg-neutral-900/30 flex items-center gap-2.5 text-xs font-mono text-neutral-400">
                <Shield className="w-4 h-4 text-emerald-400" />
                <span>Nenhuma restrição ativa. Agentes operando no modo autônomo padrão.</span>
              </div>
            )}

            {/* HISTÓRICO DE DIRETRIZES */}
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-2 flex items-center gap-1.5">
                <Clock className="w-3 h-3" />
                Histórico de Comandos Recentes
              </p>
              <div className="space-y-2">
                {history.length === 0 ? (
                  <p className="text-[11px] text-neutral-600 italic font-mono">Nenhum comando recente registrado.</p>
                ) : (
                  history.slice(0, 5).map((item, idx) => (
                    <div
                      key={item.id || idx}
                      className="p-2.5 rounded-lg bg-neutral-900/40 border border-neutral-800/60 text-xs font-mono"
                    >
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <span
                          className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${
                            item.type === 'revoked'
                              ? 'bg-neutral-800 text-neutral-300 border-neutral-700'
                              : 'bg-purple-950/60 text-purple-300 border-purple-800/60'
                          }`}
                        >
                          {item.type === 'revoked' ? 'REVOGAÇÃO' : 'DIRETRIZ'}
                        </span>
                        <span className="text-[10px] text-neutral-500">
                          {item.timestamp ? new Date(item.timestamp).toLocaleTimeString('pt-BR') : ''}
                        </span>
                      </div>
                      <p className="text-neutral-300 text-[11px] line-clamp-2">{item.text}</p>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Formulário de Envio */}
          <form onSubmit={handleSubmit} className="p-4 border-t border-neutral-800/80 bg-neutral-900/40 space-y-3">
            <div>
              <label htmlFor="directive-input" className="block text-[10px] font-mono uppercase text-neutral-400 mb-1.5">
                Nova Diretriz em Linguagem Natural
              </label>
              <textarea
                id="directive-input"
                rows={2}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="Ex: Não opere memecoins hoje, foque apenas em Ethereum..."
                className="w-full bg-neutral-950 border border-neutral-800 rounded-xl p-3 text-xs text-white placeholder-neutral-600 focus:outline-none focus:border-purple-500 transition-colors font-mono resize-none"
              />
            </div>

            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-neutral-500">Duração:</span>
                <select
                  value={durationHours}
                  onChange={(e) => setDurationHours(Number(e.target.value))}
                  className="bg-neutral-950 border border-neutral-800 rounded-lg px-2 py-1 text-xs text-neutral-300 font-mono focus:outline-none focus:border-purple-500"
                >
                  <option value={1}>1 hora</option>
                  <option value={6}>6 horas</option>
                  <option value={12}>12 horas</option>
                  <option value={24}>24 horas (1 dia)</option>
                  <option value={48}>48 horas (2 dias)</option>
                  <option value={168}>7 dias</option>
                </select>
              </div>

              <button
                type="submit"
                disabled={loading || !inputText.trim()}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-mono font-bold transition-all shadow-[0_0_15px_rgba(147,51,234,0.3)]"
              >
                <Send className="w-3.5 h-3.5" />
                <span>Enviar</span>
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}

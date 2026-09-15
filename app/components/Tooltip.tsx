'use client'

import React from 'react'
import { HelpCircle } from 'lucide-react'

interface TooltipProps {
  text: string
  position?: 'top' | 'bottom' | 'left' | 'right'
  size?: 'xs' | 'sm' | 'md'
  children?: React.ReactNode
  className?: string
  width?: string
}

const positionClasses: Record<string, string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  left: 'right-full top-1/2 -translate-y-1/2 mr-2',
  right: 'left-full top-1/2 -translate-y-1/2 ml-2',
}

const sizeClasses: Record<string, string> = {
  xs: 'w-3 h-3',
  sm: 'w-3.5 h-3.5',
  md: 'w-4 h-4',
}

/**
 * Tooltip acessível — exibe painel explicativo ao hover ou foco.
 *
 * Uso simples (ícone de dúvida):
 *   <Tooltip text="Explicação aqui" />
 *
 * Uso com trigger customizado:
 *   <Tooltip text="Explicação" position="bottom">
 *     <span>Texto</span>
 *   </Tooltip>
 */
export default function Tooltip({
  text,
  position = 'top',
  size = 'sm',
  children,
  className = '',
  width = 'w-60',
}: TooltipProps) {
  return (
    <span
      className={`relative inline-flex items-center group/tip ${className}`}
      tabIndex={0}
      role="button"
      aria-label="Mais informações"
    >
      {children ?? (
        <HelpCircle
          className={`${sizeClasses[size]} text-neutral-500 hover:text-neutral-300 transition-colors cursor-help shrink-0`}
        />
      )}

      {/* Painel do tooltip */}
      <span
        className={`
          pointer-events-none absolute z-50 ${width}
          bg-neutral-900 border border-neutral-700 rounded-xl
          px-3 py-2.5 shadow-xl shadow-black/60
          text-[11px] text-neutral-200 leading-relaxed font-sans font-normal
          whitespace-normal text-left
          opacity-0 scale-95
          group-hover/tip:opacity-100 group-hover/tip:scale-100
          group-focus/tip:opacity-100 group-focus/tip:scale-100
          transition-all duration-150 ease-out
          ${positionClasses[position]}
        `}
        aria-hidden="true"
      >
        {text}
      </span>
    </span>
  )
}

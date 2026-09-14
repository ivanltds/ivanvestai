import { NextRequest, NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '',
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '',
})

export async function GET() {
  try {
    const [activeDirective, ttl, historyRaw] = await Promise.all([
      redis.get<string>('ai:user_directives'),
      redis.ttl('ai:user_directives'),
      redis.lrange<any>('ai:directives_history', 0, 25),
    ])

    const history = (historyRaw || []).map((item: any) => {
      if (!item) return null
      try {
        return typeof item === 'string' ? JSON.parse(item) : item
      } catch {
        return { text: String(item), timestamp: new Date().toISOString() }
      }
    }).filter(Boolean)

    return NextResponse.json({
      activeDirective: activeDirective || null,
      ttl: ttl && ttl > 0 ? ttl : null,
      history,
    })
  } catch (error: any) {
    return NextResponse.json({ error: error?.message || 'Failed to fetch directives' }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { action, directive, hours = 24 } = body

    if (action === 'revoke') {
      await redis.del('ai:user_directives')
      
      const historyEntry = {
        id: `rev-${Date.now()}`,
        type: 'revoked',
        text: 'Instrução revogada pelo investidor. Agentes liberados para estratégia padrão.',
        timestamp: new Date().toISOString(),
      }
      await redis.lpush('ai:directives_history', JSON.stringify(historyEntry))
      await redis.ltrim('ai:directives_history', 0, 49)

      return NextResponse.json({
        success: true,
        message: 'Diretriz revogada com sucesso.',
        activeDirective: null,
      })
    }

    if (!directive || typeof directive !== 'string' || !directive.trim()) {
      return NextResponse.json({ error: 'Diretriz vazia não permitida' }, { status: 400 })
    }

    const cleanDirective = directive.trim()
    const durationHours = Math.max(1, Math.min(Number(hours) || 24, 720))
    const expirationSeconds = durationHours * 3600

    await redis.set('ai:user_directives', cleanDirective, { ex: expirationSeconds })

    const historyEntry = {
      id: `dir-${Date.now()}`,
      type: 'set',
      text: cleanDirective,
      hours: durationHours,
      timestamp: new Date().toISOString(),
    }
    await redis.lpush('ai:directives_history', JSON.stringify(historyEntry))
    await redis.ltrim('ai:directives_history', 0, 49)

    return NextResponse.json({
      success: true,
      activeDirective: cleanDirective,
      ttl: expirationSeconds,
    })
  } catch (error: any) {
    return NextResponse.json({ error: error?.message || 'Failed to update directive' }, { status: 500 })
  }
}

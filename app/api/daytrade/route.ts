import { NextRequest, NextResponse } from 'next/server'
import { Redis } from '@upstash/redis'

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '',
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '',
})

const safeParse = (raw: any, defaultObj: any) => {
  if (!raw) return defaultObj
  try {
    const decoded = typeof raw === 'string' ? decodeURIComponent(raw) : raw
    return typeof decoded === 'string' ? JSON.parse(decoded) : decoded
  } catch {
    return defaultObj
  }
}

export async function GET() {
  try {
    const [sessionRaw, lastCycleTsRaw, snapshotsRaw, microtradesRaw] = await Promise.all([
      redis.get<any>('daytrade:session'),
      redis.get<any>('daytrade:last_cycle_timestamp'),
      redis.lrange<any>('daytrade:snapshots', 0, 30),
      redis.lrange<any>('daytrade:trades', 0, 50),
    ])

    const session = safeParse(sessionRaw, null)
    const lastCycleTs = lastCycleTsRaw ? parseFloat(lastCycleTsRaw) : null
    const snapshots = (snapshotsRaw || []).map((s: any) => safeParse(s, {})).reverse()
    const microtrades = (microtradesRaw || []).map((t: any) => safeParse(t, {})).reverse()

    // Cálculo da estimativa de início com base no ciclo de 15 minutos (900s)
    let estimatedWaitSeconds = 0
    const now = Date.now() / 1000
    if (lastCycleTs) {
      const elapsedSinceLastCycle = now - lastCycleTs
      const remainingToNext = 900 - elapsedSinceLastCycle
      // Se já passou de 900s, o próximo ciclo deve disparar a qualquer segundo
      estimatedWaitSeconds = remainingToNext > 0 ? Math.ceil(remainingToNext) : 10
    } else {
      estimatedWaitSeconds = 60 // Fallback estimado
    }

    return NextResponse.json({
      session,
      estimatedWaitSeconds,
      lastCycleTs,
      snapshots,
      microtrades,
      serverTime: now,
    })
  } catch (error: any) {
    return NextResponse.json({ error: error?.message || 'Erro ao consultar Daytrade' }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const action = body.action || 'request'

    if (action === 'request') {
      const capital = parseFloat(body.capital || 50.0)
      const currency = (body.currency || 'BRL').toUpperCase()
      const symbol = currency === 'USDT' ? 'BTC/USDT' : 'BTC/BRL'

      const session = {
        status: 'pending',
        capital,
        currency,
        symbol,
        requested_at: new Date().toISOString(),
        started_at: null,
        duration_minutes: 10,
      }

      await redis.set('daytrade:session', JSON.stringify(session))
      return NextResponse.json({ success: true, session })
    } else if (action === 'cancel') {
      const sessionRaw = await redis.get<any>('daytrade:session')
      const session = safeParse(sessionRaw, {})
      session.status = 'cancelled'
      session.cancelled_at = new Date().toISOString()
      await redis.set('daytrade:session', JSON.stringify(session))
      return NextResponse.json({ success: true, session })
    }

    return NextResponse.json({ error: 'Ação desconhecida' }, { status: 400 })
  } catch (error: any) {
    return NextResponse.json({ error: error?.message || 'Erro ao processar Daytrade' }, { status: 500 })
  }
}

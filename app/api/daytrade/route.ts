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
    const [
      sessionRaw, 
      lastCycleTsRaw, 
      snapshotsRaw, 
      microtradesRaw, 
      tradesRaw,
      accountBalancesRaw,
      botConfigRaw
    ] = await Promise.all([
      redis.get<any>('daytrade:session'),
      redis.get<any>('daytrade:last_cycle_timestamp'),
      redis.lrange<any>('daytrade:snapshots', -30, -1),
      redis.lrange<any>('daytrade:microtrades', -50, -1),
      redis.lrange<any>('daytrade:trades', -50, -1),
      redis.get<any>('portfolio:account_balances'),
      redis.get<any>('bot:config'),
    ])

    const session = safeParse(sessionRaw, null)
    const lastCycleTs = lastCycleTsRaw ? parseFloat(lastCycleTsRaw) : null
    const snapshots = (snapshotsRaw || []).map((s: any) => safeParse(s, {})).reverse()
    const rawTrades = (microtradesRaw && microtradesRaw.length > 0) ? microtradesRaw : (tradesRaw || [])
    const microtrades = rawTrades.map((t: any) => safeParse(t, {})).reverse()
    const accountBalances = safeParse(accountBalancesRaw, {})
    const botConfig = safeParse(botConfigRaw, { dry_run: false })

    // Saldo disponível oficial
    const brlBalance = parseFloat(accountBalances.BRL || 0)
    const usdtBalance = parseFloat(accountBalances.USDT || 0)

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
      balances: {
        BRL: brlBalance,
        USDT: usdtBalance,
      },
      botConfig: {
        dry_run: botConfig.dry_run ?? false,
      }
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
      const capital = parseFloat(body.capital || 0)
      const currency = (body.currency || 'BRL').toUpperCase()
      const symbol = currency === 'USDT' ? 'BTC/USDT' : 'BTC/BRL'

      // Consulta saldos atuais e configuração para validação de segurança
      const [accountBalancesRaw, botConfigRaw] = await Promise.all([
        redis.get<any>('portfolio:account_balances'),
        redis.get<any>('bot:config'),
      ])
      const accountBalances = safeParse(accountBalancesRaw, {})
      const botConfig = safeParse(botConfigRaw, { dry_run: false })
      const isDryRun = botConfig.dry_run ?? false

      const brlBalance = parseFloat(accountBalances.BRL || 0)
      const usdtBalance = parseFloat(accountBalances.USDT || 0)

      // Validação de Mínimo por Par
      const minRequired = currency === 'USDT' ? 5.0 : 10.0
      if (capital < minRequired) {
        return NextResponse.json(
          { error: `O valor mínimo para operações de trade na Binance é de ${currency === 'USDT' ? '$5.00 USDT' : 'R$ 10,00'}.` },
          { status: 400 }
        )
      }

      // Validação de Saldo Disponível (se mercado real)
      if (!isDryRun) {
        const availableBalance = currency === 'USDT' ? usdtBalance : brlBalance
        if (availableBalance < minRequired) {
          return NextResponse.json(
            { error: `Saldo insuficiente na Binance: você possui ${currency === 'USDT' ? `$${availableBalance.toFixed(2)} USDT` : `R$ ${availableBalance.toFixed(2)}`}, mas o mínimo exigido é ${currency === 'USDT' ? '$5.00 USDT' : 'R$ 10,00'}. Ative o Modo Simulação para testar.` },
            { status: 400 }
          )
        }
        if (capital > availableBalance) {
          return NextResponse.json(
            { error: `O capital configurado (${currency === 'USDT' ? `$${capital.toFixed(2)}` : `R$ ${capital.toFixed(2)}`}) excede o saldo livre disponível na corretora (${currency === 'USDT' ? `$${availableBalance.toFixed(2)}` : `R$ ${availableBalance.toFixed(2)}`}).` },
            { status: 400 }
          )
        }
      }

      const session = {
        status: 'pending',
        capital,
        currency,
        symbol,
        requested_at: new Date().toISOString(),
        started_at: null,
        duration_minutes: 10,
      }

      await Promise.all([
        redis.set('daytrade:session', JSON.stringify(session)),
        redis.del('daytrade:snapshots'),
        redis.del('daytrade:microtrades'),
        redis.del('daytrade:trades'),
      ])
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

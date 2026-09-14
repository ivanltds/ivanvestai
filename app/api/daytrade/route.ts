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
      dailyTradesRaw,
      accountBalancesRaw,
      openPositionsRaw,
      botConfigRaw,
      chatRaw,
      historyRaw
    ] = await Promise.all([
      redis.get<any>('daytrade:session'),
      redis.get<any>('daytrade:last_cycle_timestamp'),
      redis.lrange<any>('daytrade:snapshots', -100, -1),
      redis.lrange<any>('daytrade:microtrades', -50, -1),
      redis.lrange<any>('daytrade:trades', -50, -1),
      redis.lrange<any>('daytrade:daily_trades', -300, -1),
      redis.get<any>('portfolio:account_balances'),
      redis.get<any>('portfolio:open_positions'),
      redis.get<any>('bot:config'),
      redis.lrange<any>('daytrade:chat', -100, -1),
      redis.lrange<any>('daytrade:history', 0, 19),
    ])

    const session = safeParse(sessionRaw, null)
    const lastCycleTs = lastCycleTsRaw ? parseFloat(lastCycleTsRaw) : null
    const snapshots = (snapshotsRaw || []).map((s: any) => safeParse(s, {})).reverse()
    const rawTrades = (microtradesRaw && microtradesRaw.length > 0) ? microtradesRaw : (tradesRaw || [])
    const microtrades = rawTrades.map((t: any) => safeParse(t, {})).reverse()
    const accountBalances = safeParse(accountBalancesRaw, {})
    const openPositions = safeParse(openPositionsRaw, {})
    const botConfig = safeParse(botConfigRaw, { dry_run: false })
    const chatMessages = (chatRaw || []).map((m: any) => safeParse(m, {}))
    const daytradeHistory = (historyRaw || []).map((h: any) => safeParse(h, {}))

    // Saldo disponível oficial
    const brlBalance = parseFloat(accountBalances.BRL || 0)
    const usdtBalance = parseFloat(accountBalances.USDT || 0)
    const btcBalance = parseFloat(accountBalances.BTC || 0)
    const ethBalance = parseFloat(accountBalances.ETH || 0)

    // Taxa de câmbio aproximada USD/BRL e cotações de carteira
    const btcBrlPrice = openPositions['BTC/BRL']?.current_price || 407000
    const ethBrlPrice = openPositions['ETH/BRL']?.current_price || 13000
    const usdtBrlRate = 5.20 // fallback estável

    // Mapeamento de todos os ativos da carteira com saldo positivo
    const walletAssets = [
      {
        asset: 'BTC',
        name: 'Bitcoin',
        balance: btcBalance,
        approxBrl: btcBalance * btcBrlPrice,
        approxUsd: (btcBalance * btcBrlPrice) / usdtBrlRate,
        minCapitalUsd: 5.0,
        canTrade: (btcBalance * btcBrlPrice) / usdtBrlRate >= 1.0,
      },
      {
        asset: 'ETH',
        name: 'Ethereum',
        balance: ethBalance,
        approxBrl: ethBalance * ethBrlPrice,
        approxUsd: (ethBalance * ethBrlPrice) / usdtBrlRate,
        minCapitalUsd: 5.0,
        canTrade: (ethBalance * ethBrlPrice) / usdtBrlRate >= 1.0,
      },
      {
        asset: 'USDT',
        name: 'Tether USD',
        balance: usdtBalance,
        approxBrl: usdtBalance * usdtBrlRate,
        approxUsd: usdtBalance,
        minCapitalUsd: 1.0, // $1 min para moedas meme na Binance
        canTrade: usdtBalance >= 1.0,
      },
      {
        asset: 'BRL',
        name: 'Real Brasileiro',
        balance: brlBalance,
        approxBrl: brlBalance,
        approxUsd: brlBalance / usdtBrlRate,
        minCapitalUsd: 2.0,
        canTrade: brlBalance >= 10.0,
      },
    ].filter((w) => w.balance > 0.00000001)

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

    // =========================================================================
    // Agregação do Balanço Diário de Operações Sniper (Ganhos e Perdas)
    // =========================================================================
    const todayUtc = new Date().toISOString().slice(0, 10)
    let todayBrt = todayUtc
    try {
      todayBrt = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())
    } catch {
      todayBrt = todayUtc
    }

    const parsedDailyTrades = (dailyTradesRaw || []).map((t: any) => safeParse(t, {}))
    const parsedStoredTrades = (tradesRaw || []).map((t: any) => safeParse(t, {}))
    const parsedCurrentMicro = (microtradesRaw || []).map((t: any) => safeParse(t, {}))

    const allCandidateTrades: any[] = [
      ...parsedDailyTrades,
      ...parsedStoredTrades,
      ...parsedCurrentMicro,
    ]

    for (const h of daytradeHistory) {
      if (Array.isArray(h?.trades)) {
        for (const t of h.trades) {
          allCandidateTrades.push(t)
        }
      }
    }

    const seenTradeKeys = new Set<string>()
    const uniqueTodayTrades: any[] = []

    for (const t of allCandidateTrades) {
      if (!t || !t.timestamp) continue
      const ts = String(t.timestamp)
      const datePart = ts.slice(0, 10)
      const tradeTimeMs = new Date(ts).getTime()
      const isWithin24h = !isNaN(tradeTimeMs) && (Date.now() - tradeTimeMs) < 24 * 60 * 60 * 1000
      const isDateMatch = datePart === todayUtc || datePart === todayBrt

      if (isDateMatch || isWithin24h) {
        const key = `${ts}_${t.symbol || t.pair}_${t.action || t.type}_${t.price}_${t.qty || t.crypto_qty}`
        if (!seenTradeKeys.has(key)) {
          seenTradeKeys.add(key)
          uniqueTodayTrades.push({
            ...t,
            symbol: t.symbol || t.pair || 'DESCONHECIDO',
            action: t.action || t.type || 'TRADE',
            pnl_pct: typeof t.pnl_pct === 'number' ? t.pnl_pct : (parseFloat(t.pnl_pct) || 0),
            net_pnl_fiat: typeof t.net_pnl_fiat === 'number' ? t.net_pnl_fiat : (parseFloat(t.net_pnl_fiat) || 0),
            currency: t.currency || 'USDT',
            price: typeof t.price === 'number' ? t.price : (parseFloat(t.price) || 0),
            amount: typeof t.amount === 'number' ? t.amount : (parseFloat(t.amount) || 0),
          })
        }
      }
    }

    // Ordena do mais recente para o mais antigo
    uniqueTodayTrades.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())

    // Filtra posições encerradas (vendas) para cálculo oficial de PnL financeiro
    const sellTrades = uniqueTodayTrades.filter((t) => t.action === 'SELL')
    const totalClosed = sellTrades.length
    const winningTrades = sellTrades.filter((t) => t.net_pnl_fiat > 0).length
    const losingTrades = sellTrades.filter((t) => t.net_pnl_fiat < 0).length
    const breakevenTrades = sellTrades.filter((t) => t.net_pnl_fiat === 0).length
    const winRatePct = totalClosed > 0 ? Math.round((winningTrades / totalClosed) * 1000) / 10 : 0

    const netPnlBrl = Math.round(sellTrades.filter((t) => t.currency === 'BRL').reduce((acc, t) => acc + (t.net_pnl_fiat || 0), 0) * 100) / 100
    const netPnlUsdt = Math.round(sellTrades.filter((t) => t.currency === 'USDT').reduce((acc, t) => acc + (t.net_pnl_fiat || 0), 0) * 100) / 100

    const totalVolumeBrl = Math.round(uniqueTodayTrades.filter((t) => t.currency === 'BRL').reduce((acc, t) => acc + (t.amount || 0), 0) * 100) / 100
    const totalVolumeUsdt = Math.round(uniqueTodayTrades.filter((t) => t.currency === 'USDT').reduce((acc, t) => acc + (t.amount || 0), 0) * 100) / 100

    const dailySummary = {
      date: todayBrt,
      total_trades: totalClosed,
      total_orders: uniqueTodayTrades.length,
      winning_trades: winningTrades,
      losing_trades: losingTrades,
      breakeven_trades: breakevenTrades,
      win_rate_pct: winRatePct,
      net_pnl_brl: netPnlBrl,
      net_pnl_usdt: netPnlUsdt,
      total_volume_brl: totalVolumeBrl,
      total_volume_usdt: totalVolumeUsdt,
      trades: uniqueTodayTrades,
      closed_trades: sellTrades,
    }

    return NextResponse.json({
      session,
      estimatedWaitSeconds,
      lastCycleTs,
      snapshots,
      microtrades,
      dailySummary,
      serverTime: now,
      walletAssets,
      chatMessages,
      daytradeHistory,
      balances: {
        BRL: brlBalance,
        USDT: usdtBalance,
        BTC: btcBalance,
        ETH: ethBalance,
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
      const currency = (body.currency || 'USDT').toUpperCase()
      const sourceAsset = (body.source_asset || 'USDT').toUpperCase()
      const symbol = 'SCANNER_AUTO'

      // Consulta saldos atuais e posições para validação de segurança
      const [accountBalancesRaw, openPositionsRaw, botConfigRaw] = await Promise.all([
        redis.get<any>('portfolio:account_balances'),
        redis.get<any>('portfolio:open_positions'),
        redis.get<any>('bot:config'),
      ])
      const accountBalances = safeParse(accountBalancesRaw, {})
      const openPositions = safeParse(openPositionsRaw, {})
      const botConfig = safeParse(botConfigRaw, { dry_run: false })
      const isDryRun = botConfig.dry_run ?? false

      const usdtBrlRate = 5.20
      const btcBrlPrice = openPositions['BTC/BRL']?.current_price || 407000
      const ethBrlPrice = openPositions['ETH/BRL']?.current_price || 13000

      // Calcula o poder de compra do ativo fonte selecionado em USD e BRL
      let sourceBalance = parseFloat(accountBalances[sourceAsset] || 0)
      let sourceBalanceUsd = 0

      if (sourceAsset === 'USDT') {
        sourceBalanceUsd = sourceBalance
      } else if (sourceAsset === 'BRL') {
        sourceBalanceUsd = sourceBalance / usdtBrlRate
      } else if (sourceAsset === 'BTC') {
        sourceBalanceUsd = (sourceBalance * btcBrlPrice) / usdtBrlRate
      } else if (sourceAsset === 'ETH') {
        sourceBalanceUsd = (sourceBalance * ethBrlPrice) / usdtBrlRate
      } else {
        sourceBalanceUsd = sourceBalance
      }

      // Validação de Mínimo da Binance ($1 para memecoins, $5 para altcoins maiores)
      const minRequiredUsd = 1.0 // Suporta moedas com minNotional $1 como PEPE/DOGE
      if (currency === 'USDT' && capital < 1.0) {
        return NextResponse.json(
          { error: 'O valor mínimo para operações no modo Scanner é de $1.00 USDT.' },
          { status: 400 }
        )
      }
      if (currency === 'BRL' && capital < 10.0) {
        return NextResponse.json(
          { error: 'O valor mínimo em Reais para operações é de R$ 10,00.' },
          { status: 400 }
        )
      }

      // Validação de Saldo Disponível no ativo de origem
      if (!isDryRun) {
        const capitalInUsd = currency === 'BRL' ? capital / usdtBrlRate : capital
        if (capitalInUsd > sourceBalanceUsd * 1.01) { // 1% tolerância para variações de preço
          return NextResponse.json(
            {
              error: `Capital selecionado (${currency === 'BRL' ? `R$ ${capital.toFixed(2)}` : `$${capital.toFixed(2)}`}) excede o saldo disponível de ${sourceAsset} (aprox. $${sourceBalanceUsd.toFixed(2)} USD).`,
            },
            { status: 400 }
          )
        }
      }

      const session = {
        status: 'pending',
        capital,
        currency,
        source_asset: sourceAsset,
        symbol,
        requested_at: new Date().toISOString(),
        started_at: null,
        duration_minutes: 10,
      }

      const startMsg = {
        id: `chat_${Date.now()}`,
        timestamp: new Date().toISOString(),
        elapsed_sec: 0,
        elapsed_str: "00:00",
        type: "SYSTEM",
        symbol: "SESSÃO",
        tag: "SISTEMA",
        message: `⚡ Nova solicitação de Day Trade: $${capital.toFixed(2)} ${currency} financiado via ${sourceAsset}. Aguardando próximo ciclo de varredura.`,
        sender: "Ivanvest AI"
      }

      await Promise.all([
        redis.set('daytrade:session', JSON.stringify(session)),
        redis.del('daytrade:snapshots'),
        redis.del('daytrade:microtrades'),
        redis.rpush('daytrade:chat', JSON.stringify(startMsg)),
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


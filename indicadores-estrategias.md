# IvanVestAI — Indicadores, Estratégias e Fontes de Notícias

Detalhamento técnico da seção "Estratégias de trading" do `mvp-spec.md`. Este documento define os parâmetros concretos que os agentes vão usar — é o que vira código de fato nos agentes de análise técnica e de notícias.

## 1. Fontes RSS (validadas)

Todas as URLs abaixo foram testadas e confirmadas como feeds RSS/XML válidos e ativos (verificado em 16/09/2026). Mix de fontes internacionais (alto volume, cobertura ampla) e brasileiras (idioma nativo, contexto local/regulatório BR).

| Fonte | URL do feed | Idioma |
|---|---|---|
| CoinDesk | `https://www.coindesk.com/arc/outboundfeeds/rss/` | EN |
| Cointelegraph | `https://cointelegraph.com/rss` | EN |
| The Block | `https://www.theblock.co/rss.xml` | EN |
| Decrypt | `https://decrypt.co/feed` | EN |
| CryptoSlate | `https://cryptoslate.com/feed/` | EN |
| BeInCrypto | `https://beincrypto.com/feed/` | EN |
| Bitcoin Magazine | `https://bitcoinmagazine.com/feed` | EN |
| NewsBTC | `https://www.newsbtc.com/feed/` | EN |
| Livecoins | `https://livecoins.com.br/feed/` | PT-BR |
| Criptofácil | `https://www.criptofacil.com/feed/` | PT-BR |

Descartados na validação: `br.cointelegraph.com/rss` (retornou erro 410 — feed desativado) e `portaldobitcoin.uol.com.br/feed/` (bloqueio de acesso) e `moneytimes.com.br/feed/` (feed é de notícias gerais, não cripto-específico). Revisar periodicamente se algum feed muda de URL ou sai do ar — vale um health-check automático no próprio bot (se um feed falhar 3 ciclos seguidos, alertar por e-mail).

Com 10 fontes ativas de alto volume de publicação, o requisito de "mínimo 10 notícias novas a cada 15 minutos" deve ser folgado na maior parte do tempo; o agente de notícias deve deduplicar por similaridade de título/conteúdo (várias fontes cobrem o mesmo fato) antes de gerar os resumos.

## 2. Indicadores por tipo de estratégia

O comitê escolhe qual estratégia aplicar por ativo conforme o regime de mercado identificado (tendência forte, faixa lateral, rompimento, etc.). Cada estratégia usa um conjunto próprio de indicadores; o cálculo roda sobre klines da própria Binance.

### 2.1 Trend following (seguir tendência)
- **EMA 9 / EMA 21 / EMA 50**: cruzamento de médias (EMA9 > EMA21 > EMA50 = tendência de alta confirmada).
- **MACD (12, 26, 9)**: histograma positivo e crescente reforça entrada.
- **ADX (14)**: só considerar "em tendência" se ADX > 25 (abaixo disso, tratar como mercado lateral e não aplicar essa estratégia).
- **Timeframe**: tendência avaliada no 4h/1h; timing de entrada no 15m.

### 2.2 Mean reversion (reversão à média)
- **RSI (14)**: sobrevendido < 30 (candidato a compra), sobrecomprado > 70 (candidato a venda/saída).
- **Bollinger Bands (20, 2)**: preço tocando ou rompendo a banda inferior/superior como gatilho, %B como score contínuo.
- **Estocástico (14, 3, 3)**: confirma sobrevenda/sobrecompra junto com o RSI (reduz sinal falso).
- **Pré-condição**: só aplicar se ADX < 20-25 (mercado sem tendência forte — senão "reversão" pode ser só o início de uma queda/alta maior).

### 2.3 Breakout (rompimento de faixa)
- **Donchian Channels (20 períodos)**: rompimento da máxima/mínima de 20 períodos como gatilho.
- **Volume relativo**: volume do candle de rompimento ≥ 2x a média móvel de volume de 20 períodos (confirma que é rompimento real, não ruído).
- **ATR (14)**: usado para dimensionar o quão "significativo" é o rompimento e para calcular stop inicial (ex: stop a 1.5x ATR abaixo do ponto de entrada).

### 2.4 Scalping de curtíssimo prazo
- **VWAP**: referência de preço "justo" intradiário — comprar abaixo do VWAP com reversão, vender ao cruzar de volta.
- **EMA 9 / EMA 21 no timeframe de 1m/5m**: cruzamentos rápidos para timing de entrada/saída.
- **RSI rápido (7 períodos)**: mais sensível que o RSI(14) padrão, adequado ao ritmo do scalping.
- Uso mais restrito dado o ciclo do comitê ser de 15 min (scalping tradicional é sub-minuto) — na prática, aplicar como "scalping de curto prazo" dentro da janela de cada ciclo, não como scalping de alta frequência literal.

## 3. Indicadores e regras transversais (aplicam-se a todas as estratégias)

- **ATR (14)**: usado em todas as estratégias para dimensionar stop-loss/take-profit e ajustar o trailing stop de forma proporcional à volatilidade do ativo (evita stop fixo em % que é apertado demais em ativo volátil ou frouxo demais em ativo calmo).
- **Confluência mínima para entrada**: pelo menos 2 de 3 indicadores da estratégia escolhida precisam concordar na mesma direção. Sistema de voto simples: cada indicador vota +1 (favorável), 0 (neutro) ou -1 (contrário); soma ≥ +2 libera a entrada para avaliação do agente de viabilidade.
- **Correlação entre ativos**: calcular correlação móvel de 30 dias entre o par candidato e o BTC (e entre os pares já em carteira). Evitar abrir posição se a correlação com alguma posição já aberta for > 0.75 — considerar como "mesma aposta" e não diversificação real.
- **Classificação setorial**: mapear cada moeda do universo Top 100 numa categoria (Layer 1, Layer 2, DeFi, Gaming/Metaverse, Meme, Infraestrutura/Oracle, Exchange token, Stablecoin — excluída da negociação). Fonte inicial: categorias públicas do CoinGecko, com override manual na configuração se necessário.
- **Score de meme coin (dentro da tolerância de 5%)**: combinação ponderada de (a) z-score de volume das últimas 24h vs. média de 30 dias, (b) score de sentimento/menção do agente de notícias, (c) momentum de preço (variação % nas últimas 4h e 24h). Um ativo só entra na cesta de "alto risco tolerado" se pelo menos 2 dos 3 sinais estiverem elevados simultaneamente.
- **Circuit breaker (10%)**: calculado sobre o equity total (BRL) no início do dia (00:00 America/Sao_Paulo) vs. equity atual. Ao cruzar -10%, dispara e-mail; não bloqueia novas entradas automaticamente (conforme decisão de risco já registrada no spec principal).
- **Pausa por volatilidade extrema**: gatilho quando o candle mais recente (no timeframe de 15m) tiver variação de preço acima de N desvios-padrão do ATR médio recente do próprio ativo, ou volume > 3x a média — nesses casos, pausar novas entradas nesse ativo (não no bot inteiro) por um cooldown configurável (sugestão inicial: 30-60 min).
- **Janelas de risco (calendário macro)**: manter uma lista configurável de eventos recorrentes (reuniões do FOMC, divulgação de CPI/payroll dos EUA) com data/hora; bloquear novas entradas numa janela ao redor desses eventos (sugestão inicial: -15min a +30min). MVP pode começar com uma lista estática atualizada manualmente; evolução futura seria integrar uma API de calendário econômico.

## 4. Nível de confiança do comitê

- Cada agente relevante (técnico, notícia, viabilidade) contribui com uma nota de confiança (0-100%) e sua justificativa.
- A decisão final exige confiança agregada ≥ 80% (ponderação sugerida: 50% análise técnica/viabilidade, 30% avaliação de risco/portfólio, 20% notícias/sentimento — ajustável depois de rodar com dados reais).
- Abaixo de 80%, a oportunidade vai para o log de "rejeitadas" (resumido/colapsado no dashboard), não vira operação.

## Pendências para quando formos implementar
- Definir a biblioteca de indicadores técnicos em Python (ex: `pandas-ta` ou `ta-lib`) e validar performance de cálculo pros ~100 pares a cada ciclo de 15 min.
- Montar a tabela de classificação setorial inicial (Top 100 moedas → setor) — pode ser gerada uma vez e revisada periodicamente.
- Montar a lista inicial do calendário de eventos macro (FOMC/CPI) para os próximos meses.
- Definir os pesos exatos de ponderação da confiança agregada com base nos primeiros backtests.

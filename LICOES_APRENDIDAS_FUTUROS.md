# Lições Aprendidas: Operações de Futuros e Bloqueios Regulatórios

## 1. Bloqueio da CVM em Corretoras Estrangeiras (Binance, Bybit, OKX)
- **Binance:** Brasileiros residentes e com KYC do Brasil **não conseguem** ativar a API de negociação de futuros, pois a CVM proíbe a oferta de derivativos para clientes locais sem autorização. Embora o usuário possa mudar o idioma para "Inglês" para passar pela tela de aviso e abrir a carteira `USDⓈ-M Futures`, a **geração da API Key** com a permissão "Enable Futures" é barrada permanentemente por ser vinculada ao CPF/documento.
- **Bybit:** Apesar de antes permitir operações irrestritas, a Bybit se adequou às regras da CVM e, a partir de **Setembro de 2026**, bloqueou ativamente o trading de futuros e derivativos para contas brasileiras.
- **Conclusão:** Para rodar robôs quantitativos institucionais no Brasil sem enfrentar o bloqueio geográfico de Futuros, as alternativas factíveis são:
  1. Operar puramente em **Mercado Spot (À Vista)** em grandes corretoras.
  2. Migrar o robô para corretoras sem regras duras de KYC (MEXC, KuCoin) ou plataformas descentralizadas de derivativos (ex: Hyperliquid, dYdX).

## 2. Posições "Fantasmas" e a transição `DRY_RUN`
- **Problema Observado:** Quando o modo simulação (`DRY_RUN=True`) abriu uma posição (salvando na memória do banco de dados `daytrade:session` via Vercel KV) e o script foi interrompido sem ser finalizado, a posição "pendente" ficou presa na memória. 
- **O Conflito:** Ao alterar o ambiente para Produção (`DRY_RUN=False`) e rodar o robô novamente, o sistema leu a posição virtual como se fosse real. Ao atingir o alvo (Target), tentou executar uma ordem de fechamento verdadeira (VENDA de NEAR) na Binance. Como não havia ativos reais comprados na carteira Spot, a Binance rejeitou com `Insufficient Balance`.
- **Prevenção:** O script de execução continua repetindo a checagem porque não conseguiu vender. A lição é que **sempre que houver transição de DRY_RUN de `True` para `False`**, o banco de dados da sessão de day trade **deve ser limpo** (`FLUSHDB` ou apagar a chave da sessão) para evitar cruzar operações virtuais com execução de ordens reais.

## 3. Sobrecarga de Tokens (OpenAI Rate Limits - Erro 429)
- **Causa Raiz:** O agente "Performance Analyst" recebia o histórico inteiro das sessões (incluindo o texto maciço das notícias globais consumidas nas operações anteriores). Isso empurrou o tamanho do prompt para mais de 300.000 tokens por requisição, estourando o TPM (Tokens Per Minute) do GPT-4o-mini.
- **Correção Adotada:** Enviar apenas resumos limpos (`simplified_logs`), limitando os logs enviados à data, aos ativos comprados/vendidos e ao "sentiment" direto, desprezando o texto bruto de notícias arquivado na memória.

## 4. Requisições no Vercel KV (Erro HTTP 431)
- A API REST da Upstash/Vercel aceita parâmetros na própria URL (`GET /set/key/value`), o que gerava um `HTTP 431 Request Header Fields Too Large` quando o robô tentava salvar o painel inteiro do chat num único pacote.
- A solução definitiva arquitetada foi abandonar requisições `GET` na URL e usar `POST` diretamente na rota `/` enviando o comando como array JSON (`["SET", "chave", "valor"]`) no corpo da mensagem. Isso elimina o gargalo de caracteres da URL para strings muito extensas geradas pelas memórias do LLM.

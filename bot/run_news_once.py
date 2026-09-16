"""Roda o NewsAgent uma vez (fora do ciclo de 15 min do bot, que nunca foi
ligado -- bot_status continua `paused`, ver arquitetura-tecnica.md 9.3) só
pra popular a tabela `news_items` no Postgres, que o dashboard web lê em
/news. Sem isso a página fica vazia: não é bug de carregamento, é que
NENHUM agente do comitê jamais rodou de verdade nesta sessão (só os
backtests e o paper trading, que não tocam nessa tabela).

De brinde, isso testa uma parte do scaffold que nunca tinha sido exercitada
contra a API de verdade: a chamada Structured Outputs da OpenAI (ver ponto
em aberto na seção 9.3 de arquitetura-tecnica.md) -- se algo estiver errado
com a chave, o nome do modelo, ou o schema Pydantic, aparece aqui.

Uso:
    python run_news_once.py
"""
from __future__ import annotations

from agents.news_agent import NewsAgent

SCRIPT_VERSION = "2026-09-16-v1"


def main() -> None:
    print(f"[run_news_once.py versão: {SCRIPT_VERSION}]")
    print("Buscando RSS, deduplicando e resumindo/pontuando via OpenAI (Structured Outputs)...")
    saved = NewsAgent().run()

    print()
    print(f"=== {len(saved)} notícia(s) nova(s) salva(s) em news_items ===")
    for item in saved:
        print(f"[{item.source}] ({item.sentiment_score:+.2f}) {item.summary_pt}")

    if not saved:
        print("Nenhuma notícia nova (ou tudo já estava salvo de uma execução anterior --")
        print("dedup_hash evita duplicar). Recarrega a página /news no dashboard.")
    else:
        print()
        print("Pronto -- recarrega a página /news no dashboard pra ver.")


if __name__ == "__main__":
    main()

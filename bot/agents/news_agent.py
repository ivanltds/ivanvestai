"""Busca os RSS validados, deduplica, resume+traduz (modelo barato) e
gera score de sentimento. Ver indicadores-estrategias.md seção 1."""
from __future__ import annotations

import hashlib
import logging

import feedparser
import requests
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from agents.base import BaseAgent
from config.settings import settings
from core.llm_client import call_structured
from db.models import NewsItem
from db.session import get_session

logger = logging.getLogger(__name__)

# Achado 24/09/2026 (arquitetura-tecnica.md 9.20, itens fora do corte de 30 --
# Fase 5): feedparser.parse(url) não tem timeout nenhum quando recebe uma URL
# direto -- uma fonte RSS lenta/travada podia travar fetch_recent_items()
# indefinidamente. Buscamos o conteúdo com requests (timeout explícito) e só
# então passamos os bytes pro feedparser -- por fonte, isolado, pra uma fonte
# fora do ar não derrubar as outras 9.
_FEED_TIMEOUT_SECONDS = 10


def _fetch_feed(source: str, url: str):
    try:
        response = requests.get(url, timeout=_FEED_TIMEOUT_SECONDS, headers={"User-Agent": "ivanvestai-bot/1.0"})
        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception as exc:
        logger.warning("NewsAgent: falha ao buscar feed %s (%s) -- pulando esta fonte neste ciclo.", source, exc)
        return None

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "BeInCrypto": "https://beincrypto.com/feed/",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
    "NewsBTC": "https://www.newsbtc.com/feed/",
    "Livecoins": "https://livecoins.com.br/feed/",
    # "Criptofácil" removida em 26/09/2026 (multi-conta-plano.md 10.15/10.18): o
    # certificado TLS do site dá "Hostname mismatch" tanto pra www.criptofacil.com
    # quanto pra criptofacil.com (testado com as duas variantes) -- problema do
    # lado do servidor/rede deles, fora do nosso controle. Sem essa fonte o bot
    # segue com as outras 9 normalmente.
}


class NewsSummary(BaseModel):
    summary_pt: str = Field(description="Resumo em português, 1-2 frases.")
    sentiment_score: float = Field(ge=-1.0, le=1.0, description="-1 muito negativo, +1 muito positivo pro mercado cripto.")


def _dedup_hash(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode("utf-8")).hexdigest()


def _is_duplicate(title: str, recent_titles: list[str], threshold: int = 85) -> bool:
    return any(fuzz.token_sort_ratio(title, t) >= threshold for t in recent_titles)


class NewsAgent(BaseAgent):
    name = "news_agent"
    # Achado 25/09/2026 (pedido do Ivan -- créditos da OpenAI zerados): este
    # agente ficava hardcoded na OpenAI mesmo com settings.filter_agent_provider
    # ="deepseek" (opt-in manual, ver config/settings.py e core/llm_client.py),
    # diferente de viability_agent/position_review_agent, que já respeitavam
    # essa flag. Resumo/tradução/sentimento de notícia não é decisão de risco
    # (isso continua com risk_committee_agent, sempre OpenAI, ver comentário lá)
    # -- não há motivo pra manter só este preso à OpenAI. Mesmo padrão dos
    # outros 2 agentes-filtro.
    model = (
        settings.deepseek_model
        if settings.filter_agent_provider == "deepseek"
        else settings.openai_model_cheap
    )

    def fetch_recent_items(self, minutes: int = 15, min_items: int = 10) -> list[dict]:
        import datetime as dt

        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
        items: list[dict] = []
        parsed_feeds: dict[str, object] = {}

        for source, url in RSS_FEEDS.items():
            parsed = _fetch_feed(source, url)
            parsed_feeds[source] = parsed
            if parsed is None:
                continue
            for entry in parsed.entries:
                published = getattr(entry, "published_parsed", None)
                if published:
                    published_dt = dt.datetime(*published[:6], tzinfo=dt.timezone.utc)
                    if published_dt < cutoff:
                        continue
                items.append({"source": source, "title": entry.title, "url": entry.link})

        # Se não juntou o mínimo de notícias recentes (feeds mais devagar
        # nesse ciclo), relaxa a janela pra garantir o mínimo de 10 pedido no MVP.
        # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 6): antes este loop
        # refazia a busca de rede das 10 fontes do ZERO, descartando os `parsed`
        # já obtidos no loop acima -- dobrava as chamadas de rede em todo ciclo
        # com poucas notícias recentes. Agora reaproveita o resultado já buscado
        # (parsed_feeds) e só refaz a busca de rede pra uma fonte que tinha
        # falhado na primeira tentativa.
        if len(items) < min_items:
            for source, url in RSS_FEEDS.items():
                parsed = parsed_feeds.get(source)
                if parsed is None:
                    parsed = _fetch_feed(source, url)
                if parsed is None:
                    continue
                for entry in parsed.entries[:5]:
                    items.append({"source": source, "title": entry.title, "url": entry.link})

        # Deduplicação por similaridade de título
        deduped: list[dict] = []
        seen_titles: list[str] = []
        for item in items:
            if not _is_duplicate(item["title"], seen_titles):
                deduped.append(item)
                seen_titles.append(item["title"])

        # Achado 24/09/2026 (item Fase 5): antes retornava
        # `deduped[:max(min_items, len(deduped))]` -- como max(min_items,
        # len(deduped)) nunca é menor que len(deduped), esse slice nunca
        # cortava nada (no-op). O mínimo já é garantido pela relaxação de
        # janela acima; não há um teto documentado no spec, então devolvemos
        # a lista deduplicada inteira mesmo.
        return deduped

    def summarize_and_score(self, item: dict) -> NewsSummary:
        return call_structured(
            agent_name=self.name,
            model=self.model,
            system_prompt=(
                "Você resume notícias de criptomoedas em português do Brasil, de forma "
                "objetiva e factual, e atribui um score de sentimento de mercado."
            ),
            user_prompt=f"Fonte: {item['source']}\nTítulo: {item['title']}",
            response_model=NewsSummary,
        )

    def run(self) -> list[NewsItem]:
        raw_items = self.fetch_recent_items()
        saved: list[NewsItem] = []

        with get_session() as session:
            for item in raw_items:
                # Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): o
                # commit desta sessão só acontece uma vez, no fim do `with`
                # (ver db/session.get_session) -- sem este try/except, um item
                # que falha no meio do loop (ex: erro de parse do LLM) fazia
                # rollback de TODOS os itens já adicionados nesta mesma
                # chamada de run(), não só do item problemático.
                try:
                    dedup_hash = _dedup_hash(item["title"])
                    if session.query(NewsItem).filter_by(dedup_hash=dedup_hash).first():
                        continue

                    summary = self.summarize_and_score(item)
                    news_item = NewsItem(
                        source=item["source"],
                        title_original=item["title"],
                        summary_pt=summary.summary_pt,
                        sentiment_score=summary.sentiment_score,
                        url=item["url"],
                        dedup_hash=dedup_hash,
                    )
                    session.add(news_item)
                    saved.append(news_item)
                except Exception:
                    logger.warning(
                        "NewsAgent: falha ao resumir/gravar item %r -- pulando, resto do lote segue.",
                        item.get("title"), exc_info=True,
                    )

        return saved

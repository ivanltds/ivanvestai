"""Busca os RSS validados, deduplica, resume+traduz (modelo barato) e
gera score de sentimento. Ver indicadores-estrategias.md seção 1."""
from __future__ import annotations

import hashlib

import feedparser
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from agents.base import BaseAgent
from config.settings import settings
from core.llm_client import call_structured
from db.models import NewsItem
from db.session import get_session

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
    "Criptofácil": "https://www.criptofacil.com/feed/",
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
    model = settings.openai_model_cheap

    def fetch_recent_items(self, minutes: int = 15, min_items: int = 10) -> list[dict]:
        import datetime as dt

        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
        items: list[dict] = []

        for source, url in RSS_FEEDS.items():
            parsed = feedparser.parse(url)
            for entry in parsed.entries:
                published = getattr(entry, "published_parsed", None)
                if published:
                    published_dt = dt.datetime(*published[:6], tzinfo=dt.timezone.utc)
                    if published_dt < cutoff:
                        continue
                items.append({"source": source, "title": entry.title, "url": entry.link})

        # Se não juntou o mínimo de notícias recentes (feeds mais devagar
        # nesse ciclo), relaxa a janela pra garantir o mínimo de 10 pedido no MVP.
        if len(items) < min_items:
            for source, url in RSS_FEEDS.items():
                parsed = feedparser.parse(url)
                for entry in parsed.entries[:5]:
                    items.append({"source": source, "title": entry.title, "url": entry.link})

        # Deduplicação por similaridade de título
        deduped: list[dict] = []
        seen_titles: list[str] = []
        for item in items:
            if not _is_duplicate(item["title"], seen_titles):
                deduped.append(item)
                seen_titles.append(item["title"])

        return deduped[: max(min_items, len(deduped))]

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

        return saved

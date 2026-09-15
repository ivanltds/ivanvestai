import json
import re
import time
import datetime
import email.utils
import urllib.request
import xml.etree.ElementTree as ET
from src.llm.provider import get_llm_provider

class NewsResearcherAgent:
    """
    Agente 1: Pesquisador de Notícias
    Busca as notícias mais recentes (RSS) em múltiplos portais, ordena estritamente
    pelo horário de publicação (mais recentes primeiro) e utiliza IA para:
    - Traduzir a manchete para o português
    - Gerar um resumo executivo de 1-2 frases destacando o impacto no mercado
    - Extrair o sentimento geral e moedas mais citadas
    """
    def __init__(self):
        self.llm = get_llm_provider()
        self.rss_feeds = [
            {"name": "Decrypt", "url": "https://decrypt.co/feed"},
            {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
            {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
        ]

    def _clean_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        clean = re.sub(r'<[^>]+>', ' ', raw_html)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def _parse_pubdate(self, pub_text: str):
        if not pub_text:
            now = datetime.datetime.now(datetime.timezone.utc)
            return now.timestamp(), now.isoformat(), now.strftime("%d/%m %H:%M")
        try:
            dt = email.utils.parsedate_to_datetime(pub_text)
            # Converte para timezone UTC ou BRT para consistência
            ts = dt.timestamp()
            iso = dt.isoformat()
            # Formata no padrão legível brasileiro
            dt_brt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=-3)))
            fmt = dt_brt.strftime("%d/%m às %H:%M")
            return ts, iso, fmt
        except Exception:
            now = datetime.datetime.now(datetime.timezone.utc)
            return now.timestamp(), now.isoformat(), now.strftime("%d/%m %H:%M")

    def fetch_latest_news(self) -> dict:
        """Busca notícias dos feeds RSS, unifica, remove duplicatas e ordena por mais recente."""
        raw_items = []
        seen_urls = set()

        for feed_info in self.rss_feeds:
            feed_name = feed_info["name"]
            url = feed_info["url"]
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as response:
                    xml_data = response.read()

                try:
                    root = ET.fromstring(xml_data)
                    items = root.findall('./channel/item')
                except ET.ParseError:
                    import re
                    items_raw = re.findall(r'<item>(.*?)</item>', xml_data.decode('utf-8', errors='ignore'), re.DOTALL)
                    class FakeItem:
                        def __init__(self, raw):
                            self._raw = raw
                        def find(self, tag):
                            tag_name = tag.split('}')[-1] if '}' in tag else tag
                            m = re.search(rf'<{tag_name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag_name}>', self._raw, re.DOTALL)
                            return type('E', (), {'text': m.group(1).strip() if m else ''})() if m else None
                    items = [FakeItem(r) for r in items_raw[:15]]

                for item in items[:10]:
                    title_el = item.find('title')
                    desc_el = item.find('description')
                    link_el = item.find('link')
                    pub_el = item.find('pubDate')

                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    pub_text = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

                    if not title or not link:
                        continue

                    # Normalização básica de link para desduplicar
                    norm_link = link.split('?')[0].rstrip('/')
                    if norm_link in seen_urls:
                        continue
                    seen_urls.add(norm_link)

                    ts, iso, formatted_date = self._parse_pubdate(pub_text)
                    clean_desc = self._clean_html(desc)[:200]

                    raw_items.append({
                        "title": title,
                        "description": clean_desc,
                        "url": link,
                        "source": feed_name,
                        "pub_date_raw": pub_text,
                        "published_at": iso,
                        "published_at_str": formatted_date,
                        "timestamp": ts,
                    })
            except Exception as e:
                print(f"[Agente 1] Aviso: Falha ao ler feed {feed_name} ({url}): {e}")

        # Ordena ESTRITAMENTE pela data/hora de publicação (mais recente primeiro)
        raw_items.sort(key=lambda x: x["timestamp"], reverse=True)

        # Seleciona as 10 notícias mais recentes
        top_items = raw_items[:10]

        # Monta o texto puro para análise do LLM
        prompt_lines = []
        for idx, it in enumerate(top_items):
            prompt_lines.append(
                f"[ID {idx}] ({it['source']} - {it['published_at_str']})\n"
                f"Título: {it['title']}\n"
                f"Descrição: {it['description']}\n"
            )

        return {
            "items": top_items,
            "text": "\n".join(prompt_lines)[:25000] # Limite de segurança de contexto
        }

    def analyze_news(self) -> dict:
        """Usa a IA para traduzir, resumir e gerar o panorama macro das notícias."""
        print("[Agente 1] Coletando notícias globais de cripto dos feeds RSS...")
        news_data = self.fetch_latest_news()
        items = news_data["items"]
        news_text = news_data["text"]

        if not items:
            print("[Agente 1] Alerta: Nenhuma notícia capturada. Retornando sentimento neutro.")
            return {
                "market_sentiment": "neutral",
                "top_coins": [],
                "summary": "Sem dados recentes de notícias.",
                "sources": []
            }

        system_prompt = """
        Você é o Analista Chefe de Inteligência de Mercado do Fundo Quantitativo IvanvestAI.
        Sua missão é analisar as notícias recentes de criptoativos e entregar um relatório estruturado em JSON com:
        1. "market_sentiment": "bullish" | "bearish" | "neutral"
        2. "summary": Um resumo executivo de 2 linhas do cenário macro e liquidez atual em português.
        3. "top_coins": Lista de até 3 moedas com maior destaque (símbolo, "bullish"|"bearish", "reason").
        4. "analyzed_news": Para CADA notícia fornecida (identificada por [ID X]), retorne:
           - "id": número do ID correspondente
           - "title_pt": título traduzido de forma fluida e jornalística para o português do Brasil (sem jargões estranhos)
           - "summary_pt": resumo curto de 1 a 2 frases explicando o fato essencial e seu impacto prático no mercado cripto.
        
        Responda ESTRITAMENTE em formato JSON compatível:
        {
            "market_sentiment": "bullish",
            "summary": "...",
            "top_coins": [{"coin": "BTC", "sentiment": "bullish", "reason": "..."}],
            "analyzed_news": [
                {"id": 0, "title_pt": "...", "summary_pt": "..."}
            ]
        }
        """

        print(f"[Agente 1] Enviando {len(items)} notícias para o agente traduzir, resumir e avaliar sentimento...")
        try:
            response_text = self.llm.generate_response(
                system_prompt=system_prompt,
                user_prompt=f"NOTÍCIAS RECENTES ORDENADAS POR HORÁRIO:\n\n{news_text}",
                expect_json=True
            )
            data = json.loads(response_text)
        except Exception as e:
            print(f"[Agente 1] Erro na análise da IA: {e}. Aplicando fallback direto.")
            data = {
                "market_sentiment": "neutral",
                "top_coins": [],
                "summary": "Análise temporariamente simplificada por contingência.",
                "analyzed_news": []
            }

        # Mapeia as traduções e resumos da IA de volta para cada notícia
        analyzed_map = {}
        for an in data.get("analyzed_news", []):
            if isinstance(an, dict) and "id" in an:
                analyzed_map[an["id"]] = an

        enriched_sources = []
        for idx, it in enumerate(items):
            analysis = analyzed_map.get(idx, {})
            title_pt = analysis.get("title_pt") or it["title"]
            summary_pt = analysis.get("summary_pt") or it["description"][:160]

            enriched_sources.append({
                "title": it["title"],
                "title_pt": title_pt,
                "summary_pt": summary_pt,
                "url": it["url"],
                "source": it["source"],
                "published_at": it["published_at"],
                "published_at_str": it["published_at_str"],
                "timestamp": it["timestamp"]
            })

        data["sources"] = enriched_sources
        print(f"[Agente 1] Concluído! {len(enriched_sources)} notícias traduzidas e resumidas com sucesso.")
        return data

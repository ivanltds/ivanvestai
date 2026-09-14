import json
import urllib.request
import xml.etree.ElementTree as ET
from src.llm.provider import get_llm_provider

class NewsResearcherAgent:
    """
    Agente 1: Pesquisador de Notícias
    Busca as notícias mais recentes (RSS) e usa a IA para extrair os sentimentos
    gerais do mercado e as moedas mais citadas. Agora também arquiva os links originais.
    """
    def __init__(self):
        self.llm = get_llm_provider()
        # RSS Feeds combinados
        self.rss_urls = [
            "https://cryptopanic.com/news/rss/", # Agregador Geral (Mais rápido)
            "https://cointelegraph.com/rss",     # Focado em Análises
            "https://www.coindesk.com/arc/outboundfeeds/rss/" # Mercado Tradicional Cripto
        ]

    def fetch_latest_news(self) -> dict:
        """Busca as últimas notícias dos feeds RSS e retorna o texto puro e a lista de links."""
        news_items = []
        links_list = []
        
        for url in self.rss_urls:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    xml_data = response.read()
                
                # Tenta parsear como XML primeiro; se falhar, tenta recuperar os items com regex
                try:
                    root = ET.fromstring(xml_data)
                    items = root.findall('./channel/item')
                except ET.ParseError:
                    # Fallback: extrai apenas os títulos/links com regex quando o XML estiver malformado
                    import re
                    items_raw = re.findall(r'<item>(.*?)</item>', xml_data.decode('utf-8', errors='ignore'), re.DOTALL)
                    class FakeItem:
                        def __init__(self, raw):
                            self._raw = raw
                        def find(self, tag):
                            tag_name = tag.split('}')[-1] if '}' in tag else tag
                            m = re.search(rf'<{tag_name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag_name}>', self._raw, re.DOTALL)
                            return type('E', (), {'text': m.group(1).strip() if m else ''})() if m else None
                    items = [FakeItem(r) for r in items_raw[:10]]
                
                # Pega as 10 notícias mais recentes de CADA feed
                for item in items[:10]:
                    title_el = item.find('title')
                    desc_el = item.find('description')
                    link_el = item.find('link')
                    title = title_el.text if title_el is not None else ""
                    desc = desc_el.text if desc_el is not None else ""
                    link = link_el.text if link_el is not None else ""
                    
                    if title:
                        news_items.append(f"Título: {title}\nResumo: {desc}\n")
                        if link:
                            links_list.append({"title": title, "url": link})
            except Exception as e:
                print(f"[Agente 1] Aviso: Falha ao ler feed {url}: {e}")

        if not news_items:
            return {"text": "Falha geral ao buscar notícias em todas as fontes.", "links": []}

        return {
            "text": "\n".join(news_items),
            "links": links_list
        }

    def analyze_news(self) -> dict:
        """Usa a IA para ler as notícias e gerar um panorama (JSON)."""
        print("[Agente 1] Coletando notícias globais de cripto...")
        news_data = self.fetch_latest_news()
        news_text = news_data["text"]
        news_links = news_data["links"]
        
        if "Falha" in news_text:
            print("[Agente 1] Alerta: Não foi possível ler as notícias. Retornando sentimento neutro.")
            return {"market_sentiment": "neutral", "top_coins": [], "summary": "Sem dados de notícias.", "sources": []}

        system_prompt = """
        Você é um analista chefe de um fundo de hedge de criptomoedas.
        Analise as manchetes fornecidas e retorne um JSON extritamente neste formato:
        {
            "market_sentiment": "bullish" | "bearish" | "neutral",
            "summary": "Resumo de 2 linhas do cenário atual",
            "top_coins": [
                {"coin": "nome ou símbolo da moeda", "sentiment": "bullish" | "bearish", "reason": "motivo em 1 frase"}
            ]
        }
        A lista 'top_coins' deve conter no máximo as 3 moedas mais relevantes mencionadas nas notícias, incluindo memecoins se citadas.
        """
        
        print("[Agente 1] Enviando para a IA analisar o sentimento...")
        response_text = self.llm.generate_response(
            system_prompt=system_prompt,
            user_prompt=f"MANCHETES RECENTES:\n{news_text}",
            expect_json=True
        )
        
        try:
            data = json.loads(response_text)
            data["sources"] = news_links
            return data
        except json.JSONDecodeError:
            print("[Agente 1] Erro: IA não retornou um JSON válido.")
            return {"market_sentiment": "neutral", "top_coins": [], "summary": "Erro na leitura da IA.", "sources": []}

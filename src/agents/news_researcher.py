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
        # RSS Feed gratuito do Cointelegraph
        self.rss_url = "https://cointelegraph.com/rss"

    def fetch_latest_news(self) -> dict:
        """Busca as últimas notícias do feed RSS e retorna o texto puro e a lista de links."""
        try:
            req = urllib.request.Request(self.rss_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
            
            root = ET.fromstring(xml_data)
            news_items = []
            links_list = []
            
            # Pega as 15 notícias mais recentes
            for item in root.findall('./channel/item')[:15]:
                title = item.find('title').text if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                
                news_items.append(f"Título: {title}\nResumo: {desc}\n")
                if link:
                    links_list.append({"title": title, "url": link})
            
            return {
                "text": "\n".join(news_items),
                "links": links_list
            }
        except Exception as e:
            return {"text": f"Falha ao buscar notícias: {str(e)}", "links": []}

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

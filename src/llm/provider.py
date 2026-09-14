import os
from abc import ABC, abstractmethod
import json

class LLMProvider(ABC):
    """Interface base para todos os provedores de IA. Permite trocar a IA facilmente."""
    @abstractmethod
    def generate_response(self, system_prompt: str, user_prompt: str, expect_json: bool = False) -> str:
        pass

class OpenAIProvider(LLMProvider):
    def __init__(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Instale a biblioteca 'openai' primeiro: pip install openai")
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY não configurada no .env")
        
        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini") # Usamos modelo mais barato para rodar 24x/dia

    def generate_response(self, system_prompt: str, user_prompt: str, expect_json: bool = False) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2, # Baixa criatividade = alta precisão para trades
        }
        
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

def get_llm_provider() -> LLMProvider:
    """Fábrica que decide qual IA instanciar com base no .env"""
    provider_name = os.getenv("LLM_PROVIDER", "openai").lower()
    
    if provider_name == "openai":
        return OpenAIProvider()
    # No futuro: elif provider_name == "gemini": return GeminiProvider()
    
    raise ValueError(f"Provedor de LLM não suportado: {provider_name}")

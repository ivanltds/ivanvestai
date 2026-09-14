import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Carrega arquivo .env da raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)


class Settings:
    """Configurações da aplicação e parâmetros de execução do DCA."""

    EXCHANGE_ID: str = os.getenv("EXCHANGE_ID", "binance").lower()
    API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")

    DCA_SYMBOL: str = os.getenv("DCA_SYMBOL", "BTC/BRL")
    DCA_AMOUNT_FIAT: float = float(os.getenv("DCA_AMOUNT_FIAT", "50.00"))
    DCA_CURRENCY: str = os.getenv("DCA_CURRENCY", "BRL")

    # Por padrão de segurança, DRY_RUN inicia como True
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").strip().lower() in ("true", "1", "yes")

    LOG_DIR: str = os.getenv("LOG_DIR", "logs")

    @classmethod
    def validate(cls) -> None:
        """Valida se as credenciais obrigatórias estão presentes quando não for simulação."""
        if not cls.DRY_RUN and (not cls.API_KEY or not cls.SECRET_KEY):
            raise ValueError(
                "Credenciais da Binance (BINANCE_API_KEY e BINANCE_SECRET_KEY) "
                "são obrigatórias para execução real (DRY_RUN=false)."
            )


settings = Settings()

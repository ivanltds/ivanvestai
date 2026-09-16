"""Cria as tabelas no Postgres a partir dos models (MVP — sem Alembic ainda).

Uso: python -m db.init_db
Quando o schema estabilizar, migrar para Alembic para versionar mudanças
sem perder dados em produção.
"""
from db.models import Base
from db.session import engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas/atualizadas com sucesso.")


if __name__ == "__main__":
    main()
